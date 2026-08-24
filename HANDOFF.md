# HANDOFF — Local-model-manager (GitHub merged; live deploy next)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** Safe order **executed**. Flag soak **passed**. Tip `47adba3`. MCC disk `26e5eb8`. Live `llama-guardian` has **`FLEET_ROUTER=true`** (uncommitted live ecosystem). Carter proceeded: **PR4 next**. Do not start PR6/PR8.

Night 4 cutover is **complete** and is a different workstream. Soak leftovers stay in `NEXT-SESSION-HANDOFF.md` (Night 4).

## Plan

Full plan: `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`  
Session prompt: `NEXT.SESSION.md`

## GitHub merge ≠ live (do not merge again)

PR1–PR3 and PR-MCC are already on GitHub `main`. There is no second GitHub merge.

| Layer | llama-guardian | MCC |
|---|---|---|
| GitHub `main` | **done** `47adba3` | **done** `26e5eb8` (PR #9) |
| Disk | **done** + uncommitted `FLEET_ROUTER: "true"` in `ecosystem.config.cjs` | **done** `26e5eb8`; dirty files kept |
| Running process | **done** — PM2 id 20, `FLEET_ROUTER=true`. seats 200; health has seats | disk ready; Windows `mav-console` not running (not started) |

## Safe order (executed 2026-08-23)

1–4 done. Flag on. Soak passed. Clerk POST pong, GLM stayed stopped, consult 409. **Next: PR4 only.**

## What not to redo

- Night 4 Gates 0–4, 690-routing Sessions 1–4, Board WinUI build
- PR1–PR3 + PR-MCC GitHub merge
- Enabling the flag before live processes pass step 3

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Start PR6 / PR8 in the PR4 session
- Commit live `FLEET_ROUTER=true` to origin unless Carter wants GitHub default true
- Restart PM2 without WORKBOARD
- Merge MCC again

Merged: llama #2/#4/#3, MCC #9.
