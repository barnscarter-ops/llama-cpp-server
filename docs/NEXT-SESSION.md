# NEXT SESSION — Watched smoke gate (attempt 2): executor launch lessons applied

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian-managed local workers (repo `PLAN.md`, Sessions 0–2 done)
**Tip:** `21f460d` (pushed; `main` = `origin/main`)
**Status:** Gate **not yet run**. First launch attempt aborted by Carter during preflight. Stack verified healthy after: guardian `ok` on :8080, `local-llm` online, `LOCAL_WORKER_ENABLED` **absent** from PM2 env (confirmed 2026-08-28 ~01:5x). WORKBOARD row reads "STOPPED BY CARTER — gate not run."

## The gate itself (unchanged)

Enable `LOCAL_WORKER_ENABLED=true` via elevated PM2 delete-and-re-add, one disposable `tool_execution` job, verify pass criteria, rollback evidence. Full preflight below still applies. Carter must be watching WORKBOARD.

## Attempt-1 postmortem — four launch traps (2026-08-28)

1. **PM2 resurrect had left `llama-guardian` STOPPED** in the daemon while a stale detached guardian process still served :8080. Health checks passed until the stale process died mid-session → pi got connection errors. **Trap: `/__guardian/health` 200 does not prove PM2 registration is live. Always `pm2 jlist` first.** (Fixed that night: started + `pm2 save`.)
2. **Circular executor dependency — the big one.** The executor was launched on `llamacpp/local-llm` (behind the very guardian it manages). Its first elevated-access test was `pm2 stop llama-guardian` → severed its own brain mid-turn. **Never run a stack-ops executor on the stack being operated.** Use NIM/cloud for the gate executor.
3. **Executor improvisation:** `pm2 stop` as an access test is NOT in the preflight — the prompt must explicitly forbid stopping/restarting guardian except at the prescribed delete-and-readd step.
4. **Opacity:** pi TUI spinner hides all progress for minutes (NIM free tier ≈ 40 RPM, slow). Carter couldn't see what it was doing → stopped it. **Requirement: live-visible progress.**

## Launch recipe for attempt 2 (changes only)

- Executor: `pi --provider nvidia-nim --model nvidia/nemotron-3-ultra-550b-a55b` (or another cloud model) in an Orca terminal titled `<workstream> — gate executor`. NEVER `llamacpp/local-llm` or `llamacpp-690` for this gate.
- Before sending the prompt: `pm2 jlist` → assert `llama-guardian` online; if stopped, `pm2 start llama-guardian && pm2 save` first.
- Prompt additions: "Do not stop, restart, or delete any PM2 process except the single prescribed llama-guardian delete-and-re-add step. After each preflight step, append one line to WORKBOARD (or a log file the operator can tail) before proceeding."
- Alternative shape if TUI opacity remains a problem: run pi in `--print` mode from a script, tee output to `logs/gate-attempt2.log`, and tail it — transcript visible without Orca TUI.

## Preflight (unchanged)

1. Read this file + repo `PLAN.md` gate section.
2. Announce on WORKBOARD (`C:\Workspace\Active\brain\WORKBOARD.md`), verify no active jobs/requests.
3. Record rollback config (current PM2 env; flag absent = rollback = delete env var).
4. Elevated delete-and-re-add of `llama-guardian` with `LOCAL_WORKER_ENABLED=true` — gsudo cmd /c "set PM2_HOME=C:\ProgramData\pm2&& pm2 delete llama-guardian && pm2 start ..." (args changes = delete+re-add, never restart). Then `pm2 save`.
5. Fire one request immediately after the guardian restart (idle-timer/reset quirk).
6. Submit one small `tool_execution` job in a disposable workspace under `D:\Workspace`; poll durable result; inspect logs + SQLite audit + git status of the workspace.
7. On pass: disable flag again (or leave on per Carter), record evidence.
8. Then Session 3 (wire Grok + Hermes MCP) becomes eligible.

**Pass criteria:** one correctly attributed job, no orphan pi process, no unexpected AIWA swap, no raw endpoint exposure, clean rollback evidence.

## Shipped this session (2026-08-28, close #2)

- **pi local-seat metadata fixed**: `~/.pi/agent/models.json` `llamacpp.local-llm` was still GLM-4.7-Flash **49152** — updated to Qwen3.6-35B-A3B **131072** (matches live server, verified via `/v1/models`). This is why Orca showed the local seat limited to 49k.
- **Vault pi bootstrap updated** (`~/.pi/agent/AGENTS.md`): AIWA rename (was "the 690"), local seat now "Qwen3.6-35B-A3B, 131k ctx since 2026-08-26", guardian path noted.
- Guardian recovered + PM2 dump saved after resurrect-left-stopped discovery.
- Executor residue cleaned: `nul` reserved-name file deleted (Win32 `DeleteFileW` with `\\?\` prefix — MSYS/PowerShell layers can't touch it; `os.path.exists('...nul')` always lies).
- Two pre-existing "Pi ready" Orca terminals were closed in error during cleanup (flagged to Carter; recreate if needed).

## Do not

- Do not run the gate executor on any `llamacpp` provider (circular kill — proven the hard way).
- Do not enable `LOCAL_WORKER_ENABLED` unattended — watched gate only.
- Do not point guardian at `pi.cmd` or any cmd shim (truncates multi-line prompts).
- Do not restart PM2 guardian without WORKBOARD announce.
- Extra llama on the R9700; clients on `:8081`; rewriting `690-routing/swap-*.sh`.
