# HANDOFF — Local-model-manager (GitHub merged; live deploy next)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server`  
**Workstream:** Local-model-manager — merge  
**Status:** **GitHub `main` has PR1–PR3.** Tip `649ee37`. MCC GitHub `main` `26e5eb8`. `FLEET_ROUTER` still **false**. Live guardian/MCC were **not** restarted. Carter proceeded: execute the **Safe order**.

Night 4 cutover is **complete** and is a different workstream. Soak leftovers stay in `NEXT-SESSION-HANDOFF.md` (Night 4).

## Plan

Full plan: `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`  
Session prompt: `NEXT.SESSION.md`

## GitHub merge ≠ live (do not merge again)

PR1–PR3 and PR-MCC are already on GitHub `main`. There is no second GitHub merge.

| Layer | llama-guardian | MCC |
|---|---|---|
| GitHub `main` | **done** `649ee37` | **done** `26e5eb8` (PR #9) |
| Disk | **done** (`C:\Workspace\Infrastructure\llama-cpp-server`) | **not yet** — `C:\Workspace\Active\MCC` behind 3, dirty |
| Running process | **not yet** — PM2 ~2 days, `watch` off. Live seats is 503 `llama_offline`; health has no `seats`. | **not yet** — `llama-status.mjs` still `/v1/models` |

## Safe order (execute this)

Announce WORKBOARD before PM2 / MCC restart. Flag stays **false** until step 4.

1. Restart **llama-guardian** (PM2). Disk already has merged code.
2. Update live MCC folder to GitHub `main` without wiping dirty files; restart MCC so status uses `/__guardian/health` `llama_up`.
3. Verify live: `GET /__guardian/seats` **200**; health has `seats.aiwa` / `seats.workbench`; `/v1/models` still GLM-shaped.
4. Then `FLEET_ROUTER=true`, restart guardian, soak. No PR4 until soak.

## What not to redo

- Night 4 Gates 0–4, 690-routing Sessions 1–4, Board WinUI build
- PR1–PR3 + PR-MCC GitHub merge
- Enabling the flag before live processes pass step 3

## Do not

- Extra llama on the R9700
- Call `:8081` from clients
- Rewrite `690-routing/swap-*.sh`
- SSH swap from guardian while `FLEET_SWAP_OWNER=false`
- Enable `FLEET_ROUTER=true` before live guardian **and** MCC pass Safe order step 3
- Restart PM2 without WORKBOARD
- Merge MCC again

Merged: llama #2/#4/#3, MCC #9.
