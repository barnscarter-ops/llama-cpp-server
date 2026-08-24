# HANDOFF — Local-model-manager (PR1 on this branch)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** PR1 committed on this branch (`9b2b566`). Not merged to `main`. `FLEET_ROUTER` still false. Next: **PR2** `GET /__guardian/seats`.

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

**PR2** in `qwen-queue/llama-guardian.py`: `GET /__guardian/seats` from the PR1 occupant cache (`aiwa` / `workbench`). Register **before** catch-all. Do not change OpenAI `GET /v1/models`. Seats handler must not live-GET 8081. Base new work on this branch, not `main`. Cheap implementers: pi-glm5.4 or pi-deepseek, not Grok 4.5.

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before MCC `llama-status` uses `/__guardian/health` `llama_up`
