import { describe, expect, it, vi } from "vitest";
import { createQwenChatClient, type QueueFetch } from "../src/qwen/client.js";
import type { AppConfig } from "../src/config.js";

const baseConfig: AppConfig = {
  baseUrl: "http://127.0.0.1:8080/v1",
  model: "local-llm",
  queueBaseUrl: "http://127.0.0.1:8080",
  queuePollMs: 1,
  queueSource: "acp-qwen-agent",
  timeoutMs: 5_000,
  allowWrites: false,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("createQwenChatClient", () => {
  it("submits a diff-only task and polls the guardian queue", async () => {
    const fetchFn = vi
      .fn<QueueFetch>()
      .mockResolvedValueOnce(jsonResponse({ job_id: "qj_1", status: "queued" }))
      .mockResolvedValueOnce(
        jsonResponse({
          job_id: "qj_1",
          status: "succeeded",
          result: { response: { choices: [{ message: { content: "diff --git a/a b/a" } }] } },
        }),
      );
    const client = createQwenChatClient(baseConfig, fetchFn);

    const res = await client.completeChat({
      messages: [{ role: "user", content: "add a focused test" }],
    });

    expect(res.content).toBe("diff --git a/a b/a");
    expect(res.toolCalls).toEqual([]);
    expect(fetchFn.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8080/__guardian/jobs");
    const submitted = JSON.parse(String(fetchFn.mock.calls[0]?.[1]?.body));
    expect(submitted.source).toBe("acp-qwen-agent");
    expect(submitted.request.tools).toBeUndefined();
    expect(submitted.decision_context.summary).toContain("add a focused test");
  });

  it("surfaces a Hermes bypass without directly calling Qwen", async () => {
    const fetchFn = vi.fn<QueueFetch>().mockResolvedValue(
      jsonResponse({
        status: "not_queued",
        decision: { route: "bypass", reason: "Tiny mechanical edit" },
      }),
    );
    const client = createQwenChatClient(baseConfig, fetchFn);

    await expect(
      client.completeChat({ messages: [{ role: "user", content: "fix typo" }] }),
    ).rejects.toThrow(/Hermes did not route.*Tiny mechanical edit/i);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("rejects model tools before submitting a queue job", async () => {
    const fetchFn = vi.fn<QueueFetch>();
    const client = createQwenChatClient(baseConfig, fetchFn);

    await expect(
      client.completeChat({
        messages: [{ role: "user", content: "list files" }],
        tools: [
          {
            type: "function",
            function: { name: "list_files", description: "list", parameters: {} },
          },
        ],
      }),
    ).rejects.toThrow(/diff-only.*tools/i);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("surfaces a failed job result", async () => {
    const fetchFn = vi
      .fn<QueueFetch>()
      .mockResolvedValueOnce(jsonResponse({ job_id: "qj_2", status: "queued" }))
      .mockResolvedValueOnce(jsonResponse({ job_id: "qj_2", status: "failed", error: "Qwen timed out" }));
    const client = createQwenChatClient(baseConfig, fetchFn);

    await expect(
      client.completeChat({ messages: [{ role: "user", content: "add test" }] }),
    ).rejects.toThrow(/failed.*Qwen timed out/i);
  });
});
