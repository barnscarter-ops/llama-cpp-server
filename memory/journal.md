# llama-cpp-server — Journal

Chronological log of session activity. Append a date-stamped entry at session
close; never rewrite history.

<!-- 2026-08-28 (later, Hermes TUI default profile) — guardian control-plane audit.
Swept all Hermes profiles/skills/env/cron/bots/MCP for llamacpp routing or
guardian-override paths. Findings in `harness routing/Hermes.md` (new folder,
committed): ungated qwen-llamacpp provider entries in default+mav-room configs;
prefer-local doctrine in external ~/.agents/skills orchestration skills; stale
GLM fallback line in ~/.claude/CLAUDE.md; pi llamacpp provider dormant (pi
paused). Delegation/aux/moa/env/cron verified clean. Deleted omp profile
entirely (pre-delete archive in ~/Documents/hermes-profile-backups/), mav-room
untouched per Carter. Open decisions Q1–Q5 in the report; Q4 (mav-room MCP
gap) deferred by Carter. -->

<!-- 2026-08-28 session close — Grok. PLAN Sessions 0–6 complete. Session 6
commit b5eee2b docs(guardian): publish managed local worker rollout (Codex/
Claude MCP fragments not live; native subagents stay cloud; :8081 down;
.240 clerk reachable; enforcement proposed not deployed). Live: guardian
ok, workers off, llama_up=false, AIWA clerk. WORKBOARD parked + 2026-08-27
690 swap ANNOUNCE marked done. No next executor job queued. Inbox:
brain/inbox/2026-08-28-llama-worker-plan-complete.md. -->

<!-- 2026-08-27 — repo doc structure standardized (AGENTS.md contract, docs/NEXT-SESSION.md, journal.md). See AGENTS.md. -->

<!-- 2026-08-28 — Chief (session restart prep). Context: Carter reinstalled pi as an
independent global npm install (0.84.3); it had been living under Hermes-managed
nodes the whole time. Tasked with verifying the new install doesn't break the
guardian worker runner (commit b4b6168 had pinned resolution to the Hermes-managed
copy).

What shipped:
- e00a003 "fix(guardian): prefer independent global npm Pi over Hermes-managed
  runtime" — _pi_command() resolution order is now: LOCAL_WORKER_PI_EXE env →
  %APPDATA%\npm global install + Program Files node.exe → Hermes-managed
  %LOCALAPPDATA%\hermes\node fallback → PATH. Pushed; carried the 5 sitting
  worker-manager commits (f105d33..b4b6168) to origin. main = origin/main.
- Empirical finding: pi.cmd shims truncate multi-line prompt arguments (worker
  got only the preamble, never its task). Direct node.exe cli.js passes multi-
  line prompts intact. Guardian invokes node.exe directly, so unaffected — but
  never point the runner at a .cmd shim. Recorded in the commit message.
- Offline suite: 90 passed (added global-preference + Hermes-fallback tests).
- Live verification while GLM was awake: full worker command E2E through the
  patched code path (validate_worker_submission → worker_command → _pi_command
  → pi 0.84.3 → local model → file written+verified); seat health tg 80.6 t/s
  (gate 20); VRAM both-counters 15.4 GB dedicated + 352 MB shared = no-spill
  baseline. scripts/gpu-counters.ps1 added (both-counter WDDM placement check).
- docs/NEXT-SESSION.md was 5 days stale (still described the 8/23 fleet-merge
  state); fully rewritten around the local-worker-manager workstream.

What's left:
- Watched smoke gate for LOCAL_WORKER_ENABLED=true (Carter watching WORKBOARD)
  — the next session's entire job. Full preflight in docs/NEXT-SESSION.md.
- Worker-manager PLAN Sessions 3–6 after the gate.
- pi runtime resolution is source-only; the running guardian process was started
  before e00a003 — harmless while LOCAL_WORKER_ENABLED is absent (runner never
  invoked), but the watched gate's delete-and-re-add will pick it up anyway. -->

<!-- 2026-08-28 session close — Grok. Close docs in this commit. Live:
guardian ok, workers_enabled=false, llama_up=false, AIWA clerk. Handoff:
docs/NEXT-SESSION.md (Session 6). Inbox:
brain/inbox/2026-08-28-llama-worker-s3-s5-close.md. WORKBOARD parked. -->

<!-- 2026-08-28 (Grok) — Sessions 3–5. S3: Grok+Hermes MCP live in
config.toml / config.yaml (default models unchanged). Stdio initialize,
tools/list, reject raw model. Live MCP jobs: grok
qj_3e8a4e29129a4a718d5550e7ca8f652a GROK_MCP.txt=S34_GROK_OK; hermes
qj_c926913dc0db46b98d7329a6aa42d62b HERMES_MCP.txt=S34_HERMES_OK. S4:
planning 409 consult_gate_required; gated planning 503 consult_unavailable;
frontier 409 requires_cloud; AIWA stayed clerk. Preference table in
LOCAL-WORKER-MANAGER.md + qwen-queue/harness/CALIBRATION.md. S5: DeepSeek
SubagentProvider adapter (fixed tool_execution, parent cwd + run id, cancel);
Pi native skipped. Flag rolled back; local-llm stopped. Session 6 remains. -->

