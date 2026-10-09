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

## Guardian dispatcher (in progress, all flags default off)

Build plan: `guardian-dispatcher-PLAN.md` (project files). Each step is additive and behind a flag.

- **Step 1 — model registry.** `model_registry.py` + `models.json` list every model Guardian could use (egress class, cost, context, quality tier, concurrency). `GET /__guardian/models` (loopback or bearer, like `/__guardian/seats`) returns the registry plus live readiness: `ready`, `wakeable(seconds)`, `needs_approval`, `unavailable(reason)`. Workbench readiness reads in-process state only and never calls llama-server on 8081, so reading it cannot wake the model. Override the file with `GUARDIAN_MODELS_FILE`. No flag: nothing else imports the registry yet.
- **Step 2 — per-model slots (`GUARDIAN_MODEL_SLOTS=true`, default off).** `model_slots.py` gives each registry model a concurrency slot and a swap gate per host. Direct proxy calls and queued jobs both take the slot, including the R9700 path that had no lock. A swap (`aiwa_swap.perform_swap`) takes the whole R9700 exclusively: it waits for in-flight work to finish (`GUARDIAN_SWAP_DRAIN_TIMEOUT_S`, default 900, then 409 `aiwa_busy` without touching the running request) and blocks new R9700 work until the swap ends. Workbench keeps `generation_lock`; the slot is taken outside it.
- **Step 3 — per-model queues (`GUARDIAN_PER_MODEL_QUEUES=true`, default off).** Jobs carry an additive `model_id` column (existing rows are backfilled at startup; the migration is idempotent). With the flag on there is one worker per local registry model, each claiming only its own model's jobs, so a long R9700 job no longer blocks a Workbench job. Durability, requeue-on-restart, idempotency keys, priority, cancel and the status endpoints are unchanged. `GET /__guardian/queues` (loopback or bearer) shows depth, running job and oldest wait per model. Cloud-model workers arrive with the cloud backend (step 8).
- **Step 4 — candidate filter.** `candidates.py` `build_candidates(registry_state, clearance, cost_ceiling, task_limits)` is a pure function: it drops models whose egress is wider than the job's clearance (`pc` < `lan` < `internet`), unavailable models, models with too small a context, and models whose worst-case cost exceeds the ceiling. A missing or unrecognized clearance means `pc` (Chief's `none` is accepted as `pc`); a missing ceiling means 0 (so paid models are never offered unasked). `needs_approval` models stay in, flagged. Nothing calls it until step 6.
- **Step 5 — Qwen only on approval (`GUARDIAN_QWEN_APPROVAL=true`, default off; implies the slot guard).** Nemotron is the resting model. Qwen loads only inside an approved session (`qwen_session.py`): hold the R9700 exclusively, drain the running Nemotron task (never killed; `GUARDIAN_SWAP_DRAIN_TIMEOUT_S`), consume the approval, load Qwen, run the task, then reload Nemotron on success, failure or cancel, and only then let queued Nemotron work resume. Approvals are rows in `qwen_approvals` (`pending|approved|denied|used|expired`, TTL `GUARDIAN_QWEN_APPROVAL_TTL_S`, default 1800 s): `GET /__guardian/approvals?status=pending`, `POST /__guardian/approvals/{id}/decide` with `{"decision":"approve|deny","decided_by":"..."}`. Deciding needs `Authorization: Bearer $GUARDIAN_QWEN_APPROVAL_TOKEN` even from loopback (a token separate from the queue token; unset = deciding disabled, 503), and an approval is bound to the task it was issued for (job idempotency key, `X-Guardian-Task-Id`, or a hash of the body) so another task cannot spend it (`403 approval_wrong_task`). An unapproved Qwen request or job gets `409 needs_approval` with the approval id (no more silent downgrade to Nemotron); pass the id back as `X-Guardian-Approval-Id` (direct) or `approval_id` (job). `gate=operator|frontier` and `POST /__guardian/swap {to: consult}` no longer authorize a swap. At startup a Guardian that finds Qwen loaded reloads Nemotron. If the Nemotron reload fails, `/__guardian/health` reports `nemotron_reload_failed` (+ `_since`, `_retries`) and a background loop retries every 60 s until it works; a second cancel during a reload cannot release the R9700 lock early. `CONSULT_IDLE_RESTORE_S` is untouched (its loop is skipped while the flag is on).
- **Step 6 — task API (`GUARDIAN_TASK_API=true`, default off).** `POST /__guardian/tasks` takes `task_id`, `idempotency_key`, `summary`, `messages` or `prompt`, `clearance` (`pc|lan|internet`, default `pc`), `cost_ceiling_usd` (default 0), `quality_floor` (1-3), `priority`, and no model. Guardian reads every model's readiness, filters by clearance/cost/context (`candidates.py`), lets the selector (`RulesSelector`: ready local cheapest first, then ready cloud, then wakeable, approval-gated Qwen last) choose, re-checks the pick, and enqueues on that model's queue. One re-selection if the pick went down; then `422 selection_failed` with a reason. `GET /__guardian/tasks/{id}` returns status, the job result and an evidence object (`chosen_model`, `selector`, `candidates_offered`, `selector_answer`, `readiness_at_pick`, `readiness_at_dispatch`, `queue_wait_ms`, `fallback_used`, `served_model`). A Qwen pick becomes `awaiting_approval` and is enqueued when the approval is decided. Models Guardian cannot actually run are marked unavailable: R9700 models when `FLEET_ROUTER` is off, Qwen when `GUARDIAN_QWEN_APPROVAL` is off, and every cloud model until the cloud backend exists (step 8). `/v1/chat/completions` is unchanged. An approval records its scope (clearance and cost ceiling, returned as `approval_scope`); a task only spends an approval whose scope equals its own, else it fails `approval_scope_mismatch`. Input tokens are estimated pessimistically (2 bytes per token) from the real message text.
- **Step 7 — Jev selects the model (`GUARDIAN_SELECTOR=jev`, default `rules`).** `jev_selector.py` plugs into the task API: candidates (already filtered for clearance and availability) go to Jev as `JevPickRequest`-style profiles plus a task summary (cut to 2000 chars; messages never leave). The answer must be one offered id; abstain, timeout (`GUARDIAN_JEV_TIMEOUT_S`, default 10), HTTP error, bad shape, cap reached or low confidence (`GUARDIAN_JEV_MIN_CONFIDENCE`, default 0) all end in `selection_failed` with a `jev_*` reason and no rules fallback. Spend: UTC-day reservation ledger `jev_daily_spend`, cap `GUARDIAN_JEV_DAILY_CAP_USD` (default 2.00). Key: `GUARDIAN_JEV_API_KEY` env override, else one Secret Manager version via gcloud (`GUARDIAN_JEV_SECRET_REF`, `GUARDIAN_JEV_SECRET_PRINCIPAL`); never logged. If the flag is on but no key source exists, tasks fail with `jev_reference_missing`. Jev is asked only when the task carries `summary_cleared_for_jev: true` (Chief sets it; default false); otherwise the pick fails `jev_summary_not_cleared` without a key load, spend or network call. Every summary also goes through `summary_redaction.py` (keys, bearer/basic strings, private key blocks, labelled secrets, URL credentials, phones, emails, IPs, long hex/base64) before it leaves, and a summary that is nothing but redactions is not sent. A spend ledger that cannot be opened or written is a `jev_ledger_unavailable` selection failure, not a 500.
- **Step 8 — cloud dispatch and key custody (`GUARDIAN_CLOUD=true`, default off).** `cloud_backend.py` serves `deepseek-flash` (direct DeepSeek, no aggregator) with the key held only by Guardian: `GUARDIAN_DEEPSEEK_API_KEY`, else a Secret Manager version via `GUARDIAN_DEEPSEEK_API_KEY_SECRET_REF` + `..._SECRET_PRINCIPAL`. It clamps output tokens (`GUARDIAN_CLOUD_MAX_OUTPUT_TOKENS`, 8192), caps request size (256 KB), limits calls per window (`GUARDIAN_CLOUD_MAX_CALLS_PER_WINDOW` 200 per `GUARDIAN_CLOUD_WINDOW_S` 3600), honors `max_concurrent` (`GUARDIAN_CLOUD_MAX_CONCURRENT`, 2), and keeps a per-model UTC-day spend ledger (`GUARDIAN_CLOUD_DAILY_CAP_USD`, 5.00): the worst case is reserved before a call and settled to real usage after. With the flag on, the old `seat == cloud` 409 becomes routing: direct `/v1/chat/completions` (non-streaming; busy answers 429 `cloud_busy`), `/__guardian/jobs` (fleet router on) and the task API all reach it, and a model with less headroom than one full-length reply reads `unavailable(spend_cap)`. A queued cloud job with the flag off fails instead of reaching the local model.
