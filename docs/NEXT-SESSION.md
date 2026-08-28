# NEXT SESSION — Session 6: Codex/Claude fallback + bypass-control

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian-managed local workers (`PLAN.md`)
**Tip:** `85a63a5` (pushed; `main` = `origin/main`)
**Live:** guardian `ok` on `:8080`, `llama_up=false`, `LOCAL_WORKER_ENABLED` **off** (`/__guardian/workers` enabled=false), AIWA occupant **clerk**, `swap_owner=true`.

## Shipped

- Watched smoke **attempt 2 PASS** (operator=Grok, not Pi). Job `qj_d52cb887f9ae49db9ef440d322595a9f`, `SMOKE.txt=GATE_ATTEMPT_2_OK`. Flag rolled back.
- Attempt 1 attribution: **Hermes GLM-5.3**, not Codex. It launched Pi on `llamacpp/local-llm` and circular-killed guardian.
- **Pi orchestrators paused.** Agent work → Hermes/Grok. Guardian's coded Pi runner still used for admitted `tool_execution` jobs only.
- **Session 3:** Grok + Hermes MCP live (`~/.grok/config.toml`, `%LOCALAPPDATA%\hermes\config.yaml`). Defaults unchanged (`grok-4.6` / `glm-5.3`). Live jobs `qj_3e8a4e29129a4a718d5550e7ca8f652a` / `qj_c926913dc0db46b98d7329a6aa42d62b`.
- **Session 4:** `qwen-queue/harness/CALIBRATION.md`. Prefer local only for bounded standard `tool_execution`. Planning 409/503, frontier 409, AIWA stayed clerk.
- **Session 5:** DeepSeek `SubagentProvider` at `qwen-queue/harness/deepseek-guardian-provider.mjs` (5/5 tests). **Pi native skipped.**
- Commits: `17b1e26` `c080df5` `85a63a5`.

## Open

- **Session 6** (last PLAN session): Codex/Claude MCP fallback docs; inventory `:8081` and `.240` bypass; propose (do not deploy) enforcement.
- Pi native path still skipped until Carter unpauses Pi.
- `LOCAL_WORKER_ENABLED` stays off unless a watched flip.
- This Grok tab does not have the new MCP until config reload. Hermes gateway was not restarted.
- Stale WORKBOARD row: 690 GPU swap ANNOUNCE 2026-08-27 (occupant is clerk).

## Next session

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`

**Prompt:** Read `docs/NEXT-SESSION.md` and `PLAN.md` Session 6. Execute Session 6 only: document the MCP fallback for Codex and Claude (their native subagents remain cloud unless a supported provider override is verified); inventory direct access paths to Workbench `:8081` and AIWA `.240`; propose — do not deploy — network/process enforcement with rollback and compatibility impact. No firewall, no PM2 mutation, no Pi spawn, no `LOCAL_WORKER_ENABLED` flip. Commit `docs(guardian): publish managed local worker rollout`.

## Do not

- Do not spawn Pi agents (paused 2026-08-28). Use Hermes.
- Do not enable `LOCAL_WORKER_ENABLED` unattended.
- Do not point guardian at `pi.cmd`.
- Do not restart PM2 guardian without WORKBOARD announce.
- Extra llama on the R9700; clients on `:8081`; rewriting `690-routing/swap-*.sh`.
