# Local-model queue

Everything for the Hermes-decided local-model coding queue, consolidated 2026-07-22.

## Contents

| File | Role |
|------|------|
| `llama-guardian.py` | Gateway on port 8080. Proxies to llama-server (8081), owns llama lifecycle, hosts the job queue API (`/__guardian/jobs`). Runs under PM2 as `llama-guardian` (path registered in `ecosystem.config.cjs` and the PM2 dump at `C:\ProgramData\pm2`). |
| `guardian_queue.py` | Queue logic imported by the guardian. State persists in `..\logs\guardian-queue.sqlite3` (path derived from `__file__/../../logs` — keep this folder one level below the repo root). |
| `test_guardian_queue.py` | Queue tests (`python -m pytest test_guardian_queue.py`). |
| `qwen-submit.ps1` | Client: submits a task (+ context files) to the queue. |
| `guardian_workers.py` | Guardian-owned work-class policy and Pi runner. Disabled unless `LOCAL_WORKER_ENABLED=true`. |
| `local-worker-mcp.py` | Stdio MCP adapter for Grok, Hermes, Codex, Claude, Pi, and DeepSeek Harness. |
| `qwen-context.ps1` | Selects `-ContextFiles` for a task by querying code-review-graph. |
| `code-review-graph/` | Clone of [tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) with its own `.venv` (editable install). Git-ignored by the parent repo. |

## Workflow

```powershell
# once per repo (and `update` after big changes)
& .\code-review-graph\.venv\Scripts\code-review-graph.exe build --repo <repo>

# per task
$ctx = & .\qwen-context.ps1 -Query "keyword1","keyword2" -Repo <repo>
& .\qwen-submit.ps1 -Task "<task>" -Source claude -ContextFiles $ctx -Wait
```

Graph data lives in `<repo>\.code-review-graph\` (self-git-ignored by the tool).
Search is FTS keyword-based — use code-ish terms ("stale", "guardian_queue"),
not sentences. Hermes-Supervisor graph is built; build others as needed.

## Guardian-managed local workers

### Versioned policy descriptor (local source; not deployed by this change)

`GET /__guardian/workers/descriptor/v1/{work_class}` adds a separate read-only
`GuardianWorkerDescriptor.v1` response for `mechanical_execution` and
`tool_execution`, under the existing queue authorization check. Existing
`GET/POST /__guardian/workers` bodies and behavior remain unchanged. This
handler never probes, wakes, refreshes a cache, submits a job or reserves a seat.

`policyRevision` is SHA256 of sorted-key compact ASCII JSON for the included
`GuardianWorkerPolicy.v1` **non-secret projection**. It binds work class,
configured provider/model alias, fixed Pi runner, documented paused status,
egress and unknown capability evidence. It is not a digest of private skill
contents, executable configuration or an authorization receipt. No endpoint,
credential, executable path or skill path is returned. Changing supported
runner/route semantics requires deliberate source contract revision; a new
operator flag cannot clear the descriptor's Pi pause.

`observedAt` records descriptor generation. AIWA readiness retains the existing
occupant cache's original observation time without fetching; Workbench's cached
health boolean has no measured timestamp or exact served-model identity, so
those fields are null. Cached reachability is diagnostic and is never capacity
or a reservation. Capability evidence, entitlement, transport and capacity
remain unknown/unavailable; execution and reservation flags are false even if
the existing worker enablement flag is true. The `origin` label and policy hash
authenticate nobody. The Core normalizer preserves these limits and V1 cannot
produce execution permission or even a complete policy-ready observation.

Offline verification: `python -B -m unittest test_guardian_worker_descriptor
test_guardian_workers` from this directory. Handler tests compile only the
actual handler definitions with inert dependencies, avoiding daemon startup
and durable queue access. Core's composition test runs this actual pure Python
producer before strict TypeScript normalization. No live service observation
or inference is implied by these tests.

The Core composition test requires Python and this source checkout at the
workspace's sibling `Infrastructure/llama-cpp-server` path. It intentionally
fails if the actual producer is absent; a copied JSON fixture is not a
substitute for cross-language composition.

`/__guardian/workers` is the local-subagent admission API. A caller requests a
validated `work_class` (`mechanical_execution` or `tool_execution` for local
workers), bounded `preference` and `quality_floor`, a task, and an existing
workspace under `LOCAL_WORKER_ROOT` (default `D:\Workspace`). Guardian owns the
route and fixed Pi runner. Planning/deep-analysis requests are gate-aware and
return typed gate/unavailable responses; frontier quality returns
`requires_cloud`. The caller never supplies a profile, endpoint, model alias,
or runner command.

The feature defaults **off** (`LOCAL_WORKER_ENABLED=false`). Watched smoke
passed 2026-08-28 and the flag was rolled back. Starting a worker may wake the
Workbench model; do not enable it unattended.

Worker lifecycle safety: queued cancellation remains a cheap terminal
`cancelled` transition. A running worker is cancelled by terminating its full
process tree with the host platform's process primitive, then recording a
terminal `cancelled` row and bounded lifecycle evidence (work class, route,
parent run, workspace, PID, timestamps, and cancel reason). A guardian restart
never replays a running write-capable worker; it marks that row failed with an
explicit-resubmission error. Ordinary completion jobs retain restart requeue
recovery. Worker audit records contain metadata only—never prompts, secrets, or
unbounded model output.

For an MCP-capable parent runtime, configure a stdio server like:

```text
python D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py --source grok
```

Use one instance per harness, changing only `--source` (`grok`, `hermes`, `pi`,
`deepseek-harness`, `codex`, or `claude`). The exposed tools submit, poll, and
cancel durable worker jobs.

### Grok and Hermes configuration (live 2026-08-28)

Main models unchanged. See `LOCAL-WORKER-MANAGER.md` for the live fragments
and the Session 4 preference table. DeepSeek native adapter:
`qwen-queue/harness/deepseek-guardian-provider.mjs`. Pi native path skipped.

### Codex and Claude (MCP fallback; not live-installed)

`--source codex` and `--source claude` are valid adapter launches. Native
Codex/Claude subagents remain cloud unless a supported provider override is
verified (none is). Fragments, bypass inventory (`:8081` / `.240`), and the
propose-only enforcement decision: `qwen-queue/harness/ROLLOUT.md`.

Parent contract: use `planning`/`deep_analysis` for architecture only with an
explicit gate; use executor classes for bounded implementation. Poll or
cancel by `job_id`. Accepted means queued, not complete; completion requires
a terminal status and durable result.

## Moved from `scripts\` (2026-07-22)

The four queue files above previously lived in `scripts\`. Updated references:
`ecosystem.config.cjs` (guardian script path), PM2 dump (re-registered + saved),
and the qwen-submit path in `~\.claude\CLAUDE.md`.
