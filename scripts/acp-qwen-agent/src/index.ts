#!/usr/bin/env node
/**
 * Entry point.
 * - `--health`: probe guardian /v1/models and exit
 * - default: ACP JSON-RPC on stdio (stdout = protocol only)
 *
 * stdout = ACP protocol only. All logs use stderr.
 */
import { loadConfig, requireWorkspace } from "./config.js";
import { logError, logInfo } from "./logger.js";
import { runHealthCheck } from "./qwen/health.js";
import { createQwenChatClient } from "./qwen/client.js";
import { runAcpStdio } from "./acp/agent.js";
import { setAllowWrites } from "./tools/registry.js";
import fs from "node:fs";
import pathModule from "node:path";

async function main(argv: string[]): Promise<number> {
  const args = argv.slice(2);

  if (args.includes("--help") || args.includes("-h")) {
    console.error(`acp-qwen-agent

Usage:
  acp-qwen-agent --health   Probe llama-guardian /v1/models
  acp-qwen-agent            Run ACP agent on stdio (editor-launched)

Environment:
  ACP_QWEN_BASE_URL   default http://127.0.0.1:8080/v1
  ACP_QWEN_MODEL      default qwen3.6-35b
  ACP_WORKSPACE       absolute workspace path (tools; later sessions)
  ACP_QWEN_TIMEOUT_MS default 120000
  ACP_ALLOW_WRITES    default false
`);
    return 0;
  }

  let config;
  try {
    config = loadConfig();
    setAllowWrites(config.allowWrites);
  } catch (err) {
    logError(err instanceof Error ? err.message : String(err));
    return 1;
  }

  if (args.includes("--health")) {
    return runHealthCheck(config);
  }

  if (args.includes("--smoke")) {
    return runSmoke(config);
  }

  if (process.stdin.isTTY) {
    logError(
      "ACP stdio mode expected (no TTY). Launch via an ACP client, or pass --health.",
    );
    return 2;
  }

  try {
    const qwen = createQwenChatClient(config);
    logInfo("starting acp stdio agent", {
      baseUrl: config.baseUrl,
      model: config.model,
    });
    await runAcpStdio({ config, qwen });
    return 0;
  } catch (err) {
    logError(err instanceof Error ? err.message : String(err));
    return 1;
  }
}

async function runSmoke(config: ReturnType<typeof loadConfig>): Promise<number> {
  try {
    const ws = requireWorkspace(config);
    logInfo("--smoke start", { workspace: ws });

    // Write a fixture file (workspace is writable for smoke, writes off for safety)
    const smokeDir = pathModule.join(ws, "smoke");
    try {
      fs.mkdirSync(smokeDir, { recursive: true });
    } catch {
      // already exists
    }
    const fixturePath = pathModule.join(smokeDir, "fixture.txt");
    if (!fs.existsSync(fixturePath)) {
      fs.writeFileSync(fixturePath, "smoke-ok", "utf8");
    }

    // list_files on the smoke dir
    const { executeTool } = await import("./tools/registry.js");

    const listRes = await executeTool("list_files", { path: "smoke" }, {
      workspaceRoot: ws,
    });
    logInfo("--smoke list_files", { ok: listRes.ok });
    if (!listRes.ok) {
      logError("--smoke list_files failed", { output: listRes.output });
      return 1;
    }

    const readRes = await executeTool("read_file", { path: "smoke/fixture.txt" }, {
      workspaceRoot: ws,
    });
    logInfo("--smoke read_file", { ok: readRes.ok });
    if (!readRes.ok) {
      logError("--smoke read_file failed", { output: readRes.output });
      return 1;
    }

    if (!readRes.output.includes("smoke-ok")) {
      logError("--smoke read_file did not contain expected content");
      return 1;
    }

    logInfo("--smoke ok", { listFiles: true, readFile: true });
    return 0;
  } catch (err) {
    logError("--smoke failed", {
      message: err instanceof Error ? err.message : String(err),
    });
    return 1;
  }
}

main(process.argv)
  .then((code) => {
    process.exitCode = code;
  })
  .catch((err) => {
    logError(err instanceof Error ? err.message : String(err));
    process.exitCode = 1;
  });
