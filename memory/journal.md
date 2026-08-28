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
