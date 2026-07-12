import fs from "node:fs";
import os from "node:os";
import pathModule from "node:path";
import { describe, expect, it, vi } from "vitest";
import { executeTool } from "../src/tools/registry.js";
import type { ToolContext } from "../src/tools/types.js";

describe("--smoke path (in-process)", () => {
  it("list_files + read_file works in a fresh workspace", async () => {
    // Create a temp workspace
    const ws = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-smoke-"));
    fs.writeFileSync(pathModule.join(ws, "fixture.txt"), "smoke-ok", "utf8");
    fs.mkdirSync(pathModule.join(ws, "sub"), { recursive: true });

    const ctx: ToolContext = { workspaceRoot: ws };

    // list_files should find fixture.txt
    const listRes = await executeTool("list_files", { path: "." }, ctx);
    expect(listRes.ok).toBe(true);
    expect(listRes.output).toContain("fixture.txt");

    // read_file should return the fixture content
    const readRes = await executeTool("read_file", { path: "fixture.txt" }, ctx);
    expect(readRes.ok).toBe(true);
    expect(readRes.output).toContain("smoke-ok");

    // Cleanup
    fs.rmSync(ws, { recursive: true, force: true });
  });
});

describe("malformed tool args", () => {
  it("returns structured error for invalid JSON in tool args", async () => {
    const ws = fs.mkdtempSync(pathModule.join(os.tmpdir(), "acp-malformed-"));
    fs.writeFileSync(pathModule.join(ws, "ok.txt"), "hello", "utf8");

    const ctx: ToolContext = { workspaceRoot: ws };

    // Pass a non-JSON string as args — list_files should parse it
    const res = await executeTool("list_files", "not-json", ctx);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/invalid tool arguments JSON|invalid args/i);

    fs.rmSync(ws, { recursive: true, force: true });
  });
});
