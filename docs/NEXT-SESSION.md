# NEXT SESSION — Session 6: Codex/Claude fallback + bypass-control

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian-managed local workers
**Status:** Sessions 0–5 done except **Pi native skipped**. Watched smoke PASS. Flag **off**. Pi orchestrators still paused.

**Session 6:** document MCP fallback for Codex/Claude; inventory `:8081` and `.240` bypass paths; propose (do not deploy) enforcement. No firewall change.

## Attempt 2 evidence (PASS)

- Operator: Grok. Elevated `pm2 jlist` first (`PM2_HOME=C:\ProgramData\pm2`): `llama-guardian` online, flag absent; `local-llm` stopped.
- Enable: delete-and-re-add from `ecosystem.config.cjs --only llama-guardian` with a temporary `LOCAL_WORKER_ENABLED=true` line (reverted before any commit) + `pm2 save`. `/__guardian/workers` → `enabled: true`.
- Job `qj_d52cb887f9ae49db9ef440d322595a9f`: `POST /__guardian/workers` 202, source=`grok`, `work_class=tool_execution`, `require_local`/`standard`, workspace `D:\Workspace\tmp\guardian-smoke-2026-08-28`. queued → running → **succeeded** (~25s worker). `SMOKE.txt` = `GATE_ATTEMPT_2_OK`. Route `workbench-executor`. Worker pid 19984 dead after. AIWA occupant stayed **clerk**. Stored worker spec had no profile/model/endpoint/runner/executable.
- Rollback: delete-and-re-add from clean ecosystem (no flag) + `pm2 stop local-llm` + `pm2 save`. Confirmed: `workers_enabled=false`, `llama_up=false`, `:8081` down, `:8080` pid 36156, RAM ~43.3 GB.

## Sessions 3–5 (done this turn)

- S3: Grok + Hermes MCP live; stdio + live jobs `qj_3e8a4e29129a4a718d5550e7ca8f652a` / `qj_c926913dc0db46b98d7329a6aa42d62b`.
- S4: `qwen-queue/harness/CALIBRATION.md`. Planning 409/503, frontier 409, AIWA clerk.
- S5: DeepSeek `SubagentProvider` in `qwen-queue/harness/deepseek-guardian-provider.mjs`. **Pi native skipped.**

## Attempt-1 postmortem (keep)

1. Health 200 ≠ PM2 live — always elevated `pm2 jlist` first.
2. Never operate this stack from `llamacpp/*` (circular `pm2 stop` kill).
3. No PM2 stop/restart except the prescribed delete-and-re-add.
4. No Pi TUI spinner for watched ops.

## Shipped this session (2026-08-28, close #2 + attempt 2)

- **pi local-seat metadata fixed**: `~/.pi/agent/models.json` `llamacpp.local-llm` was still GLM-4.7-Flash **49152** — updated to Qwen3.6-35B-A3B **131072** (matches live server, verified via `/v1/models`). This is why Orca showed the local seat limited to 49k.
- **Vault pi bootstrap updated** (`~/.pi/agent/AGENTS.md`): AIWA rename (was "the 690"), local seat now "Qwen3.6-35B-A3B, 131k ctx since 2026-08-26", guardian path noted.
- Guardian recovered + PM2 dump saved after resurrect-left-stopped discovery.
- Executor residue cleaned: `nul` reserved-name file deleted (Win32 `DeleteFileW` with `\\?\` prefix — MSYS/PowerShell layers can't touch it; `os.path.exists('...nul')` always lies).
- Two pre-existing "Pi ready" Orca terminals were closed in error during cleanup (flagged to Carter; recreate if needed).

## Do not

- **Do not spawn Pi agents.** Paused 2026-08-28 (install-location issues + failed gate). Use Hermes.
- Do not run the gate operator on any `llamacpp` provider (circular kill — proven the hard way).
- Do not enable `LOCAL_WORKER_ENABLED` unattended — watched gate only.
- Do not point guardian at `pi.cmd` or any cmd shim (truncates multi-line prompts).
- Do not restart PM2 guardian without WORKBOARD announce.
- Extra llama on the R9700; clients on `:8081`; rewriting `690-routing/swap-*.sh`.
