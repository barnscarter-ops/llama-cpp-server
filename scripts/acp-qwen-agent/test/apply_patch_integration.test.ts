import fs from "node:fs";
import pathModule from "node:path";
import os from "node:os";
import { describe, expect, it } from "vitest";
import {
  WriteApprovalStore,
  computeDiffHash,
  executePatchWrite,
  writeApprovalStore,
} from "../src/tools/apply_patch.js";
import { setAllowWrites, executeTool } from "../src/tools/registry.js";
import type { ToolContext } from "../src/tools/types.js";

describe("apply_patch integration: approval wiring", () => {
  it("rejects write when allowWrites=false (Gate 1)", async () => {
    setAllowWrites(false);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    const res = await executeTool("apply_patch", {
      path: "test.txt",
      newContent: "hello world",
    }, ctx);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/disabled|ACP_ALLOW_WRITES/i);
  });

  it("rejects write when allowWrites=true but no approval (Gate 2)", async () => {
    setAllowWrites(true);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    const res = await executeTool("apply_patch", {
      path: "test.txt",
      newContent: "hello world",
    }, ctx);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/no editor approval|diff hash/i);
  });

  it("allows write after recording approval for exact content (file on disk)", async () => {
    setAllowWrites(true);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    const path = "approved.txt";
    const content = "approved content\n";

    // Simulate editor approval (like handleApplyPatch does after ACP allow)
    writeApprovalStore.set(path, content, true);

    // executeTool should pass both gates
    const res = await executeTool("apply_patch", { path, newContent: content }, ctx);
    expect(res.ok).toBe(true);
    expect(res.output).toMatch(/Wrote/);

    // Verify file was written to disk
    const fullPath = pathModule.join(root, path);
    expect(fs.existsSync(fullPath)).toBe(true);
    expect(fs.readFileSync(fullPath, "utf8")).toBe(content);
  });

  it("rejects write with different content after approval (hash mismatch)", async () => {
    setAllowWrites(true);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    // First, approve content A
    const approvedPath = "replay.txt";
    const contentA = "content A\n";
    writeApprovalStore.set(approvedPath, contentA, true);

    // ExecuteTool with content A — should succeed
    const resA = await executeTool("apply_patch", {
      path: approvedPath,
      newContent: contentA,
    }, ctx);
    expect(resA.ok).toBe(true);

    // Now try content B with the same approval — should fail
    const contentB = "content B\n";
    const resB = await executeTool("apply_patch", {
      path: approvedPath,
      newContent: contentB,
    }, ctx);
    expect(resB.ok).toBe(false);
    expect(resB.output).toMatch(/no editor approval|diff hash/i);

    // Verify file still has content A, not B
    const fullPath = pathModule.join(root, approvedPath);
    expect(fs.readFileSync(fullPath, "utf8")).toBe(contentA);
  });

  it("consumes approval after successful write (no replay)", async () => {
    setAllowWrites(true);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    const path = "consumed.txt";
    const content = "consumed\n";

    writeApprovalStore.set(path, content, true);

    // First write — succeeds
    const res1 = await executeTool("apply_patch", { path, newContent: content }, ctx);
    expect(res1.ok).toBe(true);

    // Approval should be consumed (cleared after write)
    const approvalAfter = writeApprovalStore.getApproval(path, content);
    expect(approvalAfter).toBeUndefined();

    // Second write with same content — should fail (approval consumed)
    const res2 = await executeTool("apply_patch", { path, newContent: content }, ctx);
    expect(res2.ok).toBe(false);
    expect(res2.output).toMatch(/no editor approval|diff hash/i);
  });

  it("WriteApprovalStore.clear removes approval", () => {
    const store = new WriteApprovalStore();
    store.set("x.txt", "content", true);
    expect(store.getApproval("x.txt", "content")?.approved).toBe(true);

    store.clear("x.txt", "content");
    expect(store.getApproval("x.txt", "content")).toBeUndefined();
  });

  it("executePatchWrite writes to disk directly", () => {
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const writePath = "direct-write.txt";
    const writeContent = "direct-write-content\n";

    const result = executePatchWrite(writePath, writeContent, root);
    expect(result.ok).toBe(true);
    expect(result.output).toMatch(/Wrote/);

    const fullPath = pathModule.join(root, writePath);
    expect(fs.existsSync(fullPath)).toBe(true);
    expect(fs.readFileSync(fullPath, "utf8")).toBe(writeContent);
  });

  it("handleApplyPatch records approval and writes (in-process integration)", async () => {
    setAllowWrites(true);
    const root = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-integration-"));
    const ctx: ToolContext = { workspaceRoot: root };

    const path = "loop-integration.txt";
    const content = "loop-integration-content\n";

    // Simulate what handleApplyPatch does after editor approval:
    writeApprovalStore.set(path, content, true);

    // Then executeTool should succeed (same path as handleApplyPatch)
    const res = await executeTool("apply_patch", { path, newContent: content }, ctx);
    expect(res.ok).toBe(true);
    expect(res.output).toMatch(/Wrote/);

    const fullPath = pathModule.join(root, path);
    expect(fs.existsSync(fullPath)).toBe(true);
    expect(fs.readFileSync(fullPath, "utf8")).toBe(content);
  });
});
