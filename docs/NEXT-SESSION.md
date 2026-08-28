# NEXT SESSION — PLAN complete; workstream parked

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian-managed local workers (`PLAN.md`) — **complete**
**Tip:** `b5eee2b` (pushed; `main` = `origin/main`) plus this close commit
**Live:** guardian `ok` on `:8080`, `llama_up=false`, `:8081` down,
`LOCAL_WORKER_ENABLED` **off** (`/__guardian/workers` enabled=false),
AIWA occupant **clerk**, `swap_owner=true`.

## Shipped

- PLAN Sessions **0–6 complete**. Capstone: `qwen-queue/harness/ROLLOUT.md`.
- Watched smoke **attempt 2 PASS** then flag rolled back. Job
  `qj_d52cb887f9ae49db9ef440d322595a9f`, `SMOKE.txt=GATE_ATTEMPT_2_OK`.
- Attempt 1 was **Hermes GLM-5.3**, not Codex (Pi on `llamacpp/local-llm`,
  circular guardian kill). **Pi orchestrators paused.**
- Session 3: Grok + Hermes MCP live. Defaults unchanged (`grok-4.6` /
  `glm-5.3`). Jobs `qj_3e8a4e29129a4a718d5550e7ca8f652a` /
  `qj_c926913dc0db46b98d7329a6aa42d62b`.
- Session 4: prefer local only for bounded standard `tool_execution`.
  Planning 409/503, frontier 409, AIWA stayed clerk.
  `qwen-queue/harness/CALIBRATION.md`.
- Session 5: DeepSeek `SubagentProvider` in-repo. **Pi native skipped.**
- Session 6: Codex/Claude MCP fragments documented, **not live-installed**.
  Native subagents stay cloud. `:8081` / `.240` inventory. Enforcement
  **proposed, not deployed** (keep loopback `:8081` + policy; do not
  firewall `.240`).
- WORKBOARD: guardian row parked S0–S6; stale 2026-08-27 690 GPU swap
  ANNOUNCE marked done (occupant clerk).
- Commits: `17b1e26` `c080df5` `85a63a5` `b5eee2b`.

## Open

- No PLAN session remains. Do **not** start new guardian feature work
  unless Carter names it.
- `LOCAL_WORKER_ENABLED` stays off until Pi is unpaused **or** the
  guardian runner is switched off Pi.
- Codex/Claude MCP is docs-only until a watched harness-config install.
- Grok/Hermes MCP needs a process reload to appear in an old live tab;
  Hermes gateway was not restarted.
- `.240` remains a LAN completions bypass (and Board/ops GET). Do not
  deploy nftables/firewall without an allowlist that keeps those GETs.

## Next session

**No executor job is queued.** This workstream is parked.

If Carter names a leftover, possible follow-ups (do not start from this
prompt alone):

1. Unpause Pi or switch the worker runner off Pi, then a watched
   `LOCAL_WORKER_ENABLED` flip.
2. Live-wire the Codex/Claude MCP fragments in `ROLLOUT.md`.
3. A different repo.

**Repo:** none queued
**Prompt:** none — wait for Carter

## Do not

- Do not spawn Pi agents (paused 2026-08-28). Use Hermes.
- Do not enable `LOCAL_WORKER_ENABLED` unattended.
- Do not point guardian at `pi.cmd`.
- Do not restart PM2 guardian without WORKBOARD announce.
- Do not deploy firewall/nftables on `:8081` or `.240`.
- Extra llama on the R9700; clients on `:8081`; rewriting
  `690-routing/swap-*.sh`.
