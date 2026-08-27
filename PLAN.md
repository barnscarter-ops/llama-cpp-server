# PLAN — Guardian-managed local workers

**Workstream:** Local models as controlled subagent workers

**Status:** Planned. Source baseline exists locally and is uncommitted. No
session may enable local workers, modify PM2, swap AIWA, alter LocalLlmBoard,
or change a live harness configuration without the separate watched approval
gate in this plan.

## Goal

Make `llama-guardian` the admission, policy, and scheduling boundary for local
subagent work. Parent runtimes ask for a **work class**, not a model endpoint,
model alias, or arbitrary runner. Guardian maps that class to an approved local
seat/runner, persists the parent-child job relationship, and returns a durable
result.

## Non-negotiable architecture

| Work class | Guardian-controlled route | Rule |
|---|---|---|
| `mechanical_execution` | AIWA clerk / Nemotron | Bounded, write-capable task; no auto-swap. |
| `tool_execution` | Workbench Qwen / Pi executor | Write-capable tool loop; Workbench lifecycle stays guardian-owned. |
| `planning` / `deep_analysis` | AIWA consult / Qwen 3.8 | Never auto-swap. Requires explicit `gate=operator` or `gate=frontier`; reject or remain pending otherwise. |

The caller supplies the work class and bounded task. Guardian owns the profile
mapping. This corrects the staged baseline's caller-selected `profile` design.

## Local-preference policy

Local is the default **worker preference** for explicitly declared,
standard-quality tasks that fit an approved work class; it is never an
unconditional model default. The parent retains responsibility for declaring a
quality floor, and guardian enforces the policy without prompt classification:

| Parent declaration | Guardian action |
|---|---|
| `prefer_local` + standard quality + approved class | Admit the mapped local worker if its seat is healthy and available; otherwise return a typed unavailable result. |
| `require_local` + approved class | Admit only that local worker; never substitute cloud or another seat. |
| Frontier quality, ambiguous architecture, safety-critical, or unsupported work | Return `requires_cloud`; do not spawn a weak local worker and do not proxy to cloud. |
| Planning / deep analysis | Consult only under its existing operator/frontier gate; never auto-swap. |

This policy keeps the local pool busy with the work it is good at while making
quality escalation visible to the parent runtime. It must be calibrated with
measured task outcomes before Grok or Hermes preferentially dispatches broad
classes of work locally.

## Assumptions

- The current uncommitted Python baseline is the starting point and is not
  discarded: it adds `guardian_workers.py`, `local-worker-mcp.py`, worker
  routes in `llama-guardian.py`, documentation, and tests.
- Worker processes may write inside their assigned workspace. This is an
  operational boundary, not a hostile-code sandbox.
- Grok and Hermes are the first parent harnesses. Pi and DeepSeek Harness get
  native integration only after the universal MCP contract is proven. Codex and
  Claude use that same MCP path initially.
- `C:\Workspace\Active\WindowsApps\LocalLlmBoard` is frozen: do not edit,
  start, initialize, commit, or use it as a proxy.

## Global stop conditions

Stop the active session and report rather than improvising if any of these is
true:

- A proposed change needs a PM2 restart/delete-and-start, an AIWA swap, direct
  `.240` access, remote deployment, firewall change, or harness config change
  without Carter's separate watched approval.
- The plan would auto-swap consult, make Qwen 3.8 a general Pi worker, expose
  raw aliases/endpoints to a caller, or bypass guardian.
- The working tree contains unrelated changes that overlap a target file.
- A write-capable worker cannot be bounded to its declared workspace.

## Session 0 — Correct and commit the guarded-worker contract

**Objective:** Independently review the staged baseline, replace
caller-selected profiles with guardian-owned work-class policy, and land a
fully offline-tested source baseline.

**Target files:**

- `qwen-queue/guardian_workers.py`
- `qwen-queue/llama-guardian.py`
- `qwen-queue/local-worker-mcp.py`
- `qwen-queue/test_guardian_workers.py`
- `qwen-queue/test_guardian_queue.py`
- `qwen-queue/README.md`
- `LOCAL-WORKER-MANAGER.md`
- `brain/knowledge/local-llm-architecture.md` (only if its staged wording is
  still accurate after the contract change)

**Tasks:**

1. Review every staged file before editing. Preserve the durable queue's
   existing ordinary-completion behavior.
2. Replace external `profile` selection with a validated `work_class` contract
   and an internal, code-owned policy map. Add bounded `preference` and
   quality-floor inputs; do not surface raw Pi model IDs in the MCP tool
   catalog.
3. Model `planning`/`deep_analysis` as a gate-aware consult capability: no
   automatic `POST /__guardian/swap`, no clerk-to-consult rewrite, and a clear
   typed result when consult is unavailable or the gate is absent.
4. Keep the feature disabled by default. Confirm current PM2 configuration has
   no `LOCAL_WORKER_ENABLED=true` entry.
