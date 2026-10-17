# Turning Guardian on

What the code now guarantees, and the exact steps. Flags are all off today; nothing here changes behavior until they are set.

## What changed in code
- A caller that names a cloud model itself (`/v1/chat/completions`, `POST /__guardian/jobs`) is refused unless it is authorized (loopback, or `Authorization: Bearer $GUARDIAN_QUEUE_TOKEN` with `GUARDIAN_QUEUE_ALLOW_REMOTE=true`) and sends `X-Guardian-Clearance: internet`. Without the header: `403 clearance_required`. Narrower or unknown clearance: `403 clearance_too_narrow`. Not authorized: `403 cloud_forbidden`. The task API is unchanged and is the normal path.
- Secrets load from a gitignored `.guardian.env` at the repo root (override with `GUARDIAN_ENV_FILE`); real environment variables win. Never put tokens in `ecosystem.config.cjs`.
- `/__guardian/health` has `turn_on_gaps`: names of missing settings for the flags that are on. Empty list = nothing missing.

## Steps (each needs Carter's go; restarts are Carter's)
1. Create `.guardian.env` next to `ecosystem.config.cjs` on the Workbench: `GUARDIAN_QWEN_APPROVAL_TOKEN=<random 32+ chars>`. Put the same value in Chief's env as `CHIEF_GUARDIAN_APPROVAL_TOKEN`.
2. Add flags to the same file, in this order: `GUARDIAN_MODEL_SLOTS=true`, `GUARDIAN_PER_MODEL_QUEUES=true`, `GUARDIAN_QWEN_APPROVAL=true`, `GUARDIAN_TASK_API=true`. Keep `GUARDIAN_SELECTOR=rules`. `FLEET_ROUTER=true` must be on (it is in ecosystem.config.cjs). Cloud (`GUARDIAN_CLOUD=true`) last, and only with the Jev/DeepSeek key available.
3. `pm2 restart llama-guardian` (admin PowerShell, `PM2_HOME=C:\Users\carte\.pm2`), then `pm2 save`.
4. Check `GET /__guardian/health`: `status ok`, `turn_on_gaps: []`. Health is the only request to send; do not send inference requests to the live Guardian.
5. Chief: set `CHIEF_USE_GUARDIAN_TASKS=1`, `CHIEF_GUARDIAN_URL`, the clearance and cost envs, restart the bridge.

## Rollback
Remove the flags from `.guardian.env` and restart `llama-guardian`. Unset flags behave exactly as before.

## Still open (not blocking)
Workspace Pi dev jobs still pick models in Chief; Chief still holds the DeepSeek relay key; local-seat calls on :8080 remain unauthenticated (they spend nothing).
