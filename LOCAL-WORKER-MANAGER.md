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

Watched smoke passed 2026-08-28 (`qj_d52cb887f9ae49db9ef440d322595a9f`); the
PM2 flag was rolled back to off. Grok and Hermes MCP instances are live in
their harness configs (Session 3). DeepSeek Harness has a native
`SubagentProvider` adapter in-repo (Session 5). **Pi native path is skipped**
while Pi orchestrators are paused. Codex and Claude stay on the same MCP
adapter (`--source codex` / `--source claude`). Their **native** subagents
remain cloud unless a DeepSeek-style provider override is verified — none
is, as of Session 6. Fragments and the `:8081` / `.240` inventory:
`qwen-queue/harness/ROLLOUT.md`. Do not live-edit Codex or Claude configs
from this workstream.

## Preference table (Session 4, 2026-08-28)

| Parent declaration | Guardian / parent action | Evidence |
|---|---|---|
| Bounded `tool_execution`, `quality_floor=standard`, `prefer_local` or `require_local` | Admit Workbench Pi runner | Attempt 2: job succeeded ~25s, `SMOKE.txt` correct, source=grok |
| Bounded `mechanical_execution`, standard | Admit AIWA clerk (no swap) | Policy map + Session 4 fixture |
| `planning` / `deep_analysis` without `gate` | `consult_gate_required` (409); no swap | Policy + Session 4 probe |
| `planning` / `deep_analysis` with `gate=operator` or `frontier` | `consult_unavailable` until a watched consult; **never auto-swap** | `guardian_workers.py` |
| `quality_floor=frontier` | `requires_cloud` (409) | Policy + Session 4 probe |
| Unknown API, Swift/iOS, architecture, safety-critical, ambiguous | Keep cloud; do not submit local | MacBridge S2–S10 (GLM-5.3); local 27–30B failed as executors |

Re-evaluate this table when: local jobs fail twice in a row on the same class, the Workbench or AIWA model changes, context limits change, or the guardian runner is switched off Pi.

## Grok/Hermes MCP wiring (live 2026-08-28)

Live files: `%USERPROFILE%\\.grok\\config.toml` and
`%LOCALAPPDATA%\\hermes\\config.yaml`. Main models unchanged (Grok `grok-4.6`,
Hermes `glm-5.3`). Python is the 3.12 install, matching other local MCP
servers — not the `python` Store stub.

Grok TOML:

```toml
[mcp_servers.guardian_local_worker]
command = 'C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe'
args = ['D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py', '--source', 'grok']
```

Hermes YAML:

```yaml
mcp_servers:
  guardian_local_worker:
    command: C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe
    args:
      - D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py
      - --source
      - hermes
    enabled: true
    timeout: 60
```

Parent contract: planning/deep-analysis classes are for architecture and
require their operator/frontier gate; executor classes are for bounded
implementation. Poll or cancel with the returned `job_id`. Accepted means
queued, not complete; only a terminal status with a durable result is
completion evidence. Escalate visibly on `requires_cloud`,
`consult_gate_required`, `consult_unavailable`, or `local_workers_disabled`.
Never pass model, profile, endpoint, runner, or executable.

## Codex / Claude MCP fallback (Session 6, not live)

Native Codex child agents and Claude Agent/Task runs stay on their cloud
defaults (`gpt-5.6-terra`, `claude-fable-5`). Live MCP catalogs are `gws`
(+ Codex `node_repl`) only. A custom OpenAI-compatible `base_url` aimed at
`:8080` or `:8081` is **not** a supported worker override.

Install these fragments only in a watched harness-config change. Python and
adapter paths match the live Grok/Hermes rows.

Codex (`%USERPROFILE%\\.codex\\config.toml`):

```toml
[mcp_servers.guardian_local_worker]
command = 'C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe'
args = ['D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py', '--source', 'codex']
```

Claude Code (add the key under `mcpServers` in `%USERPROFILE%\\.claude.json`;
do not replace the object):

```json
"guardian_local_worker": {
  "command": "C:\\Users\\carte\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
  "args": [
    "D:\\Workspace\\Infrastructure\\llama-cpp-server\\qwen-queue\\local-worker-mcp.py",
    "--source",
    "claude"
  ]
}
```

## Direct upstreams and enforcement (Session 6, not deployed)

Live: guardian `:8080` ok, `:8081` not listening, AIWA `.240` clerk reachable,
workers off. `:8081` already binds loopback. `.240` binds `0.0.0.0` with no
API key — that is the remaining completions bypass, and also Board
`SeatProbe` + operator rollback.

**Do not deploy firewall, nftables, API keys, or AppLocker from this
plan.** Recommended stay: current binds + policy + the existing
single-llama enforcer. Optional later (watched): `:8081` API key, or an
AIWA POST allowlist that still permits Board GET and rollback curls.
Full table, rollback, and compatibility: `qwen-queue/harness/ROLLOUT.md`.
