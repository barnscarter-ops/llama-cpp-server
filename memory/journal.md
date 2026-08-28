# llama-cpp-server — Journal

Chronological log of session activity. Append a date-stamped entry at session
close; never rewrite history.

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
