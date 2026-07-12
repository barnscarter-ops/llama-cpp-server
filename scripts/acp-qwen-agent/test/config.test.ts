import { afterEach, describe, expect, it } from "vitest";
import path from "node:path";
import { loadConfig, requireWorkspace } from "../src/config.js";

const ORIGINAL = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL };
});

describe("loadConfig", () => {
  it("applies defaults when env is empty", () => {
    delete process.env.ACP_QWEN_BASE_URL;
    delete process.env.ACP_QWEN_MODEL;
    delete process.env.ACP_WORKSPACE;
    delete process.env.ACP_QWEN_TIMEOUT_MS;
    delete process.env.ACP_ALLOW_WRITES;

    const cfg = loadConfig(process.env);
    expect(cfg.baseUrl).toBe("http://127.0.0.1:8080/v1");
    expect(cfg.model).toBe("qwen3.6-35b");
    expect(cfg.timeoutMs).toBe(120_000);
    expect(cfg.allowWrites).toBe(false);
    expect(cfg.workspace).toBeUndefined();
  });

  it("parses allowWrites truthy strings", () => {
    process.env.ACP_ALLOW_WRITES = "true";
    expect(loadConfig(process.env).allowWrites).toBe(true);
    process.env.ACP_ALLOW_WRITES = "1";
    expect(loadConfig(process.env).allowWrites).toBe(true);
    process.env.ACP_ALLOW_WRITES = "no";
    expect(loadConfig(process.env).allowWrites).toBe(false);
  });

  it("resolves workspace to an absolute path", () => {
    process.env.ACP_WORKSPACE = ".";
    const cfg = loadConfig(process.env);
    expect(cfg.workspace).toBe(path.resolve("."));
  });

  it("requireWorkspace throws when missing", () => {
    delete process.env.ACP_WORKSPACE;
    const cfg = loadConfig(process.env);
    expect(() => requireWorkspace(cfg)).toThrow(/ACP_WORKSPACE/);
  });
});
