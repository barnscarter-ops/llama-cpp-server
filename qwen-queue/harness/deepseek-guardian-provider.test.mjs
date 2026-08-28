import assert from "node:assert/strict";
import test from "node:test";
import {
  GuardianSubagentProvider,
  flattenPrompt,
  guardianWorkerPayload,
} from "./deepseek-guardian-provider.mjs";

test("flattens text prompt blocks", () => {
  assert.equal(flattenPrompt([{ type: "text", text: "one" }, { type: "text", text: "two" }]), "one\ntwo");
});

test("payload is work-class only: no model/profile/endpoint", () => {
  const body = guardianWorkerPayload({
    prompt: [{ type: "text", text: "write SMOKE.txt" }],
    parent: { session: { id: "sess-1", header: { cwd: "D:/Workspace/tmp/dsh-smoke" } } },
  });
  assert.equal(body.work_class, "tool_execution");
  assert.equal(body.source, "deepseek-harness");
  assert.equal(body.parent_run_id, "sess-1");
  assert.equal(body.workspace, "D:/Workspace/tmp/dsh-smoke");
  for (const key of ["model", "profile", "endpoint", "runner", "executable"]) {
    assert.equal(Object.hasOwn(body, key), false);
  }
});

test("rejects caller-supplied model on the request object", () => {
  assert.throws(
    () => guardianWorkerPayload({
      model: "local-llm",
      prompt: [{ type: "text", text: "x" }],
      parent: { session: { header: { cwd: "D:/Workspace/tmp" } } },
    }),
    /model is guardian-owned/,
  );
});

test("start posts to guardian and cannot target a raw alias", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
    if (init.method === "POST" && url.endsWith("/__guardian/workers")) {
      const body = JSON.parse(init.body);
      assert.equal(body.source, "deepseek-harness");
      assert.equal(body.work_class, "tool_execution");
      assert.equal(Object.hasOwn(body, "model"), false);
      return {
        ok: true,
        status: 202,
        text: async () => JSON.stringify({ job_id: "qj_abcdef12", status: "queued" }),
      };
    }
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        job_id: "qj_abcdef12",
        status: "succeeded",
        result: { kind: "local_worker", work_class: "tool_execution" },
      }),
    };
  };
  const provider = new GuardianSubagentProvider({ fetchImpl, pollMs: 1 });
  assert.equal(provider.inheritsParentContext, false);
  assert.equal(provider.capabilities.outputSchema, false);
  const run = await provider.start({
    prompt: [{ type: "text", text: "bounded task" }],
    parent: { session: { id: "parent-run", header: { cwd: "D:/Workspace/tmp/dsh-smoke" } } },
  });
  const result = await run.result;
  assert.equal(result.stopReason, "completed");
  assert.equal(run.localAgent, undefined);
  assert.equal(calls[0].url.endsWith("/__guardian/workers"), true);
});

test("dispose cancels the durable guardian job", async () => {
  const paths = [];
  const fetchImpl = async (url, init) => {
    paths.push(`${init.method} ${url}`);
    if (url.endsWith("/workers")) {
      return { ok: true, status: 202, text: async () => JSON.stringify({ job_id: "qj_cancel001", status: "queued" }) };
    }
    if (url.endsWith("/cancel")) {
      return { ok: true, status: 200, text: async () => JSON.stringify({ job_id: "qj_cancel001", status: "cancelled" }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ job_id: "qj_cancel001", status: "queued" }) };
  };
  const provider = new GuardianSubagentProvider({ fetchImpl, pollMs: 50 });
  const run = await provider.start({
    prompt: [{ type: "text", text: "x" }],
    parent: { session: { id: "p", header: { cwd: "D:/Workspace/tmp" } } },
  });
  await run.dispose();
  assert.ok(paths.some((p) => p.includes("POST") && p.includes("/cancel")));
});
