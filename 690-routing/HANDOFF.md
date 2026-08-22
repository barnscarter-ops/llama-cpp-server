# Handoff — finish 690 Qwen consult / Nemotron clerk split

**Date:** 2026-08-22
**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`
**Plan:** `690-routing/PLAN.md` (this directory)
**Doctrine:** `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`
**Diagnosis (do not re-litigate):** `C:\Workspace\Active\brain\inbox\2026-08-21-qwen-nemo-routing.md`

## What already closed

- R9700 on the 690 serves both models, one at a time, at `http://192.168.1.240:8080`.
- Qwen 3.8 27B @ 262k fits (~48 t/s, ~33.9/34.2 GB). Thinking-on smoke looks elite. **As a pi executor it collapsed** (MacBridge S3/S4). Same think-bomb as Nemotron at ~29k ctx.
- Nemotron Lightning 30B is a **clerk**, not Ultra 550B (SWE-V 38.8). systemd has `--reasoning off` and 131k. Hard impl stays cloud.
- Routing table is in the architecture doc. **Not installed:** swap scripts, alias alignment, pi `reasoning: false` on Nemotron, smokes, guaranteed Nemotron-on-walkaway.

Carter at last close: “Understood. Doing now” — he was swapping Qwen in as a consult. **Check `/v1/models` before you assume Nemotron.**

## What you are here to finish

Execute `690-routing/PLAN.md` Sessions 1→4. Done = Session 4 verification: clerk smoke + consult smoke in git, `:8080` is Nemotron, systemd active.

You are a **capable SSH agent** (Grok/Claude/Hermes). Do not dispatch this plan to Nemotron or Qwen-as-worker.

## Start here

1. Read `690-routing/PLAN.md` primer (Night 4 + pkill trap + one-model rule).
2. Read `C:\Workspace\Active\brain\WORKBOARD.md`. Max 2 workstreams. Announce any llama restart **before** doing it.
3. `curl -sS --max-time 5 http://192.168.1.240:8080/v1/models` — first action, not SSH.
4. If `aiwa-transplant/NIGHT4-PLAN.md` is in progress or <2h from 18:30 Saturday 2026-08-22: **git-only** through Session 3; no live swap. CT 210 may die in cutover; scripts in this repo are the surviving copy.

SSH: `ssh -i ~/.ssh/id_ed25519_proxmox root@192.168.1.230` then `lxc-attach -n 210`.

## Do not redo

- Re-diagnose why MacBridge failed
- Re-bench tg/pp (already measured)
- Dual-serve both models
- Pass `--reasoning off` on Qwen consult
- Leave Qwen on `:8080` when you walk away
- `pkill -f llama-server` (use `pkill -f '[l]lama-server'` after `systemctl stop` for the unit)
- Commit repo-root untracked `memory/`
- Night 4 gates, API-key hardening, Nemotron 262k ladder, quality bench

## Constraints

- Only files this workstream needs. Brain `agent-memory/*` and other dirty brain files are someone else's.
- Brain git currently has **no remote** — architecture commits stay local.
- This repo **has** origin `barnscarter-ops/llama-cpp-server` — push `690-routing/` commits.
- `C:\Workspace` ≡ `D:\Workspace` junction; use `C:\` in docs if other agents expect it.

## After Session 4

Leave Nemotron up. Optional later inbox threads (not this plan): quality bench, Nemotron 262k, CORS/API key, Night 4 soak if it ran.