<!-- 2026-08-28 (Grok) — Watched smoke attempt 2 PASS, then rolled back.
Operator: this Grok session (no Pi orchestrator). Elevated pm2 jlist showed
llama-guardian online, flag absent. Delete-and-re-add with
LOCAL_WORKER_ENABLED=true (temp ecosystem line, reverted before any commit;
pm2 save). GET /__guardian/workers enabled=true. One tool_execution job
qj_d52cb887f9ae49db9ef440d322595a9f source=grok require_local standard in
D:\Workspace\tmp\guardian-smoke-2026-08-28; 202 queued → succeeded in ~25s;
SMOKE.txt == GATE_ATTEMPT_2_OK; worker pid 19984 dead; AIWA stayed clerk;
no model/profile/endpoint in the stored worker spec. Flag rolled back
(delete-and-re-add from clean ecosystem + pm2 stop local-llm + save).
Post-rollback: workers_enabled=false, llama_up=false, :8081 down, :8080
pid 36156, RAM ~43.3 GB. Session 3 (Grok+Hermes MCP) is eligible. Pi
orchestrators remain paused; the guardian Pi runner worked for this one
watched job. -->

<!-- 2026-08-28 (Grok pickup) — Pi agents paused; attempt-1 attribution corrected.
Carter: attempt 1 was Hermes GLM-5.3, not Codex. Hermes launched Pi because
build-handoff/orca-cli default to `pi --provider …`; leftover process was
`pi --provider llamacpp --model local-llm` (circular guardian kill). Pi also
has an install-location split (independent global npm vs Hermes-managed;
pi.cmd truncates multi-line prompts) found earlier tonight.
Standing rule saved: do not spawn Pi; all agent work → Hermes. Guardian
source still names Pi as the worker subprocess — LOCAL_WORKER_ENABLED stays
off until Pi is unpaused or that runner is switched. Watched gate not run. -->

<!-- 2026-08-28 (later, close #2) — Chief. Gate attempt 1 aborted by Carter during
preflight; stack verified healthy after (guardian ok, local-llm online, flag absent).
Postmortem documented in docs/NEXT-SESSION.md: (1) PM2 resurrect had left
llama-guardian STOPPED while a stale detached process served :8080 — health 200
did not prove PM2 registration; recovered + pm2 save. (2) Executor launched on
llamacpp/local-llm — behind the guardian it manages — pm2-stop'd its own brain
mid-turn; ops executors must run off-stack (NIM/cloud). (3) TUI spinner opacity
on NIM free tier gave Carter no visibility → stopped. Launch recipe for attempt 2
written: pm2 jlist assert first, off-stack executor, per-step WORKBOARD/logging,
explicit "no PM2 stops except the prescribed re-add" constraint.
Also shipped: pi models.json local-llm entry was stale GLM-4.7-Flash/49152 →
Qwen3.6-35B-A3B/131072 (why Orca showed 49k); vault pi AGENTS.md bootstrap
updated (AIWA rename + seat facts); executor 'nul' reserved-name residue deleted
via Win32 DeleteFileW. Two pre-existing "Pi ready" terminals closed in error
during cleanup — flagged. Tree clean at 21f460d; nothing to commit. -->

## 2026-09-01 — served-model of record + consult gate header (Maverick Core v1, W1-A)

- `af4bdab` (worker W1-A, pi/deepseek-v4-flash, reviewed by the coordinator; 101 pytest green): `X-Guardian-Seat` / `X-Guardian-Served-Model` / `X-Guardian-Request-Id` on every proxied completion (GLM + clerk/consult, stream + non-stream; seat-only on resolved-seat errors); per-seat `served` counters + `served_total` on `/__guardian/health` and `/__guardian/seats`; `X-Guardian-Gate: operator|frontier` (or `?gate=`) on a consult request while clerk occupies runs the same swap as `/__guardian/swap`; ungated consult still downgrades to clerk. `ecosystem.config.cjs` untouched.
- **NOT yet applied to the live PM2 `llama-guardian`** (2026-09-01): the coordinator's elevated `pm2 restart llama-guardian` was blocked by the session's permission gate for process restarts. Apply when convenient (queue was idle at the time): gsudo `set PM2_HOME=C:\ProgramData\pm2 && pm2 restart llama-guardian && pm2 save`, then confirm `/__guardian/health` shows `served_total`.
