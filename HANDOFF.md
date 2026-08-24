# HANDOFF — Local-model-manager (PR2 on this branch)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** PR2 committed on this branch (`f7cf398`, on top of PR1 `9b2b566`). Not merged to `main`. `FLEET_ROUTER` still false. Next: **PR3** extend `/__guardian/health` with `seats.aiwa` / `seats.workbench`.

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

**PR3** in `qwen-queue/llama-guardian.py` `guardian_health`: JSON `seats.aiwa` / `seats.workbench`. `llama_up` remains GLM cache. Health may GET 8081 `/v1/health` to refresh cache. No SSH. No GPU probe. OpenAI `/v1/models` stays GLM-shaped. Base new work on this branch, not `main`. Cheap implementers: pi-glm5.4 or pi-deepseek, not Grok 4.5. Do not enable `FLEET_ROUTER=true` until PR1 and PR-MCC are merged onto the boxes that run them.

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before MCC `llama-status` uses `/__guardian/health` `llama_up`
