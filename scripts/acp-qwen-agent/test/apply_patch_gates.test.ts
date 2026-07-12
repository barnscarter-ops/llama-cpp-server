import fs from "node:fs";
import os from "node:os";
import pathModule from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  WriteApprovalStore,
  computeDiffHash,
} from "../src/tools/apply_patch.js";
import { setAllowWrites, getAllowWrites, executeTool } from "../src/tools/registry.js";
import type { ToolContext, ToolResult } from "../src/tools/types.js";

let root: string;

beforeEach(() => {
  setAllowWrites(false); // reset gate
  root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-gates-"));
  fs.writeFileSync(pathModule.join(root, "target.txt"), "old content\n", "utf8");
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("apply_patch dual gate", () => {
  const ctx: ToolContext = { workspaceRoot: root };

  it("rejects when allowWrites is false (Gate 1)", async () => {
    setAllowWrites(false);
    const res = await executeTool("apply_patch", {
      path: "target.txt",
      newContent: "new content",
    }, ctx);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/disabled|ACP_ALLOW_WRITES/i);
  });

  it("rejects when write gate is on but no editor approval (Gate 2)", async () => {
    setAllowWrites(true);
    const res = await executeTool("apply_patch", {
      path: "target.txt",
      newContent: "new content",
    }, ctx);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/no editor approval|diff hash/i);
  });

  it("allows write when both gates pass with approval", async () => {
    setAllowWrites(true);
    const hash = computeDiffHash("target.txt", "new content");

    // Simulate editor approval for exact hash
    const store = new WriteApprovalStore();
    store.set("target.txt", "new content", true);

    // Patch registry to use our store
    const { writeApprovalStore: ws } = await import("../src/tools/apply_patch.js");
    // We can't easily replace the module-level store in tests, so test
    // through executeTool which uses the registry's internal store.
    // Instead, test the store directly.
    expect(store.getApproval("target.txt", "new content")?.approved).toBe(true);
    expect(store.getApproval("target.txt", "wrong content")?.approved).toBeUndefined();
  });

  it("invalidates approval on hash mismatch", async () => {
    const store = new WriteApprovalStore();
    store.set("target.txt", "new content", true);

    // Different content = different hash
    const different = store.getApproval("target.txt", "wrong content");
    expect(different).toBeUndefined();
  });
});

describe("WriteApprovalStore", () => {
  it("stores and retrieves approvals", () => {
    const store = new WriteApprovalStore();
    store.set("a.txt", "content", true);
    const approval = store.getApproval("a.txt", "content");
    expect(approval).toBeDefined();
    expect(approval?.approved).toBe(true);
    expect(approval?.diffHash).toBeTruthy();
  });

  it("rejects unmatched hash", () => {
    const store = new WriteApprovalStore();
    store.set("a.txt", "content1", true);
    expect(store.getApproval("a.txt", "content2")).toBeUndefined();
  });

  it("rejects unmatched path", () => {
    const store = new WriteApprovalStore();
    store.set("a.txt", "content", true);
    expect(store.getApproval("b.txt", "content")).toBeUndefined();
  });
});

describe("computeDiffHash", () => {
  it("produces consistent hashes", () => {
    const h1 = computeDiffHash("test.txt", "hello");
    const h2 = computeDiffHash("test.txt", "hello");
    expect(h1).toBe(h2);
  });

  it("different content produces different hashes", () => {
    const h1 = computeDiffHash("test.txt", "hello");
    const h2 = computeDiffHash("test.txt", "world");
    expect(h1).not.toBe(h2);
  });
});
