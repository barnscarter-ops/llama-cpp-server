import { describe, expect, it, vi } from "vitest";
import * as acp from "@agentclientprotocol/sdk";
import { createQwenAcpAgent, extractUserText } from "../src/acp/agent.js";
import type { AppConfig } from "../src/config.js";
import type { QwenChatClient } from "../src/qwen/client.js";

const config: AppConfig = {
  baseUrl: "http://127.0.0.1:8080/v1",
  model: "qwen3.6-35b",
  timeoutMs: 5_000,
  allowWrites: false,
  workspace: undefined,
};

describe("extractUserText", () => {
  it("joins text blocks and ignores others", () => {
    expect(
      extractUserText([
        { type: "text", text: "hello" },
        { type: "image" },
        { type: "text", text: "world" },
      ]),
    ).toBe("hello\nworld");
  });
});

describe("createQwenAcpAgent", () => {
  it("initialize advertises protocol + agentInfo", async () => {
    const qwen: QwenChatClient = {
      completeChat: vi.fn(),
    };
    const agent = createQwenAcpAgent({ config, qwen });
    const res = await agent.initialize({
      protocolVersion: acp.PROTOCOL_VERSION,
      clientCapabilities: {},
    });
    expect(res.protocolVersion).toBe(acp.PROTOCOL_VERSION);
    expect(res.agentCapabilities?.loadSession).toBe(false);
    expect(res.agentInfo?.name).toBe("qwen-acp-agent");
    expect(res.agentInfo?.version).toBe("0.1.0");
  });

  it("newSession returns a hex session id", async () => {
    const qwen: QwenChatClient = { completeChat: vi.fn() };
    const agent = createQwenAcpAgent({ config, qwen });
    const res = await agent.newSession({
      cwd: "C:\\Temp",
      mcpServers: [],
    });
    expect(res.sessionId).toMatch(/^[0-9a-f]{32}$/);
    expect(agent.sessions.get(res.sessionId)).toBeDefined();
  });

  it("in-process prompt returns model text without tools", async () => {
    const chunks: string[] = [];
    const qwen: QwenChatClient = {
      completeChat: vi.fn(async () => ({
        content: "pong from mock qwen",
        toolCalls: [],
      })),
    };
    const agentApp = createQwenAcpAgent({ config, qwen }).buildApp();

    const result = await acp
      .client({ name: "test-client" })
      .onNotification(acp.methods.client.session.update, (ctx) => {
        const update = ctx.params.update;
        if (
          update.sessionUpdate === "agent_message_chunk" &&
          update.content.type === "text"
        ) {
          chunks.push(update.content.text);
        }
      })
      .connectWith(agentApp, async (agentCx) => {
        await agentCx.request(acp.methods.agent.initialize, {
          protocolVersion: acp.PROTOCOL_VERSION,
          clientCapabilities: {},
        });
        const session = await agentCx.request(acp.methods.agent.session.new, {
          cwd: "C:\\Temp\\acp-test",
          mcpServers: [],
        });
        const prompt = await agentCx.request(acp.methods.agent.session.prompt, {
          sessionId: session.sessionId,
          prompt: [{ type: "text", text: "ping" }],
        });
        return { session, prompt };
      });

    expect(result.prompt.stopReason).toBe("end_turn");
    expect(chunks.join("")).toBe("ACP_WORKSPACE is not set. Configure an absolute workspace path to use tools; answering without tools.pong from mock qwen");
    expect(qwen.completeChat).toHaveBeenCalledOnce();
  });

  it("runs one tool call then final answer when workspace set", async () => {
    const chunks: string[] = [];
    const toolEvents: string[] = [];
    let turn = 0;
    const qwen: QwenChatClient = {
      completeChat: vi.fn(async () => {
        turn += 1;
        if (turn === 1) {
          return {
            content: null,
            toolCalls: [
              {
                id: "call_list",
                name: "list_files",
                arguments: JSON.stringify({ path: ".", maxDepth: 1 }),
              },
            ],
          };
        }
        return { content: "listed files", toolCalls: [] };
      }),
    };

    // Use a real temp dir via config.workspace
    const fs = await import("node:fs");
    const os = await import("node:os");
    const path = await import("node:path");
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "acp-loop-"));
    fs.writeFileSync(path.join(root, "a.txt"), "x", "utf8");

    try {
      const agentApp = createQwenAcpAgent({
        config: { ...config, workspace: root },
        qwen,
      }).buildApp();

      const result = await acp
        .client({ name: "test-client" })
        .onNotification(acp.methods.client.session.update, (ctx) => {
          const update = ctx.params.update;
          if (
            update.sessionUpdate === "agent_message_chunk" &&
            update.content.type === "text"
          ) {
            chunks.push(update.content.text);
          }
          if (update.sessionUpdate === "tool_call") {
            toolEvents.push(`call:${update.toolCallId}`);
          }
          if (update.sessionUpdate === "tool_call_update") {
            toolEvents.push(`upd:${update.status}`);
          }
        })
        .connectWith(agentApp, async (agentCx) => {
          await agentCx.request(acp.methods.agent.initialize, {
            protocolVersion: acp.PROTOCOL_VERSION,
            clientCapabilities: {},
          });
          const session = await agentCx.request(acp.methods.agent.session.new, {
            cwd: root,
            mcpServers: [],
          });
          return agentCx.request(acp.methods.agent.session.prompt, {
            sessionId: session.sessionId,
            prompt: [{ type: "text", text: "list files" }],
          });
        });

      expect(result.stopReason).toBe("end_turn");
      expect(chunks.join("")).toBe("listed files");
      expect(toolEvents.some((e) => e.startsWith("call:"))).toBe(true);
      expect(toolEvents).toContain("upd:completed");
      expect(qwen.completeChat).toHaveBeenCalledTimes(2);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
