# qwen-queue

Everything for the Hermes-decided Qwen coding queue, consolidated 2026-07-22.

## Contents

| File | Role |
|------|------|
| `llama-guardian.py` | Gateway on port 8080. Proxies to llama-server (8081), owns llama lifecycle, hosts the job queue API (`/__guardian/jobs`). Runs under PM2 as `llama-guardian` (path registered in `ecosystem.config.cjs` and the PM2 dump at `C:\ProgramData\pm2`). |
| `guardian_queue.py` | Queue logic imported by the guardian. State persists in `..\logs\guardian-queue.sqlite3` (path derived from `__file__/../../logs` — keep this folder one level below the repo root). |
| `test_guardian_queue.py` | Queue tests (`python -m pytest test_guardian_queue.py`). |
| `qwen-submit.ps1` | Client: submits a task (+ context files) to the queue. |
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

## Moved from `scripts\` (2026-07-22)

The four queue files above previously lived in `scripts\`. Updated references:
`ecosystem.config.cjs` (guardian script path), PM2 dump (re-registered + saved),
and the qwen-submit path in `~\.claude\CLAUDE.md`.