5. Expand offline tests for invalid classes, direct profile/model attempts,
   workspace-root validation, gate rejection, `requires_cloud`, idempotency,
   and disabled admission.

**Verification:**

```powershell
python -m py_compile qwen-queue\guardian_workers.py qwen-queue\local-worker-mcp.py qwen-queue\llama-guardian.py
python -m pytest qwen-queue\test_guardian_workers.py qwen-queue\test_guardian_queue.py qwen-queue\test_fleet_router.py qwen-queue\test_aiwa_swap.py qwen-queue\test_consult_idle_restore.py qwen-queue\test_guardian_ram_gate.py -q
git diff --check
```

**Acceptance criteria:** all tests pass; no source path can request a raw
model/profile; planning cannot trigger or bypass an AIWA swap; a frontier
quality floor never silently receives a local worker; default process
configuration remains disabled.

**Commit:** `feat(guardian): add policy-managed local worker admission`

## Session 1 — Make worker execution restart-safe and observable

**Objective:** Make the queued write-capable Pi runner safe to supervise before
any harness receives it.

**Target files:**

- `qwen-queue/guardian_workers.py`
- `qwen-queue/guardian_queue.py`
- `qwen-queue/llama-guardian.py`
- `qwen-queue/test_guardian_workers.py`
- `qwen-queue/test_guardian_queue.py`
- `qwen-queue/README.md`

**Tasks:**

1. Design cancellation for a running worker process; queued cancellation must
   retain its present behavior, while an active process receives bounded,
   platform-correct termination and a terminal job state.
2. Prevent guardian restart recovery from silently re-running a potentially
   mutating worker. Mark an interrupted write-capable worker as requiring
   explicit resubmission (or another reviewed, idempotent-safe policy), while
   retaining existing completion-job recovery semantics.
3. Record structured lifecycle evidence: work class, selected internal route,
   parent run ID, workspace, PID/start/finish/cancel reason, and bounded result
   metadata. Never log prompts, secrets, or full model output indiscriminately.
4. Add deterministic subprocess fixtures/tests; tests must never launch Pi or
   call a model.

**Verification:** run the Session 0 suite plus focused cancellation/restart
tests. Inspect the SQLite state transitions directly through the test fixture.

**Acceptance criteria:** no orphan subprocess after timeout/cancel; a guardian
restart cannot duplicate a write job; terminal job states and audit fields are
deterministic; ordinary queue tests remain green.

**Commit:** `feat(guardian): harden managed worker lifecycle`

## Session 2 — Finish the universal MCP contract and prepare Grok/Hermes

**Objective:** Deliver one standards-correct, source-owned MCP adapter and
safe, reviewable configuration instructions for the first two parent harnesses.

**Target files:**

- `qwen-queue/local-worker-mcp.py`
- `qwen-queue/test_local_worker_mcp.py` (new)
- `qwen-queue/README.md`
- `LOCAL-WORKER-MANAGER.md`
- `docs/` or `qwen-queue/harness/` only after inspecting the repository's
  existing documentation layout

**Tasks:**

1. Test JSON-RPC/MCP initialization, `tools/list`, each tool schema, error
   responses, and source attribution without a live guardian.
2. Ensure the tool calls expose only work classes and status/cancel operations;
   no raw alias, direct upstream URL, executable path, or arbitrary command
   argument enters the protocol.
3. Write exact Grok and Hermes configuration fragments/instructions, but do
   **not** edit `~/.grok/config.toml` or Hermes's live config in this session.
4. Include a short parent-agent usage contract: use planning for architecture
   and executor classes for bounded implementation; poll/cancel by job ID;
   never treat an accepted job as completed work.

**Verification:** Python compilation; new MCP protocol tests; existing guardian
suite; manual stdio initialize/tools-list fixture only.

**Acceptance criteria:** adapter output is valid MCP JSON-RPC, all tool inputs
are bounded, source identity is fixed by adapter launch, and no live harness
configuration changes.

**Commit:** `feat(guardian): complete local worker MCP contract`

## Watched operational gate — Disposable workspace smoke (not an executor session)

**Owner:** Carter watching WORKBOARD. This is the first and only point at which
`LOCAL_WORKER_ENABLED=true`, an elevated guardian delete-and-start, or a
workbench model wake may be considered.

**Preflight:** read the current AIWA/guardian handoff, announce on WORKBOARD,
verify no active requests/jobs, record a rollback configuration, and confirm a
throwaway workspace under the configured worker root.

**Smoke:** enable only the reviewed feature flag; submit one small
`tool_execution` job that creates and verifies a disposable text fixture; poll
the durable result; inspect logs, process cleanup, SQLite audit state, and git
status. Disable/restore the flag if the smoke fails. Do not use consult unless
Carter separately approves a watched operator-gated planning test.

