import { randomUUID } from "node:crypto";
import type { AppConfig } from "../config.js";

export type ChatMessage =
  | {
      role: "system" | "user" | "assistant";
      content: string;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
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

export type QwenChatClient = {
  completeChat(params: CompleteChatParams): Promise<CompleteChatResult>;
};

type QueueDecision = {
  route?: string;
  reason?: string;
};

type QueueJob = {
  job_id?: string;
  status?: string;
  error?: string | null;
  decision?: QueueDecision;
  result?: {
    response?: {
      choices?: Array<{
        message?: { content?: string | null };
      }>;
    };
  };
};

export type QueueFetch = typeof fetch;

function queueUrl(config: AppConfig, suffix: string): string {
  return `${config.queueBaseUrl.replace(/\/$/, "")}/__guardian/${suffix}`;
}

function decisionSummary(messages: ChatMessage[]): string {
  const userText = messages
    .filter((message) => message.role === "user")
    .map((message) => message.content)
    .join("\n")
    .trim();
  const excerpt = userText.slice(0, 8_000);
  return [
    "ACP editor request for a bounded coding diff.",
    "The result must be reviewed and is never applied automatically.",
    `Task: ${excerpt || "No user task supplied."}`,
  ].join(" ");
}

function queueError(prefix: string, job: QueueJob): Error {
  const detail = job.error || job.decision?.reason || "no diagnostic returned";
  return new Error(`${prefix}: ${detail}`);
}

async function responseJson(response: Response): Promise<QueueJob> {
  const body = (await response.json().catch(() => ({}))) as QueueJob & {
    error?: { message?: string };
  };
  if (!response.ok) {
    throw new Error(
      `Guardian queue returned HTTP ${response.status}: ${body.error?.message || body.error || "unknown error"}`,
    );
  }
  return body;
}

type PollResult = {
  job: QueueJob;
  retries: number;
};

async function pollJobStatus(
  fetchFn: QueueFetch,
  url: string,
  signal: AbortSignal,
): Promise<PollResult> {
  const backoffs = [500, 1000, 2000];
  let retries = 0;

  for (let attempt = 0; attempt <= backoffs.length; attempt++) {
    let res: Response;
    try {
      res = await fetchFn(url, {
        method: "GET",
        signal,
        headers: { accept: "application/json" },
      });
    } catch (err) {
      // network error — retryable
      if (attempt < backoffs.length) {
        await waitForPoll(backoffs[attempt], signal);
        retries++;
        continue;
      }
      throw err;
    }

    // HTTP 5xx (guardian overload / cold-start) — retryable; 4xx is not
    if (res.status >= 500 && attempt < backoffs.length) {
      await waitForPoll(backoffs[attempt], signal);
      retries++;
      continue;
    }

    return { job: await responseJson(res), retries };
  }

  throw new Error("unreachable");
}

function waitForPoll(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("Queue request aborted"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new Error("Queue request aborted"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function cancelQueuedJob(
  fetchFn: QueueFetch,
  config: AppConfig,
  jobId: string,
): Promise<void> {
  try {
    await fetchFn(queueUrl(config, `jobs/${encodeURIComponent(jobId)}/cancel`), {
      method: "POST",
      headers: { accept: "application/json" },
    });
  } catch {
    // The caller's cancellation remains authoritative. A running Qwen job may
    // finish, but a queued job is best-effort cancelled by the guardian.
  }
}

/**
 * Queue-only Qwen client. It never calls llama-server's OpenAI endpoint
 * directly; Hermes decides whether guardian may enqueue the task.
 */
export function createQwenChatClient(
  config: AppConfig,
  fetchFn: QueueFetch = fetch,
): QwenChatClient {
  return {
    async completeChat(params: CompleteChatParams): Promise<CompleteChatResult> {
      if (params.tools?.length) {
        throw new Error("ACP queue mode is diff-only and does not permit model tools.");
      }

      const controller = new AbortController();
      const abort = () => controller.abort();
      params.signal?.addEventListener("abort", abort, { once: true });
      const timeout = setTimeout(() => controller.abort(), config.timeoutMs);
      let jobId: string | undefined;

      try {
        const submit = await fetchFn(queueUrl(config, "jobs"), {
          method: "POST",
          signal: controller.signal,
          headers: { "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify({
            source: config.queueSource,
            idempotency_key: randomUUID(),
            decision_context: { summary: decisionSummary(params.messages) },
            request: {
              model: config.model,
              messages: params.messages,
              stream: false,
            },
          }),
        });
        let job = await responseJson(submit);
        if (job.status === "not_queued") {
          throw queueError("Hermes did not route this ACP task to Qwen", job);
        }
        jobId = job.job_id;
        if (!jobId) {
          throw new Error("Guardian queue response did not include a job_id");
        }

        let pollRetries = 0;

        while (job.status === "queued" || job.status === "running") {
          await waitForPoll(config.queuePollMs, controller.signal);
          const result = await pollJobStatus(
            fetchFn,
            queueUrl(config, `jobs/${encodeURIComponent(jobId)}`),
            controller.signal,
          );
          pollRetries += result.retries;
          job = result.job;
        }

        if (job.status !== "succeeded") {
          throw queueError(`Guardian queue job ${job.status || "unknown"}`, job);
        }
        const content = job.result?.response?.choices?.[0]?.message?.content;
        if (typeof content !== "string" || !content.trim()) {
          throw new Error("Queued Qwen job completed without assistant text");
        }
        return { content, toolCalls: [] };
      } catch (err) {
        if (jobId && controller.signal.aborted) {
          await cancelQueuedJob(fetchFn, config, jobId);
        }
        if (err instanceof Error && err.name === "AbortError") {
          throw new Error("Guardian queue request aborted or timed out");
        }
        throw err;
      } finally {
        clearTimeout(timeout);
        params.signal?.removeEventListener("abort", abort);
      }
    },
  };
}
