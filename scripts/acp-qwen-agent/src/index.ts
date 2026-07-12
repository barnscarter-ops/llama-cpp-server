#!/usr/bin/env node
/**
 * Entry point.
 * - `--health`: probe guardian /v1/models and exit
 * - default: ACP JSON-RPC on stdio (stdout = protocol only)
 *
 * stdout = ACP protocol only. All logs use stderr.
 */
import { loadConfig } from "./config.js";
import { logError, logInfo } from "./logger.js";
import { runHealthCheck } from "./qwen/health.js";
import { createQwenChatClient } from "./qwen/client.js";
import { runAcpStdio } from "./acp/agent.js";

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
  } catch (err) {
    logError(err instanceof Error ? err.message : String(err));
    return 1;
  }

  if (args.includes("--health")) {
    return runHealthCheck(config);
  }

  if (args.includes("--smoke")) {
    logError("--smoke is implemented in a later session; use --health for now");
    return 2;
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

main(process.argv)
  .then((code) => {
    process.exitCode = code;
  })
  .catch((err) => {
    logError(err instanceof Error ? err.message : String(err));
    process.exitCode = 1;
  });
