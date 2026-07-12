# acp-qwen-agent

Local ACP agent that an editor launches over stdio. It talks to the local Qwen model through llama-guardian at `http://127.0.0.1:8080/v1`.

## Requirements

- Node.js 18+
- llama-guardian reachable on port 8080 (do not call llama-server :8081 directly)

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
# Validate env + list models through guardian
npm run build
$env:ACP_QWEN_BASE_URL = 'http://127.0.0.1:8080/v1'
$env:ACP_QWEN_MODEL = 'qwen3.6-35b'
npm run start -- --health

# Later sessions add: npm run start -- --smoke
# Default (no flags): ACP JSON-RPC on stdio (editor launches this)
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `ACP_QWEN_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible base URL |
| `ACP_QWEN_MODEL` | `qwen3.6-35b` | Model id from `GET /v1/models` |
| `ACP_WORKSPACE` | (none) | Absolute workspace root for tools |
| `ACP_QWEN_TIMEOUT_MS` | `120000` | HTTP timeout |
| `ACP_ALLOW_WRITES` | `false` | Master write gate |

## Safety

- Stdout is ACP protocol only. Logs go to stderr.
- Writes are disabled unless `ACP_ALLOW_WRITES=true` **and** the editor approves the exact diff hash.
- v1 tools stay inside `ACP_WORKSPACE` only.

## Non-goals (v1)

No arbitrary shell, no network tools, no git commit/push/delete, no agent-os dependency.
