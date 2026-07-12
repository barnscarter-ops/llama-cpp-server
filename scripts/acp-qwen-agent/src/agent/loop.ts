import type { AgentContext } from "@agentclientprotocol/sdk";
import * as acp from "@agentclientprotocol/sdk";
import type { AppConfig } from "../config.js";
import type {
  ChatMessage,
  QwenChatClient,
  ToolCallRequest,
} from "../qwen/client.js";
import { executeTool, getOpenAiToolSpecs } from "../tools/registry.js";
import { AuditLog } from "./audit.js";
import { logError, logInfo } from "../logger.js";

export const MAX_TURNS = 6;

const SYSTEM_PROMPT =
  "You are qwen-acp-agent, a helpful local coding assistant with read-only workspace tools. " +
  "Use tools when you need file contents or search results. " +
  "Only one tool call at a time. Prefer list_files/read_file/search_text. " +
  "You cannot write files in this version. Keep final answers concise Markdown.";

export type LoopDeps = {
  config: AppConfig;
  qwen: QwenChatClient;
  audit: AuditLog;
  workspaceRoot: string;
  sessionId: string;
  client: AgentContext;
  signal: AbortSignal;
};

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

/**
 * Bounded model↔tool loop. Max 6 turns. At most one tool call per model response.
 */
export async function runAgentLoop(
  deps: LoopDeps,
  userText: string,
): Promise<"end_turn" | "cancelled" | "max_turn_requests"> {
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
      const toolStarted = Date.now();
      let toolResult;
      if (args === null) {
        toolResult = {
          ok: false,
          output: "invalid tool arguments JSON",
        };
      } else {
        toolResult = await executeTool(toolCall.name, args, {
          workspaceRoot: deps.workspaceRoot,
        });
      }

      deps.audit.record({
        kind: "tool",
        ok: toolResult.ok,
        tool: toolCall.name,
        durationMs: Date.now() - toolStarted,
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
