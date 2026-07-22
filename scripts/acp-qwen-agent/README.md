# acp-qwen-agent

Local ACP agent that an editor launches over stdio. It submits bounded coding
requests to the loopback-only guardian queue; Hermes decides whether Qwen
should run them. The agent never calls raw Qwen or `llama-server :8081`.

## Requirements

- Node.js 18+
- llama-guardian reachable on port 8080 (do not call llama-server :8081 directly)

## Setup

```powershell
Set-Location C:\Workspace\Infrastructure\llama-cpp-server\scripts\acp-qwen-agent
npm ci
copy .env.example .env
# ACP_QUEUE_BASE_URL must remain loopback unless guardian remote auth is configured.
npm run check
npm test
npm run build
```

## CLI

```powershell
# Validate env + list models through guardian (exit 0 = model present)
npm run build
$env:ACP_QWEN_BASE_URL = 'http://127.0.0.1:8080/v1'
$env:ACP_QWEN_MODEL = 'qwen3.6-35b'
npm run start -- --health

# Smoke test: validates the retained workspace safety helpers only.
$env:ACP_WORKSPACE = 'C:\Temp\acp-qwen-smoke'
$env:ACP_ALLOW_WRITES = 'false'
npm run start -- --smoke

# Default (no flags): ACP JSON-RPC on stdio (editor launches this)
# The agent will block on stdio; use --health or --smoke for CLI testing.
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `ACP_QWEN_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible base URL |
| `ACP_QWEN_MODEL` | `qwen3.6-35b` | Model id from `GET /v1/models` |
| `ACP_QUEUE_BASE_URL` | base URL derived from `ACP_QWEN_BASE_URL` | Guardian queue origin, normally `http://127.0.0.1:8080` |
| `ACP_QUEUE_POLL_MS` | `750` | Job-status polling interval |
| `ACP_QUEUE_SOURCE` | `acp-qwen-agent` | Durable queue/audit source label |
| `ACP_WORKSPACE` | (none) | Used only by the local `--smoke` safety-helper check |
| `ACP_QWEN_TIMEOUT_MS` | `120000` | HTTP timeout (ms) |
| `ACP_ALLOW_WRITES` | `false` | Kept false; queued ACP mode never writes files |

## Safety Model

### Queue boundary

- Each editor prompt becomes a non-streaming queue job with a unique idempotency key.
- Hermes may queue it, bypass it as too small, or decline it for cloud fallback.
- A queued job returns text/diff only. It has no model tools, shell, network, or file-write capability.
- Cancelling the editor request best-effort cancels a queued (not already running) job.

### No automatic writes

Queued ACP mode never applies a model response. Review and apply a returned diff
in the calling editor or harness. The old workspace tool helpers remain covered
by tests and `--smoke`, but are intentionally not exposed to a queued model.

### Audit trail

All tool and model calls are logged as metadata-only audit events (no prompts, no file contents, no secrets). Access via `audit.list()` on the agent instance.

## Safety (general)

- Stdout is ACP protocol only. Logs go to stderr (`console.error`).
- Guardian is the sole route to Qwen; the ACP client never targets `:8081`.
- Queue jobs are non-streaming and pollable, with a finite timeout.

## Non-goals (v1)

No arbitrary shell, model tools, network tools, git commit/push/delete, agent-os dependency, streaming completions, or automatic writes.

## Recovery

If the agent hangs or Qwen becomes unresponsive:
- The agent loop has a finite timeout (`ACP_QWEN_TIMEOUT_MS`; default 120s).
- Ctrl+C (or process termination) is respected via `AbortController`.
- If a queue job is not accepted, its Hermes decision reason is returned to the editor; use the harness's current provider or rescope the task.
- If `--health` fails (guardian unreachable), verify llama-guardian is running on port 8080 (do not restart PM2 as part of this agent).

## Editor Integration (untested template)

No live ACP-capable editor was tested in this session. Below is the documented template for pointing an ACP client at this agent.

### Launch command

```powershell
node C:\Workspace\Infrastructure\llama-cpp-server\scripts\acp-qwen-agent\dist\index.js
```

### Required environment (before launching)

```powershell
$env:ACP_QWEN_BASE_URL = 'http://127.0.0.1:8080/v1'
$env:ACP_QWEN_MODEL = 'qwen3.6-35b'
$env:ACP_QUEUE_BASE_URL = 'http://127.0.0.1:8080'
$env:ACP_ALLOW_WRITES = 'false'
```

### ACP client configuration

An ACP-capable editor should launch this process over stdio (JSON-RPC over `node:stream`):
- **stdin/stdout**: ACP JSON-NYD protocol (the agent binds to `process.stdin` / `process.stdout`)
- **The agent uses `Writable.toWeb(process.stdout)` for the wire**; all logs go to stderr only

To integrate with a specific editor, follow that editor's ACP client plugin documentation and point the agent binary at the absolute path above with the required environment variables set.