**Pass criteria:** one correctly attributed job, no orphan Pi process, no
unexpected AIWA swap, no raw endpoint exposure, and clean rollback evidence.

## Session 3 — Wire Grok and Hermes after the watched smoke

**Objective:** Install the reviewed MCP adapter in the two priority parent
harnesses and give them an explicit, calibrated local-worker preference rather
than replacing their main or native cloud delegation models.

**Discovery targets before edits:**

- `C:\Users\carte\.grok\config.toml`
- `C:\Users\carte\AppData\Local\hermes\config.yaml`
- Existing MCP configuration conventions and launcher paths

**Source/documentation targets:** use the Session 2 discovered source-owned
configuration assets; do not add undocumented one-off launch scripts.

**Tasks:** add one MCP server instance per harness with fixed `--source`
(`grok` / `hermes`); preserve their main and native cloud delegation models.
Add routing instructions that prefer local only for the approved standard
work classes, require a declared quality floor, and escalate visibly when
guardian returns `requires_cloud` or unavailable. Verify each can list
capabilities, submit a disposable job, receive its result, and cannot select a
raw local model.

**Verification:** the watched smoke checklist per harness, plus a restart-free
config parse/launch check. Do not alter Hermes gateway or production profiles.

**Commit:** `chore(harness): wire Grok and Hermes local worker MCP`

## Session 4 — Calibrate the local-preference policy

**Objective:** Establish evidence for when Grok and Hermes should prefer a
local worker, and when they should deliberately keep work cloud-side.

**Target files:** source-owned evaluation fixtures/report location discovered
in Sessions 2–3; `LOCAL-WORKER-MANAGER.md`; guardian policy tests if a policy
threshold changes.

**Tasks:**

1. Assemble a small, representative corpus: bounded mechanical edits, focused
tool execution, code review, architecture/planning, and one known
frontier-quality task. Each item must have objective checks such as test pass,
diff scope, tool completion, or review finding recall.
2. Run the approved local profile and the existing cloud route only where the
task is safe to compare. Record correctness, verification pass rate,
wall-clock time, retries, and escalation reason—not hidden reasoning text.
3. Set the initial preference table from those measurements. Keep ambiguous,
safety-critical, unknown-API, and architecture tasks cloud-first unless
consult is explicitly operator/frontier-gated.
4. Document a re-evaluation trigger: repeated local failures, a new model,
changed context limits, or meaningful runtime integration changes.

**Verification:** all fixtures have reproducible expected outcomes; the
published preference table is traceable to recorded evidence; no benchmark
causes an unattended consult swap or changes production runtime settings.

**Acceptance criteria:** Grok/Hermes can prefer local for a bounded set of
measured task classes, and every out-of-scope/quality-sensitive class has a
clear cloud escalation path.

**Commit:** `docs(guardian): calibrate local worker preference`

## Session 5 — Native Pi and DeepSeek Harness paths

**Objective:** Add native adapters only where the runtime has an explicit,
tested subagent-provider seam; reuse the guardian work-class protocol rather
than duplicate policy.

**Discovery targets before edits:**

- Pi's configured subagent extension and `qwen-executor` package
- `C:\Users\carte\.pi\agent\settings.json`
- DeepSeek Harness `packages/subagent/` and `packages/workflow/` provider
  registration interfaces

**Tasks:** implement a Pi-native work-class mapping and a DeepSeek Harness
`SubagentProvider` adapter after reading their current interfaces; ensure each
passes parent run identity, workspace, cancellation, and a fixed work class to
guardian. Do not invent a provider API from documentation alone.

**Verification:** each runtime's focused test suite plus a watched disposable
smoke after code review. Confirm native paths cannot bypass guardian.

**Commit:** `feat(harness): route Pi and DeepSeek workers through guardian`

## Session 6 — Codex/Claude fallback and bypass-control decision

**Objective:** Finish operator documentation and make an evidence-based choice
about stricter direct-upstream controls.

**Target files:** canonical local-LLM architecture, guardian documentation,
and source-owned harness instructions discovered in earlier sessions.

**Tasks:** document the MCP fallback for Codex and Claude; clearly state that
their native subagents remain cloud unless a supported provider override is
verified. Inventory direct access paths to Workbench `:8081` and AIWA `.240`.
Propose—not deploy—network/process enforcement options with rollback and
compatibility impacts.

**Verification:** documentation links and configuration examples resolve;
guardian behavior and live endpoint policy match the docs; no firewall,
network, or deployment change is made.

**Commit:** `docs(guardian): publish managed local worker rollout`

## Build-handoff execution rule

Before every session: verify `PLAN.md` exists, working tree is clean from the
previous session, the prior verdict is PASS, no unapproved live gate is hidden
inside the session, and the executor receives only a concise instruction to
read this plan and execute the named session. Independently inspect diff,
tests, and commit evidence before proceeding.
