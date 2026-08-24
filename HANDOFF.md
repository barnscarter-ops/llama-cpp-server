# HANDOFF — Local-model-manager (PR3 on this branch)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** PR3 on this branch (`41e2435` health seats, on PR2 `f7cf398` / PR1 `9b2b566`). Not merged to `main`. `FLEET_ROUTER` still **false**. Next: merge/open GitHub PRs when Carter wants; do **not** start PR4/PR6/PR8; do **not** enable the flag until PR1 + PR-MCC are on the live boxes.

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

**PR3 is implemented** (`41e2435`): `GET /__guardian/health` nests `seats_snapshot()` as `seats.aiwa` / `seats.workbench`, writes `guardian._llama_up` from the live 8081 `/v1/health` probe. Seats handler still cache-only. Tests: 31 OK. Do not enable `FLEET_ROUTER=true` until PR1 and PR-MCC are merged onto the boxes that run them. Merge this branch only if Carter says so.

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before MCC `llama-status` uses `/__guardian/health` `llama_up`
