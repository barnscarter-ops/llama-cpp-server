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

`/__guardian/workers` is the local-subagent admission API. A caller requests a
validated `work_class` (`mechanical_execution` or `tool_execution` for local
workers), bounded `preference` and `quality_floor`, a task, and an existing
workspace under `LOCAL_WORKER_ROOT` (default `D:\Workspace`). Guardian owns the
route and fixed Pi runner. Planning/deep-analysis requests are gate-aware and
return typed gate/unavailable responses; frontier quality returns
`requires_cloud`. The caller never supplies a profile, endpoint, model alias,
or runner command.

The feature defaults **off** (`LOCAL_WORKER_ENABLED=false`) and no current PM2
environment enables it. Starting a worker may wake the Workbench model, so do
not enable or test it against live inference without Carter's approval.

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

## Moved from `scripts\` (2026-07-22)

The four queue files above previously lived in `scripts\`. Updated references:
`ecosystem.config.cjs` (guardian script path), PM2 dump (re-registered + saved),
and the qwen-submit path in `~\.claude\CLAUDE.md`.
