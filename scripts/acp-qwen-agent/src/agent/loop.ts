import type { AgentContext } from "@agentclientprotocol/sdk";
import * as acp from "@agentclientprotocol/sdk";
import type { AppConfig } from "../config.js";
import type {
  ChatMessage,
  QwenChatClient,
  ToolCallRequest,
} from "../qwen/client.js";
import { executeTool, getOpenAiToolSpecs } from "../tools/registry.js";
import { computeDiffHash, writeApprovalStore } from "../tools/apply_patch.js";
import { AuditLog } from "./audit.js";
import { logError, logInfo } from "../logger.js";
import type { ToolResult } from "../tools/types.js";

export const MAX_TURNS = 6;

const SYSTEM_PROMPT =
  "You are qwen-acp-agent, a helpful local coding assistant with read-only workspace tools. " +
  "Use tools when you need file contents or search results. " +
  "Only one tool call at a time. Prefer list_files/read_file/search_text/propose_patch. " +
  "Write files only via propose_patch (diff only). " +
  "Keep final answers concise Markdown.";

export type LoopDeps = {
  config: AppConfig;
  qwen: QwenChatClient;
  audit: AuditLog;
  workspaceRoot: string;
  sessionId: string;
  client: AgentContext;
  signal: AbortSignal;
  allowWrites?: boolean;
  timeoutMs?: number;
};

export type AgentStopReason = "end_turn" | "cancelled" | "max_turn_requests";

async function emitText(
  client: AgentContext,
  sessionId: string,
  text: string,
): Promise<void> {
  await client.notify(acp.methods.client.session.update, {
    sessionId,
    update: {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text },
    },
  });
}

async function emitToolCall(
  client: AgentContext,
  sessionId: string,
  call: ToolCallRequest,
  status: "pending" | "completed" | "failed",
  output?: string,
): Promise<void> {
  if (status === "pending") {
    await client.notify(acp.methods.client.session.update, {
      sessionId,
      update: {
        sessionUpdate: "tool_call",
        toolCallId: call.id,
        title: call.name,
        kind: "read",
        status: "pending",
        rawInput: safeJson(call.arguments),
      },
    });
    return;
  }
  await client.notify(acp.methods.client.session.update, {
    sessionId,
    update: {
      sessionUpdate: "tool_call_update",
      toolCallId: call.id,
      status: status === "completed" ? "completed" : "failed",
      rawOutput: { output: output ?? "" },
      content: output
        ? [
            {
              type: "content",
              content: { type: "text", text: output },
            },
          ]
        : undefined,
    },
  });
}

function safeJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return { _raw: raw };
  }
}

