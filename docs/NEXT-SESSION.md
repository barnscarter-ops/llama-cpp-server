# NEXT SESSION — Local worker manager: watched smoke gate

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian-managed local workers (repo `PLAN.md`, Sessions 0–2 done)
**Tip:** `e00a003` (pushed; `main` = `origin/main`)
**Status:** Sessions 0–2 committed (`f105d33`, `fdb303a`, `de73fcd`, `b4b6168`). Pi runtime question closed: `_pi_command()` now resolves Carter's **independent global npm pi 0.84.3** (`%APPDATA%\npm`) + system `node.exe` ahead of the Hermes-managed 0.84.2 fallback (`e00a003`). Verified live: full worker command ran end-to-end through the patched code path, file fixture written and verified, no orphan processes, workspace cleaned.

## Next session

**The watched operational smoke gate** — first enablement of `LOCAL_WORKER_ENABLED=true`. Carter must be watching WORKBOARD. See repo `PLAN.md` section *"Watched operational gate"*. Preflight:

1. Read this file + repo `PLAN.md` gate section.
2. Announce on WORKBOARD (`C:\Workspace\Active\brain\WORKBOARD.md`), verify no active jobs/requests.
3. Record rollback config (current PM2 env; flag absent = rollback = delete env var).
4. Elevated delete-and-re-add of `llama-guardian` with `LOCAL_WORKER_ENABLED=true` — gsudo cmd /c "set PM2_HOME=C:\ProgramData\pm2&& pm2 delete llama-guardian && pm2 start ..." (args changes = delete+re-add, never restart).
5. Fire one request immediately after the guardian restart (idle-timer/reset quirk).
6. Submit one small `tool_execution` job in a disposable workspace under `D:\Workspace`; poll durable result; inspect logs + SQLite audit + git status of the workspace.
7. On pass: disable flag again (or leave on per Carter), record evidence.
8. Then Session 3 (wire Grok + Hermes MCP) becomes eligible.

**Pass criteria:** one correctly attributed job, no orphan pi process, no unexpected AIWA swap, no raw endpoint exposure, clean rollback evidence.

## Shipped this session (2026-08-28)

- `e00a003` — `_pi_command()` prefers global npm pi over Hermes-managed runtime; never routes through `pi.cmd` (cmd.exe shim truncates multi-line prompt args — worker would never see its task; verified empirically). Carried the 5 unpushed worker-manager commits to origin.
- Offline suite: 90 passed (2 new runtime-resolution tests).
- Live seat verification: tg 80.6 t/s (gate 20), VRAM 15.4 GB dedicated + 352 MB shared (no-spill baseline).
- `scripts/gpu-counters.ps1` — both-counter WDDM placement check (inline PowerShell mangles through bash; use the file).
- Carter reinstalled pi as an independent global npm install (0.84.3) — no longer Hermes-node-dependent. Config/auth in `~/.pi` survived.

## Open

- Watched smoke gate (above) — blocked on Carter watching, nothing else.
- `docs/NEXT-SESSION.md` (this file) had been stale since 8/23 — rewritten now; the 8/23 fleet-merge content it held is fully superseded (PR1–PR3 + MCC + fleet defaults + consult gate all merged and live; flags `FLEET_ROUTER=true`, `FLEET_SWAP_OWNER=true`, `FLEET_DEFAULT_SEAT=clerk` confirmed in PM2 dump).
- Sessions 3–6 of worker-manager PLAN (Grok/Hermes wiring, calibration, Pi/DSH native, docs) — after the gate.

## Do not

- Do not enable `LOCAL_WORKER_ENABLED` unattended — watched gate only.
- Do not point guardian at `pi.cmd` or any cmd shim.
- Do not restart PM2 guardian without WORKBOARD announce.
- Do not run consult-class work without operator gate.
- Extra llama on the R9700; clients on `:8081`; rewriting `690-routing/swap-*.sh`.
