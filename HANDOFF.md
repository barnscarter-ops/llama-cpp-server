# HANDOFF — Local-model-manager (PR1 + PR2 + PR3 + PR-MCC merged)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** **Merged to `main`.** llama-cpp-server `6cbe526`. MCC `26e5eb8`. `FLEET_ROUTER` still **false**. Live guardian/MCC processes were **not** restarted. Board App/Core/Tests still frozen.

Night 4 cutover is **complete** and is a different workstream. Soak leftovers stay in `NEXT-SESSION-HANDOFF.md` (Night 4). This file is the manager-merge brief.

## Plan

Full plan: `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`  
Pointer in this repo: `PLAN.md`  
Session prompt: `NEXT.SESSION.md`

## What not to redo

- Night 4 Gates 0–4 (`aiwa-transplant/NIGHT4-COMPLETE-20260822.md`)
- 690-routing Sessions 1–4 (`690-routing/HANDOFF.md`) — clerk/consult scripts exist
- Local LLM Board WinUI build — frozen; do not edit App/Core/Tests until PR8
- PR1–PR3 + PR-MCC implementation (on `main`)

## Next

GitHub merge is done. **Do not enable `FLEET_ROUTER=true`** until llama-guardian **and** MCC are restarted so they run this `main`. Then soak with the flag still false. Do not start PR4/PR6/PR8. No live AIWA swap.

Merged PRs:

- llama-cpp-server #2 PR1 https://github.com/barnscarter-ops/llama-cpp-server/pull/2
- llama-cpp-server #4 PR2 https://github.com/barnscarter-ops/llama-cpp-server/pull/4
- llama-cpp-server #3 PR3 https://github.com/barnscarter-ops/llama-cpp-server/pull/3
- MCC #9 PR-MCC https://github.com/Maverick-Core-Software/MCC/pull/9

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before the live guardian + MCC processes are running this `main`