function parseToolArgs(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function emitStructuredError(
  client: AgentContext,
  sessionId: string,
  toolName: string,
  message: string,
): Promise<void> {
  return client.notify(acp.methods.client.session.update, {
    sessionId,
    update: {
      sessionUpdate: "agent_message_chunk",
      content: { type: "text", text: `**Tool error (${toolName}):** ${message}` },
    },
  });
}

/**
 * Handle apply_patch with dual gate (write gate + diff hash approval).
 * If ACP_ALLOW_WRITES is off, reject immediately.
 * If on, request permission from the client via session/request_permission.
 */
async function handleApplyPatch(
  deps: LoopDeps,
  toolCall: ToolCallRequest,
  args: unknown,
  toolStarted: number,
): Promise<ToolResult> {
  // Gate 1: ACP_ALLOW_WRITES
  if (!deps.allowWrites) {
    return { ok: false, output: "apply_patch is disabled. Set ACP_ALLOW_WRITES=true to enable writes." };
  }

  let parsed: { path?: string; newContent?: string } | null = null;
  try {
    if (typeof args === "string") {
      parsed = JSON.parse(args);
    } else if (typeof args === "object") {
      parsed = args as { path?: string; newContent?: string };
    }
  } catch {
    // will reject below
  }

  if (!parsed || typeof parsed.path !== "string") {
    return { ok: false, output: "apply_patch requires 'path' and 'newContent' fields" };
  }

  const newContent = parsed.newContent ?? "";

  // Gate 2: request permission from client
  const diffHash = computeDiffHash(parsed.path, newContent);
  logInfo("apply_patch permission request", {
    sessionId: deps.sessionId,
    diffHash: diffHash.slice(0, 16),
  });

  try {
    const permission = await deps.client.request(
      acp.methods.client.session.requestPermission,
      {
        sessionId: deps.sessionId,
        toolCall: {
          title: `apply_patch: ${parsed.path}`,
          kind: "write",
          status: "pending",
          toolCallId: toolCall.id,
          content: [
            {
              type: "text",
              text: `Apply write to ${parsed.path} (hash: ${diffHash.slice(0, 16)}...)`,
            },
          ],
        },
        options: [
          { kind: "allow_once", name: "Allow this write", optionId: "allow" },
        ],
      },
    ) as { outcome?: { outcome?: string; optionId?: string } };

    if (
      permission?.outcome?.outcome !== "selected" ||
      permission?.outcome?.optionId !== "allow"
    ) {
      return { ok: false, output: `apply_patch rejected by editor (hash: ${diffHash.slice(0, 16)}...)` };
    }

    // Permission granted — record approval then execute the write.
    writeApprovalStore.set(parsed.path, newContent, true);
    try {
      return await executeTool(toolCall.name, args, { workspaceRoot: deps.workspaceRoot });
    } finally {
      writeApprovalStore.clear(parsed.path, newContent);
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (deps.signal.aborted) throw new Error(`apply_patch aborted: ${message}`);
    return { ok: false, output: `apply_patch permission error: ${message}` };
  }
}

/**
 * Bounded model↔tool loop. Max 6 turns. At most one tool call per model response.
 */
export async function runAgentLoop(
  deps: LoopDeps,
  userText: string,
): Promise<AgentStopReason> {
  if (!deps.workspaceRoot) {
    await emitText(
      deps.client,
      deps.sessionId,
      "ACP_WORKSPACE is not set. Configure an absolute workspace path to use tools; answering without tools.",
    );
  }

  const messages: ChatMessage[] = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: userText },
  ];

  const tools = deps.workspaceRoot ? getOpenAiToolSpecs() : undefined;

  for (let turn = 1; turn <= MAX_TURNS; turn += 1) {
    if (deps.signal.aborted) return "cancelled";

    const started = Date.now();
    let result;
    try {
      result = await deps.qwen.completeChat({
        messages,
        tools,
        signal: deps.signal,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      deps.audit.record({
        kind: "model",
        ok: false,
        durationMs: Date.now() - started,
        detail: "error",
        sessionId: deps.sessionId,
      });
      logError("agent loop model error", { message, turn });
      await emitText(
        deps.client,
        deps.sessionId,
        `**Error talking to local Qwen:** ${message}`,
      );
      return "end_turn";
    }

    deps.audit.record({
      kind: "model",
      ok: true,
      durationMs: Date.now() - started,
      detail: `turn=${turn};tools=${result.toolCalls.length}`,
      sessionId: deps.sessionId,
    });

    if (deps.signal.aborted) return "cancelled";

    // Enforce one tool at a time.
    const toolCall = result.toolCalls[0];
    if (toolCall) {
      if (!deps.workspaceRoot) {
        await emitText(
          deps.client,
          deps.sessionId,
          "Tool call requested but ACP_WORKSPACE is not configured.",
        );
        return "end_turn";
      }

      await emitToolCall(deps.client, deps.sessionId, toolCall, "pending");
      const args = parseToolArgs(toolCall.arguments);
      let toolResult;

      if (args === null) {
        await emitStructuredError(
          deps.client,
          deps.sessionId,
          toolCall.name,
          "invalid tool arguments JSON",
        );
        toolResult = { ok: false, output: "invalid tool arguments JSON" };
      } else if (toolCall.name === "apply_patch") {
        toolResult = await handleApplyPatch(deps, toolCall, args, Date.now());
      } else {
        toolResult = await executeTool(toolCall.name, args, {
          workspaceRoot: deps.workspaceRoot,
        });
      }

      deps.audit.record({
        kind: "tool",
        ok: toolResult.ok,
        tool: toolCall.name,
        durationMs: 0,
        detail: toolResult.ok ? "ok" : "err",
        sessionId: deps.sessionId,
      });

      await emitToolCall(
        deps.client,
        deps.sessionId,
        toolCall,
        toolResult.ok ? "completed" : "failed",
        toolResult.output,
      );

      messages.push({
        role: "assistant",
        content: result.content ?? "",
        tool_calls: [
          {
            id: toolCall.id,
            type: "function",
            function: {
              name: toolCall.name,
              arguments: toolCall.arguments,
            },
          },
        ],
      } as ChatMessage);

      messages.push({
        role: "tool",
        tool_call_id: toolCall.id,
        content: toolResult.output,
      });

      logInfo("agent tool turn", {
        turn,
        tool: toolCall.name,
        ok: toolResult.ok,
      });
      continue;
    }

    if (result.content) {
      await emitText(deps.client, deps.sessionId, result.content);
    }
    return "end_turn";
  }

  await emitText(
    deps.client,
    deps.sessionId,
    "Stopped after the maximum number of tool turns (6).",
  );
  return "max_turn_requests";
}
