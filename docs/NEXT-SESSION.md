# NEXT SESSION — Hermes routing audit done; Q1–Q5 decisions pending

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Workstream:** Guardian control-plane — closing the ungated local-routing side doors in Hermes configs/skills
**Date:** 2026-08-28 (Hermes TUI, default profile)

## Shipped

- **Served-model of record (W1-A, 2026-09-01, branch `barnscarter-ops/chief-v1-guardian-served`):** guardian now sets `X-Guardian-Seat` / `X-Guardian-Served-Model` / `X-Guardian-Request-Id` on every proxied completion (GLM + clerk/consult, stream + non-stream; seat header also on resolved-seat error responses). `/__guardian/health` and `/__guardian/seats` expose per-seat `served` counters + `served_total`. Consult requests carrying `X-Guardian-Gate: operator|frontier` (or `?gate=`) run the same gated swap as `/__guardian/swap`; ungated consult still downgrades to clerk. 101 qwen-queue tests green. Committed on the worktree branch, not pushed, PM2 untouched.

- **Full Hermes-side routing audit** (`harness routing/Hermes.md`, committed):
  every config/skill/env/cron/bot/MCP surface in all profiles checked for
  llamacpp routing or guardian-override paths.
  - Ungated `qwen-llamacpp` provider (→ `127.0.0.1:8080/v1`) confirmed in
    default + mav-room `config.yaml` custom_providers.
  - Prefer-local doctrine found in external `~/.agents/skills` orchestration
    skills (build-handoff "Local GPU first"), which the default profile loads
    via `skills.external_dirs`.
  - Stale local-model fallback paragraph in `~/.claude/CLAUDE.md` (GLM-4.7-Flash
    + `192.168.1.240:8080`).
  - pi `~/.pi/agent/models.json` llamacpp provider still present (dormant; pi
    paused).
  - Delegation/aux/moa/fallback/cron/bots/.env verified clean — nothing else
    routes model calls locally.
- **omp profile deleted** (`hermes profile delete omp -y`), gateway already
  stopped. Archive: `~/Documents/hermes-profile-backups/omp-predelete-20260828.tar.gz`.
  mav-room untouched per Carter.
- Vault `projects/llama-cpp-server.md` updated with the durable control-plane
  facts.

## Open

- **Q1 (default profile)** — remove the `qwen-llamacpp` custom provider
  entirely (local access only via `local_worker_submit`), or keep as
  guardian-front-door but fix metadata (`model: local-llm`, `ctx: 131072`;
  current `qwen3-llama/65536` silently truncates if trusted).
- **Q2** — strip "Local GPU first / rollback to Qwen" doctrine from external
  `~/.agents/skills` build-handoff + parallel-build-handoff (and their
  `qwen-executor-setup.md` rollback recipes), or leave dormant while pi paused.
- **Q3** — fix `~/.claude/CLAUDE.md` local-model paragraph (name guardian as
  the only sanctioned local path, or delete the fallback line).
- **Q4 — mav-room**: add `guardian_local_worker` MCP or remove its
  `qwen-llamacpp` entry — **deferred by Carter 2026-08-28; do not touch
  mav-room without his explicit go.**
- **Q5** — remove `llamacpp` provider from `~/.pi/agent/models.json`
  (defense-in-depth; pi's core prompt overrides AGENTS.md bans).
- Guardian stack itself unchanged this session: `LOCAL_WORKER_ENABLED` still
  off; no PM2/process changes made.

## Next session

**Repo:** none queued — wait for Carter to decide Q1 (and Q2/Q3/Q5).
**Prompt:** none — Carter owes the Q1 decision (remove vs keep-and-fix the
default-profile `qwen-llamacpp` entry) before that work can start.

Note: the previous parked workstream (PLAN sessions 0–6) is fully closed; its
handoff content lives in journal history + vault. Do not reopen it.

## Do not

- Do not touch the mav-room profile (Carter's standing order 2026-08-28).
- Do not spawn Pi agents (paused). Use Hermes.
- Do not enable `LOCAL_WORKER_ENABLED` unattended.
- Do not restart PM2 guardian without WORKBOARD announce.
- Do not deploy firewall/nftables on `:8081` or `.240`.
- Do not `git add -A` on a dirty tree at close.
