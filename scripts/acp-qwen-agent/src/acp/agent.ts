import * as acp from "@agentclientprotocol/sdk";
import type { AppConfig } from "../config.js";
import { logError, logInfo } from "../logger.js";
import type { QwenChatClient } from "../qwen/client.js";
import { SessionStore } from "./session.js";
import { AuditLog } from "../agent/audit.js";
import { runAgentLoop } from "../agent/loop.js";

const PACKAGE_VERSION = "0.1.0";

export type QwenAcpAgentDeps = {
  config: AppConfig;
  qwen: QwenChatClient;
  sessions?: SessionStore;
  audit?: AuditLog;
};

export function extractUserText(
  prompt: Array<{ type: string; text?: string }>,
): string {
  const parts: string[] = [];
  for (const block of prompt) {
    if (block.type === "text" && typeof block.text === "string") {
      parts.push(block.text);
    }
  }
  return parts.join("\n").trim();
}

export function createQwenAcpAgent(deps: QwenAcpAgentDeps) {
  const sessions = deps.sessions ?? new SessionStore();
  const audit = deps.audit ?? new AuditLog();

  async function initialize(
    params: acp.InitializeRequest,
  ): Promise<acp.InitializeResponse> {
    logInfo("acp initialize", {
      clientProtocol: params.protocolVersion,
      agentProtocol: acp.PROTOCOL_VERSION,
    });
    return {
      protocolVersion: acp.PROTOCOL_VERSION,
      agentCapabilities: {
        loadSession: false,
        promptCapabilities: {
          image: false,
          audio: false,
          embeddedContext: false,
        },
      },
      agentInfo: {
        name: "qwen-acp-agent",
        version: PACKAGE_VERSION,
      },
      authMethods: [],
    };
  }

  async function newSession(
    _params: acp.NewSessionRequest,
  ): Promise<acp.NewSessionResponse> {
    const sessionId = sessions.create();
    logInfo("acp session/new", { sessionId });
    return { sessionId };
  }

  async function authenticate(
    _params: acp.AuthenticateRequest,
  ): Promise<acp.AuthenticateResponse | void> {
    return {};
  }

  async function setSessionMode(
    _params: acp.SetSessionModeRequest,
  ): Promise<acp.SetSessionModeResponse> {
    return {};
  }

  async function prompt(
    params: acp.PromptRequest,
    client: acp.AgentContext,
  ): Promise<acp.PromptResponse> {
    const session = sessions.get(params.sessionId);
    if (!session) {
      throw new Error(`Session ${params.sessionId} not found`);
    }

    session.pendingPrompt?.abort();
    session.pendingPrompt = new AbortController();
    const signal = session.pendingPrompt.signal;

    try {
      const userText = extractUserText(params.prompt);
      if (!userText) {
        await client.notify(acp.methods.client.session.update, {
          sessionId: params.sessionId,
          update: {
            sessionUpdate: "agent_message_chunk",
            content: {
              type: "text",
              text: "I did not receive any text in your prompt.",
            },
          },
        });
        return { stopReason: "end_turn" };
      }

      logInfo("acp session/prompt", {
        sessionId: params.sessionId,
        userChars: userText.length,
        workspace: deps.config.workspace ?? "",
      });

      const stop = await runAgentLoop(
        {
          config: deps.config,
          qwen: deps.qwen,
          audit,
          workspaceRoot: deps.config.workspace ?? "",
          sessionId: params.sessionId,
          client,
          signal,
          allowWrites: deps.config.allowWrites,
        },
        userText,
      );

      if (stop === "cancelled") return { stopReason: "cancelled" };
      if (stop === "max_turn_requests") {
        return { stopReason: "max_turn_requests" };
      }
      return { stopReason: "end_turn" };
    } catch (err) {
      if (signal.aborted) {
        return { stopReason: "cancelled" };
      }
      const message = err instanceof Error ? err.message : String(err);
      logError("acp prompt failed", { message, sessionId: params.sessionId });
      await client.notify(acp.methods.client.session.update, {
        sessionId: params.sessionId,
        update: {
          sessionUpdate: "agent_message_chunk",
          content: {
            type: "text",
            text: `**Error:** ${message}`,
          },
        },
      });
      return { stopReason: "end_turn" };
    } finally {
      session.pendingPrompt = null;
    }
  }

  async function cancel(params: acp.CancelNotification): Promise<void> {
    logInfo("acp session/cancel", { sessionId: params.sessionId });
    sessions.cancel(params.sessionId);
  }

  function buildApp(): acp.AgentApp {
    return acp
      .agent({ name: "qwen-acp-agent" })
      .onRequest(acp.methods.agent.initialize, (ctx) => initialize(ctx.params))
      .onRequest(acp.methods.agent.session.new, (ctx) => newSession(ctx.params))
      .onRequest(acp.methods.agent.authenticate, (ctx) =>
        authenticate(ctx.params),
      )
      .onRequest(acp.methods.agent.session.setMode, (ctx) =>
        setSessionMode(ctx.params),
      )
      .onRequest(acp.methods.agent.session.prompt, (ctx) =>
        prompt(ctx.params, ctx.client),
      )
      .onNotification(acp.methods.agent.session.cancel, (ctx) =>
        cancel(ctx.params),
      );
  }

  return {
    initialize,
    newSession,
    authenticate,
    setSessionMode,
    prompt,
    cancel,
    buildApp,
    sessions,
    audit,
  };
}

export async function runAcpStdio(deps: QwenAcpAgentDeps): Promise<void> {
  const { Readable, Writable } = await import("node:stream");
  const input = Writable.toWeb(process.stdout);
  const output = Readable.toWeb(process.stdin) as ReadableStream<Uint8Array>;
  const stream = acp.ndJsonStream(input, output);
  const agent = createQwenAcpAgent(deps);
  const connection = agent.buildApp().connect(stream);
  logInfo("acp stdio connected", {
    baseUrl: deps.config.baseUrl,
    model: deps.config.model,
    workspace: deps.config.workspace ?? "",
  });
  await connection.closed;
}
