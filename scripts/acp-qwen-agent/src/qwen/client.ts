import OpenAI from "openai";
import type { AppConfig } from "../config.js";

export type ChatMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type CompleteChatParams = {
  messages: ChatMessage[];
  signal?: AbortSignal;
};

/**
 * Minimal surface used by the agent so tests can inject a fake.
 */
export type QwenChatClient = {
  completeChat(params: CompleteChatParams): Promise<string>;
};

function mapOpenAiError(err: unknown, baseUrl: string): Error {
  if (err instanceof Error && err.name === "AbortError") {
    return new Error(
      `Qwen request aborted or timed out talking to ${baseUrl} (guardian may be cold-starting or busy)`,
    );
  }

  const anyErr = err as {
    status?: number;
    code?: string;
    message?: string;
    cause?: unknown;
  };

  const status = anyErr?.status;
  const code = anyErr?.code;
  const message =
    err instanceof Error ? err.message : String(err ?? "unknown error");

  if (
    code === "ECONNREFUSED" ||
    code === "ENOTFOUND" ||
    /ECONNREFUSED|fetch failed|network/i.test(message)
  ) {
    return new Error(
      `Cannot reach llama-guardian at ${baseUrl}: ${message}. Is the guardian up on :8080?`,
    );
  }

  if (status === 502 || status === 503 || status === 504) {
    return new Error(
      `Guardian at ${baseUrl} returned HTTP ${status} (model may be cold-starting): ${message}`,
    );
  }

  return new Error(`Qwen completion failed (${baseUrl}): ${message}`);
}

export function createOpenAiClient(config: AppConfig): OpenAI {
  return new OpenAI({
    baseURL: config.baseUrl,
    apiKey: process.env.ACP_QWEN_API_KEY ?? "not-needed",
    timeout: config.timeoutMs,
  });
}

export function createQwenChatClient(
  config: AppConfig,
  openai: OpenAI = createOpenAiClient(config),
): QwenChatClient {
  return {
    async completeChat(params: CompleteChatParams): Promise<string> {
      try {
        const res = await openai.chat.completions.create(
          {
            model: config.model,
            messages: params.messages,
            stream: false,
          },
          { signal: params.signal },
        );

        const content = res.choices[0]?.message?.content;
        if (typeof content !== "string" || content.trim().length === 0) {
          throw new Error("Model returned empty content");
        }
        return content;
      } catch (err) {
        throw mapOpenAiError(err, config.baseUrl);
      }
    },
  };
}
