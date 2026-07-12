# acp-qwen-agent

Local ACP agent that an editor launches over stdio. It talks to the local Qwen model through llama-guardian at `http://127.0.0.1:8080/v1`.

## Requirements

- Node.js 18+
- llama-guardian reachable on port 8080 (do not call llama-server :8081 directly)
- ripgrep (`rg`) available on PATH (for `search_text` tool; gracefully degrades with error message if missing)

## Setup

```powershell
Set-Location C:\Workspace\Infrastructure\llama-cpp-server\scripts\acp-qwen-agent
npm ci
copy .env.example .env
# edit .env: set ACP_WORKSPACE to an absolute disposable path
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

# Smoke test: list + read a fixture file in ACP_WORKSPACE (requires ACP_WORKSPACE)
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
| `ACP_WORKSPACE` | (none) | Absolute workspace root for tools (required for tools, `--smoke`) |
| `ACP_QWEN_TIMEOUT_MS` | `120000` | HTTP timeout (ms) |
| `ACP_ALLOW_WRITES` | `false` | Master write gate (see Safety below) |
| `ACP_QWEN_API_KEY` | `not-needed` | Guardian does not require a real API key |

## Safety Model

### Dual write gate

`apply_patch` (writing files) requires **both** conditions:

1. **`ACP_ALLOW_WRITES=true`** — the environment variable must be explicitly set.
2. **Editor approval for the exact diff hash** — the editor must approve via `session/request_permission` for the specific `{path}:{newContent}` SHA-256 hash.

If the content changes even slightly, the hash changes and a prior approval is invalid. Each approval is consumed (used once) and cannot be replayed for a different hash.

### Tool safety

All tools enforce workspace path guards:
- `resolveInsideWorkspace` resolves the full path and verifies it is inside `ACP_WORKSPACE`.
- Symlink escapes, `..` traversal, and absolute paths outside the workspace are rejected.
- Files are read in UTF-8 only; binary files are rejected.
- Output is capped at 24 000 characters; reads at 256 000 bytes.
- `list_files` skips `.git`, `node_modules`, models, logs, secrets, `.pem`, `.key`.

### Audit trail

All tool and model calls are logged as metadata-only audit events (no prompts, no file contents, no secrets). Access via `audit.list()` on the agent instance.

## Safety (general)

- Stdout is ACP protocol only. Logs go to stderr (`console.error`).
- v1 tools stay inside `ACP_WORKSPACE` only.
- Max 6 model↔tool turns per prompt; stops with `max_turn_requests`.

## Non-goals (v1)

No arbitrary shell, no network tools, no git commit/push/delete, no agent-os dependency, no streaming completions.

## Tools

| Tool | Description |
|---|---|
| `list_files` | List files/dirs in workspace (depth-capped, skips node_modules/.git etc.) |
| `read_file` | Read a UTF-8 text file (rejects binary, oversized files) |
| `search_text` | Fixed-string search via ripgrep (gracefully degrades if `rg` missing) |
| `propose_patch` | In-memory unified diff only; no disk write |
| `apply_patch` | Write a file (requires dual write gate — see Safety above) |

## Recovery

If the agent hangs or Qwen becomes unresponsive:
- The agent loop has a finite timeout (`ACP_QWEN_TIMEOUT_MS`; default 120s).
- Ctrl+C (or process termination) is respected via `AbortController`.
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
$env:ACP_WORKSPACE = 'C:\Path\To\Your\Workspace'
$env:ACP_ALLOW_WRITES = 'false'  # or 'true' if you want write capability
```

### ACP client configuration

An ACP-capable editor should launch this process over stdio (JSON-RPC over `node:stream`):
- **stdin/stdout**: ACP JSON-NYD protocol (the agent binds to `process.stdin` / `process.stdout`)
- **The agent uses `Writable.toWeb(process.stdout)` for the wire**; all logs go to stderr only

To integrate with a specific editor, follow that editor's ACP client plugin documentation and point the agent binary at the absolute path above with the required environment variables set.
