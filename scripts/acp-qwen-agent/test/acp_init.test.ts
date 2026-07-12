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

  it("in-process initialize → session/new → prompt returns model text", async () => {
    const chunks: string[] = [];
    const qwen: QwenChatClient = {
      completeChat: vi.fn(async () => "pong from mock qwen"),
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
        const init = await agentCx.request(acp.methods.agent.initialize, {
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
        return { init, session, prompt };
      });

    expect(result.init.protocolVersion).toBe(acp.PROTOCOL_VERSION);
    expect(result.session.sessionId).toMatch(/^[0-9a-f]{32}$/);
    expect(result.prompt.stopReason).toBe("end_turn");
    expect(chunks.join("")).toBe("pong from mock qwen");
    expect(qwen.completeChat).toHaveBeenCalledOnce();
  });
});
