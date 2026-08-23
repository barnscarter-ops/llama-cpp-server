# PLAN — Local-model-manager merge

**Status:** PLAN READY. Do not implement until Carter says **proceed**.

This repo is the **implementation home** (PR1+ against `qwen-queue/llama-guardian.py`). The full plan is not duplicated here.

## Canonical plan (read this)

1. `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`
2. Identical: `C:\Workspace\Active\brain\plans\local-model-manager.md`
3. Identical: `C:\Workspace\Archive\Build Plans\LocalLlmBoard\2026-08-22_local-model-manager-merge.md`

Pickup: `HANDOFF.md` and `NEXT.SESSION.md` in this repo (and Board `HANDOFF.md`).

## Recommendation (one paragraph)

Grow PM2 `llama-guardian` into the fleet front door at `http://127.0.0.1:8080`. Route by explicit `model` alias only. Clerk/consult reverse-proxy to AIWA `http://192.168.1.240:8080` without GLM locks or `:8081`. Consult mismatch → 409. Cloud → 409, no cloud proxy. Swap scripts stay `690-routing/` SoT; this process must not SSH until PR6 (`FLEET_SWAP_OWNER`).

## First PR (only after proceed)

**PR1** — `feat(guardian): route clerk/consult to AIWA without GLM locks or 8081`

Files: `qwen-queue/llama-guardian.py`, new tests. No SSH. `FLEET_ROUTER` default false. Empty model stays GLM. Occupant probe in PR1.

Do not rewrite `690-routing/swap-*.sh`. Do not point pi/DSH/Hermes at a new URL in PR1.
