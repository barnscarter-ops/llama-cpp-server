# HANDOFF — Local-model-manager (PLAN READY)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server` `main`  
**Workstream:** Local-model-manager — merge  
**Status:** PLAN READY. Wait for Carter **proceed**, then PR1 here.

Night 4 cutover is **complete** and is a different workstream. Soak leftovers stay in `NEXT-SESSION-HANDOFF.md` (Night 4). This file is the manager-merge brief.

## Plan

Full plan: `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`  
Pointer in this repo: `PLAN.md`  
Session prompt: `NEXT.SESSION.md`

## What not to redo

- Night 4 Gates 0–4 (`aiwa-transplant/NIGHT4-COMPLETE-20260822.md`)
- 690-routing Sessions 1–4 (`690-routing/HANDOFF.md`) — clerk/consult scripts exist
- Local LLM Board WinUI build — frozen; do not edit App/Core/Tests until PR8

## Next

PR1 in `qwen-queue/llama-guardian.py` after proceed. See `PLAN.md` § PR Plan.

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before MCC `llama-status` uses `/__guardian/health` `llama_up`
