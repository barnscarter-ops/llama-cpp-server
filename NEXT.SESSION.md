# NEXT.SESSION — Local-model-manager

**Pointer:** `HANDOFF.md` (this repo) and Board `PLAN.md` + Board `NEXT.SESSION.md` (paste prompt).

**GitHub `main` has PR1–PR3 + PR-MCC.** `FLEET_ROUTER` still false. Live processes not restarted. Carter proceeded: **execute the Safe order**. Do not merge again.

## Safe order

1. WORKBOARD, then restart llama-guardian (PM2), flag **false**. Disk already on `main`; process is stale (live seats 503 `llama_offline`).
2. Pull live MCC `C:\Workspace\Active\MCC` to GitHub `main` without destroying dirty files; restart MCC (`llama_up` from `/__guardian/health`).
3. Verify live: seats 200, health has `seats.aiwa` / `seats.workbench`, `/v1/models` GLM-shaped.
4. Then `FLEET_ROUTER=true`, restart guardian, soak. No PR4/PR6/PR8 until soak.

Related: Night 4 leftovers in `NEXT-SESSION-HANDOFF.md`. Do not start Mav Room Session J from this workstream.
