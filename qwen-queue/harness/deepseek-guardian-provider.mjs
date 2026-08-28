/**
 * DeepSeek Harness SubagentProvider that admits work only through llama-guardian.
 * Fixed work_class=tool_execution. Never accepts model/profile/endpoint/runner.
 * Pi native path is not implemented (paused 2026-08-28).
 *
 * Structural match for @deepseek-ai/dsh-subagent SubagentProvider:
 *   { name, capabilities, inheritsParentContext, start(request) }
 */
export const NO_START_CAPABILITIES = Object.freeze({
  outputSchema: false,
  depthLimit: false,
  toolFilter: false,
  persona: false,
});

const FORBIDDEN = new Set(["profile", "model", "endpoint", "runner", "executable"]);
const JOB_ID = /^qj_[A-Za-z0-9]{8,64}$/;

export function flattenPrompt(prompt) {
  if (!Array.isArray(prompt)) return "";
  return prompt
    .map((block) => (block && typeof block.text === "string" ? block.text : ""))
    .join("\n")
    .trim();
}

export function guardianWorkerPayload(request, { workClass = "tool_execution" } = {}) {
  if (request && typeof request === "object") {
    for (const key of FORBIDDEN) {
      if (Object.hasOwn(request, key)) {
        throw new Error(`${key} is guardian-owned and not accepted`);
      }
    }
  }
  const workspace = request?.parent?.session?.header?.cwd;
  if (typeof workspace !== "string" || !workspace.trim()) {
    throw new Error("parent session cwd (workspace) is required");
  }
  const parentRunId = request?.parent?.session?.id ?? "";
  const task = flattenPrompt(request?.prompt);
  if (!task) throw new Error("prompt is required");
  return {
    work_class: workClass,
    preference: "prefer_local",
    quality_floor: "standard",
    task,
    workspace,
    source: "deepseek-harness",
    parent_run_id: String(parentRunId),
  };
}

export async function guardianRequest(method, path, body, { baseUrl, fetchImpl } = {}) {
  const root = (baseUrl || process.env.GUARDIAN_BASE_URL || "http://127.0.0.1:8080").replace(/\/$/, "");
  const fn = fetchImpl || fetch;
  const init = { method, headers: { "content-type": "application/json" } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fn(`${root}${path}`, init);
  const text = await response.text();
  let parsed;
  try {
    parsed = text ? JSON.parse(text) : {};
  } catch {
    parsed = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    const message = parsed?.error?.message || parsed?.error || `HTTP ${response.status}`;
    const err = new Error(String(message));
    err.status = response.status;
    err.code = parsed?.error?.code;
    err.body = parsed;
    throw err;
  }
  return parsed;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class GuardianSubagentProvider {
  readonlyName = "guardian";
  capabilities = NO_START_CAPABILITIES;
  inheritsParentContext = false;

  constructor(options = {}) {
    this.name = options.name || "guardian";
    this.baseUrl = options.baseUrl || process.env.GUARDIAN_BASE_URL || "http://127.0.0.1:8080";
    this.fetchImpl = options.fetchImpl;
    this.workClass = options.workClass || "tool_execution";
    this.pollMs = options.pollMs || 2000;
  }

  async start(request) {
    const payload = guardianWorkerPayload(request, { workClass: this.workClass });
    const accepted = await guardianRequest("POST", "/__guardian/workers", payload, {
      baseUrl: this.baseUrl,
      fetchImpl: this.fetchImpl,
    });
    const jobId = accepted.job_id;
    if (typeof jobId !== "string" || !JOB_ID.test(jobId)) {
      throw new Error("guardian did not return a job_id");
    }
    const signal = request.signal;
    let cancelled = false;
    const result = (async () => {
      while (true) {
        if (signal?.aborted || cancelled) {
          cancelled = true;
          try {
            await guardianRequest("POST", `/__guardian/jobs/${jobId}/cancel`, {}, {
              baseUrl: this.baseUrl,
              fetchImpl: this.fetchImpl,
            });
          } catch {
            // cancel is best-effort; settlement still reads the durable row
          }
          return {
            output: [],
            diagnostic: "guardian worker cancelled",
            stopReason: "aborted",
          };
        }
        const status = await guardianRequest("GET", `/__guardian/jobs/${jobId}`, undefined, {
          baseUrl: this.baseUrl,
          fetchImpl: this.fetchImpl,
        });
        const state = status.status;
        if (state === "succeeded") {
          return {
            output: [{ type: "text", text: JSON.stringify(status.result ?? {}) }],
            stopReason: "completed",
          };
        }
        if (state === "cancelled" || cancelled) {
          return {
            output: [],
            diagnostic: "guardian worker cancelled",
            stopReason: "aborted",
          };
        }
        if (state === "failed") {
          return {
            output: [],
            diagnostic: String(status.error || "guardian worker failed").slice(0, 4096),
            stopReason: "error",
          };
        }
        await sleep(this.pollMs);
      }
    })();
    return {
      id: jobId,
      localAgent: undefined,
      result,
      dispose: async () => {
        cancelled = true;
        try {
          await guardianRequest("POST", `/__guardian/jobs/${jobId}/cancel`, {}, {
            baseUrl: this.baseUrl,
            fetchImpl: this.fetchImpl,
          });
        } catch {
          // already terminal
        }
      },
    };
  }
}

/** Cordis-style named exports if loaded as a DSH plugin. */
export const name = "subagent-guardian";
export const inject = ["subagents"];

export function apply(ctx, config = {}) {
  const provider = new GuardianSubagentProvider(config);
  ctx.subagents.registerProvider(provider);
  return provider;
}
