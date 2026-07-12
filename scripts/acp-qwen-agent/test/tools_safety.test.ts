import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { executeTool, getToolDefinitions } from "../src/tools/registry.js";

let root: string;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "acp-tools-"));
  fs.writeFileSync(path.join(root, "readme.md"), "# Hello\nsearch-token-xyz\n", "utf8");
  fs.mkdirSync(path.join(root, "src"));
  fs.writeFileSync(path.join(root, "src", "app.ts"), "export const n = 1;\n", "utf8");
  fs.mkdirSync(path.join(root, "node_modules", "pkg"), { recursive: true });
  fs.writeFileSync(path.join(root, "node_modules", "pkg", "x.js"), "nope", "utf8");
  fs.writeFileSync(path.join(root, "binary.bin"), Buffer.from([0, 1, 2, 3, 0, 255]));
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("read-only tools", () => {
  it("registers exactly three tools", () => {
    expect(getToolDefinitions().map((t) => t.name).sort()).toEqual(
      ["list_files", "read_file", "search_text"].sort(),
    );
  });

  it("list_files skips node_modules", async () => {
    const res = await executeTool("list_files", { path: ".", maxDepth: 3 }, {
      workspaceRoot: root,
    });
    expect(res.ok).toBe(true);
    expect(res.output).toContain("readme.md");
    expect(res.output).not.toContain("node_modules");
  });

  it("read_file returns text", async () => {
    const res = await executeTool(
      "read_file",
      { path: "readme.md" },
      { workspaceRoot: root },
    );
    expect(res.ok).toBe(true);
    expect(res.output).toContain("# Hello");
  });

  it("read_file rejects path escape", async () => {
    const res = await executeTool(
      "read_file",
      { path: "../outside.txt" },
      { workspaceRoot: root },
    );
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/path rejected|escapes workspace/i);
  });

  it("read_file rejects binary", async () => {
    const res = await executeTool(
      "read_file",
      { path: "binary.bin" },
      { workspaceRoot: root },
    );
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/binary/i);
  });

  it("search_text finds a literal (requires rg on PATH)", async () => {
    const res = await executeTool(
      "search_text",
      { pattern: "search-token-xyz", path: "." },
      { workspaceRoot: root },
    );
    if (!res.ok && /rg \(ripgrep\) not found/i.test(res.output)) {
      // Environment without rg — document but do not fail the whole suite hard.
      expect(res.output).toMatch(/rg/);
      return;
    }
    expect(res.ok).toBe(true);
    expect(res.output).toMatch(/search-token-xyz/);
  });
});
