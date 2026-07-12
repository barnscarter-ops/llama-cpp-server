import OpenAI from "openai";
import type { AppConfig } from "../config.js";

export type ChatMessage =
  | {
      role: "system" | "user" | "assistant";
      content: string;
      tool_calls?: OpenAI.Chat.ChatCompletionMessageToolCall[];
    }
  | {
      role: "tool";
      content: string;
      tool_call_id: string;
    };

export type ToolSpec = {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  };
};

export type ToolCallRequest = {
  id: string;
  name: string;
  arguments: string;
};

export type CompleteChatParams = {
  messages: ChatMessage[];
  tools?: ToolSpec[];
  signal?: AbortSignal;
};

export type CompleteChatResult = {
  content: string | null;
  toolCalls: ToolCallRequest[];
};

/**
 * Minimal surface used by the agent so tests can inject a fake.
 */
export type QwenChatClient = {
  completeChat(params: CompleteChatParams): Promise<CompleteChatResult>;
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
    async completeChat(params: CompleteChatParams): Promise<CompleteChatResult> {
      try {
        const res = await openai.chat.completions.create(
          {
            model: config.model,
            messages: params.messages as OpenAI.Chat.ChatCompletionMessageParam[],
            stream: false,
            tools: params.tools as OpenAI.Chat.ChatCompletionTool[] | undefined,
            tool_choice: params.tools?.length ? "auto" : undefined,
          },
          { signal: params.signal },
        );

        const msg = res.choices[0]?.message;
        const toolCalls: ToolCallRequest[] = (msg?.tool_calls ?? [])
          .filter((tc) => tc.type === "function")
          .map((tc) => ({
            id: tc.id,
            name: tc.function.name,
            arguments: tc.function.arguments ?? "{}",
          }));

        const content =
          typeof msg?.content === "string" && msg.content.trim().length > 0
            ? msg.content
            : null;

        if (!content && toolCalls.length === 0) {
          throw new Error("Model returned empty content and no tool calls");
        }

        return { content, toolCalls };
      } catch (err) {
        throw mapOpenAiError(err, config.baseUrl);
      }
    },
  };
}
