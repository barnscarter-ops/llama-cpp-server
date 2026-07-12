import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  PathGuardError,
  resolveInsideWorkspace,
  toWorkspaceRelative,
} from "../src/tools/path_guard.js";

let root: string;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "acp-path-"));
  fs.writeFileSync(path.join(root, "ok.txt"), "hello", "utf8");
  fs.mkdirSync(path.join(root, "sub"));
  fs.writeFileSync(path.join(root, "sub", "nested.txt"), "n", "utf8");
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("resolveInsideWorkspace", () => {
  it("allows relative paths inside workspace", () => {
    const p = resolveInsideWorkspace(root, "sub/nested.txt");
    expect(p).toBe(fs.realpathSync.native(path.join(root, "sub", "nested.txt")));
  });

  it("allows absolute paths inside workspace", () => {
    const abs = path.join(root, "ok.txt");
    expect(resolveInsideWorkspace(root, abs)).toBe(fs.realpathSync.native(abs));
  });

  it("rejects .. traversal escape", () => {
    expect(() => resolveInsideWorkspace(root, "../outside.txt")).toThrow(
      PathGuardError,
    );
  });

  it("rejects absolute path outside workspace", () => {
    const outside = path.join(os.tmpdir(), "acp-outside-not-ws.txt");
    fs.writeFileSync(outside, "x", "utf8");
    try {
      expect(() => resolveInsideWorkspace(root, outside)).toThrow(PathGuardError);
    } finally {
      fs.unlinkSync(outside);
    }
  });

  it("rejects symlink escape when supported", () => {
    const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), "acp-out-"));
    const outsideFile = path.join(outsideDir, "secret.txt");
    fs.writeFileSync(outsideFile, "secret", "utf8");
    const linkPath = path.join(root, "escape-link");
    try {
      fs.symlinkSync(outsideDir, linkPath, "junction");
    } catch {
      // Some environments block symlinks; skip rather than fail the suite.
      fs.rmSync(outsideDir, { recursive: true, force: true });
      return;
    }
    try {
      expect(() =>
        resolveInsideWorkspace(root, path.join("escape-link", "secret.txt")),
      ).toThrow(PathGuardError);
    } finally {
      fs.rmSync(linkPath, { recursive: true, force: true });
      fs.rmSync(outsideDir, { recursive: true, force: true });
    }
  });

  it("toWorkspaceRelative returns relative path", () => {
    const abs = resolveInsideWorkspace(root, "ok.txt");
    expect(toWorkspaceRelative(root, abs).replaceAll("\\", "/")).toBe("ok.txt");
  });
});
