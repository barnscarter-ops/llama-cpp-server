import { describe, expect, it, vi } from "vitest";
import type OpenAI from "openai";
import { createQwenChatClient } from "../src/qwen/client.js";
import type { AppConfig } from "../src/config.js";

const baseConfig: AppConfig = {
  baseUrl: "http://127.0.0.1:8080/v1",
  model: "qwen3.6-35b",
  timeoutMs: 5_000,
  allowWrites: false,
};

function fakeOpenAi(
  impl: () => Promise<{ choices: Array<{ message: { content: string | null } }> }>,
): OpenAI {
  return {
    chat: {
      completions: {
        create: vi.fn(impl),
      },
    },
  } as unknown as OpenAI;
}

describe("createQwenChatClient", () => {
  it("returns assistant text from a non-streaming completion", async () => {
    const openai = fakeOpenAi(async () => ({
      choices: [{ message: { content: "hello from qwen" } }],
    }));
    const client = createQwenChatClient(baseConfig, openai);
    const text = await client.completeChat({
      messages: [{ role: "user", content: "hi" }],
    });
    expect(text).toBe("hello from qwen");
  });

  it("maps empty content to an error", async () => {
    const openai = fakeOpenAi(async () => ({
      choices: [{ message: { content: "   " } }],
    }));
    const client = createQwenChatClient(baseConfig, openai);
    await expect(
      client.completeChat({ messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow(/empty content/i);
  });

  it("maps connection failures with guardian context", async () => {
    const err = Object.assign(new Error("fetch failed"), { code: "ECONNREFUSED" });
    const openai = fakeOpenAi(async () => {
      throw err;
    });
    const client = createQwenChatClient(baseConfig, openai);
    await expect(
      client.completeChat({ messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toThrow(/llama-guardian|Cannot reach/i);
  });
});
