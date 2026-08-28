# Hermes → llamacpp Routing Audit

**Date:** 2026-08-28
**Scope:** All Hermes configurations and skills that could (a) route a Hermes agent's model calls to the local llamacpp stack, or (b) override / bypass a guardian (llama-guardian) admission decision.
**Context:** Guardian is being configured to control and manage ALL sub-agent model calls. This audit identifies every side-door.

---

## Guardian's intended control surface

- **Gated path (correct):** `local_worker_submit` via the `guardian_local_worker` MCP server (`qwen-queue/local-worker-mcp.py`). Guardian acts as admission decider; `requires_cloud` / `consult_gate_required` / `local_workers_disabled` results must be escalated, not retried against a raw local endpoint.
- **Ungated path (the problem):** any direct OpenAI-compatible client pointed at `127.0.0.1:8080` (guardian's proxy front door) or `:8081` (raw llama-server, loopback only). Traffic on :8080 traverses guardian's *lifecycle* proxy but not its *admission* gate — a direct completion cold-starts the GPU with no decider involvement. :8081 skips guardian entirely.

---

## Findings

### Tier 1 — Would actively route an agent to local inference

| # | Location | Finding | Status |
|---|----------|---------|--------|
| 1 | `custom_providers: qwen-llamacpp` → `http://127.0.0.1:8080/v1` in **default** profile `config.yaml:518-524` | Selectable local provider (`local-llm` model, ctx 65536). Any agent told to "use the local model" or running `hermes model` can pick it. Hits :8080 → ungated. | **OPEN — decision pending** |
| 2 | Same `qwen-llamacpp` entry in **mav-room** profile `config.yaml:494-497` | Identical entry in the Mav-Room profile. | **OPEN — Carter deferred ("mav room can be left alone" 2026-08-28)** |
| 3 | ~~Same entry in **omp** profile~~ | — | **RESOLVED — omp profile deleted entirely 2026-08-28** (backup: `C:\Users\carte\Documents\hermes-profile-backups\omp-predelete-20260828.tar.gz`) |
| 4 | `skills.external_dirs: [~/.agents/skills]` (default profile config.yaml:348) pulls in `orchestration/build-handoff` | Its SKILL.md line 161: *"Executor: Local GPU first, then cloud fallback — Prefer local model (llamacpp/llama.cpp)"*. Prefer-local doctrine inherited silently by any default-profile session loading an orchestration skill. | **OPEN** |
| 5 | External skills `orchestration/build-handoff` + `parallel-build-handoff` (`~/.agents/skills/`), refs `qwen-executor-setup.md` | *"Rollback to Qwen: `pi --provider llamacpp --model qwen3.6-35b`"* + copy-paste `qwen-llamacpp` Hermes provider block. Pi is paused, but the recipe re-routes any future pi executor straight to the ungated port. | **OPEN (pi paused = dormant)** |
| 6 | `~/.claude/CLAUDE.md:28-31` (global Claude Code config; applies to this repo via AGENTS.md standing rules) | *"GLM-4.7-Flash at `127.0.0.1:8080` is this PC's loopback fallback"* + Nemotron at `192.168.1.240:8080`. Stale model AND a direct-to-port routing instruction outside guardian's gate. | **OPEN — stale + ungated** |
| 7 | `~/.pi/agent/models.json` — `llamacpp` provider (`http://127.0.0.1:8080/v1`) | Pi can still select it. Mitigation: `~/.pi/agent/AGENTS.md` bans pi from calling local models — but per the (now-deleted) omp guardian skill's own research, **pi's core system prompt overrides AGENTS.md**, so the ban is advisory. | **Dormant (pi paused 2026-08-28)** |

### Tier 2 — Stale metadata that mis-routes or silently truncates

| # | Location | Finding |
|---|----------|---------|
| 8 | `qwen-llamacpp` provider entries (default + mav-room): `model: qwen3-llama`, `context_length: 65536` | Live seat is Qwen3.6-35B-A3B as **`local-llm` @ ctx 131072** (since 2026-08-26, commit 5a6defb). Guardian's LEGACY_MODEL_ALIASES remaps the alias server-side, but agents trusting the Hermes registry metadata get silently truncated to 64k ctx. Same drift class documented in the `local-llm-serving` skill's post-swap checklist. |
| 9 | `~/.claude/CLAUDE.md` local-model paragraph | Declares GLM-4.7-Flash as the PC fallback — two models stale (seat is Qwen3.6). Steers future Claude sessions to a dead model name. |

### Tier 3 — Verified aligned (no action needed)

- **`delegation:` config** — default profile pins `deepseek-v4-flash` @ deepseek (cloud); `max_spawn_depth: 1`; no profile delegates to llamacpp. (omp/mav-room inherited-clean variants; omp now gone.)
- **`auxiliary.*`** (vision/compression/title_generation/curator/etc.) — all cloud (`zai-coding`, `deepseek`) or `auto`. None local.
- **`moa.*`, `fallback_providers: []`** — cloud or empty.
- **`.env` (default + profiles)** — no `HERMES_DECIDER_*` vars; no `*_BASE_URL` points at localhost/127.0.0.1. (Guardian's own `HERMES_DECIDER_MODEL/PROVIDER=glm-5.2/zai-coding` env correctly lives on guardian's pm2 process in `ecosystem.config.cjs`, not on Hermes.)
- **Cron** — default profile holds only a disabled test job; no routing logic. omp's cron DB deleted with the profile.
- **Bots** — none configured.
- **MCP servers** — gws, guardian_local_worker, zai-* , housecall-pro: none route model calls to llamacpp.
- **`local-llm-serving` / `llama-cpp` / `aiwa-pc-stack` skills** — these document *serving ops* (benching, swapping, pm2, :8080/:8081 ports for curl probes). They don't instruct an agent to route its own inference locally; probe curls are ops, not model calls. No change needed.

### Structural gap

**mav-room has no `guardian_local_worker` MCP entry** (only the default profile does). If a mav-room agent ever wants local inference, its only available route is the ungated `qwen-llamacpp` provider. If guardian is to control ALL sub-agent calls, mav-room needs either the MCP entry (gated path) or the provider removed. Carter has deferred mav-room changes for now.

---

## Actions taken this session

1. **Deleted the `omp` Hermes profile entirely** (`hermes profile delete omp -y`, gateway was already stopped). Pre-delete archive at `C:\Users\carte\Documents\hermes-profile-backups\omp-predelete-20260828.tar.gz`. This eliminated findings #3, the omp orchestration skill copies (build-handoff, parallel-build-handoff, hermes-provider-setup, local-llama-guardian, pi-executor handoffs inside the profile), and omp's cron.
2. **mav-room profile untouched** per Carter's explicit instruction.
3. Audit report written (this file).

## Open decisions for Carter

- **Q1 — the `qwen-llamacpp` provider in the DEFAULT profile:** (a) remove it so local access exists only via `local_worker_submit`, or (b) keep as guardian-front-door convenience but fix `model: local-llm`, `context_length: 131072`. (b) remains ungated-by-decider.
- **Q2 — external `~/.agents/skills` orchestration skills:** strip the "Local GPU first / rollback to Qwen" doctrine (lines 161/171 SKILL.md + `qwen-executor-setup.md` rollback section), or leave dormant while pi stays paused.
- **Q3 — `~/.claude/CLAUDE.md` local-model paragraph:** rewrite to name guardian as the only sanctioned local path (or delete the fallback line).
- **Q4 — mav-room:** add `guardian_local_worker` MCP or remove its `qwen-llamacpp` entry, when the deferral lifts.
- **Q5 — `~/.pi/agent/models.json`:** remove the `llamacpp` provider entry (defense-in-depth against pi's prompt-override behavior) — low priority while pi is paused.
