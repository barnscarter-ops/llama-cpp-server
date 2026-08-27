# Guardian Local Worker Manager

## Intent

Local models are primarily **managed executor workers**, not default main-agent
models. Parent harnesses request a named local-worker capability; guardian
admits and records the job, chooses the configured runner/model policy, and
returns the durable result. A parent never supplies a raw local endpoint,
model alias, or Pi command.

The existing OpenAI-compatible guardian proxy remains for compatibility. It is
not, by itself, subagent admission control.

## Staged implementation

The first implementation lives in `qwen-queue/` and is disabled by default:

| Component | Role |
|---|---|
| `POST /__guardian/workers` | Validate and queue a named worker request. |
| Existing SQLite `guardian_jobs` | Priority/FIFO scheduling, idempotency, restart recovery, result persistence. |
| `guardian_workers.py` | Work-class policy, workspace-root validation, and Pi subprocess runner. |
| `local-worker-mcp.py` | Stdio MCP surface for parent harnesses. |

The two initial local classes are `tool_execution` (the existing Qwen Pi
executor) and `mechanical_execution` (bounded work on Nemotron). `planning` and
`deep_analysis` remain operator/frontier-gated consult capabilities and never
auto-swap. Frontier quality returns `requires_cloud`.

## Control boundary

Each request contains a harness source, parent run ID, validated work class,
bounded preference and quality floor, task, workspace, priority, and timeout.
The workspace must already exist below
`LOCAL_WORKER_ROOT` (default `D:\Workspace`). The runner is always a fixed Pi
command run from that workspace. It is write-capable by design, per Carter's
direction.

This is a scheduling and audit boundary, not a hostile-code sandbox: a
write-capable Pi subprocess has the Windows permissions of its user. A later
sandbox/worktree policy can strengthen isolation without changing the parent
protocol.

## Enablement and rollout

Nothing is live until a watched, approved configuration change sets
`LOCAL_WORKER_ENABLED=true` in the elevated guardian PM2 environment and
performs the required delete-and-start cycle. Do not do that as part of source
authoring or offline verification.

Start with one manual `workbench-executor` job in a disposable repository, then
wire the `local-worker-mcp.py --source grok` and `--source hermes` instances.
Pi can subsequently use its native package path; DeepSeek Harness can add a
native `SubagentProvider`; Codex and Claude can use the same MCP tool unless a
supported native provider override becomes available.
