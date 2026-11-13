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

## Interactive chat priority (website chat agent on the LAN)
A public chat agent on another machine can use the AIWA clerk through Guardian, always go first, and optionally take the clerk over from running non-chat work. Off unless the token is set.

| Setting | Meaning |
|---|---|
| `GUARDIAN_INTERACTIVE_TOKEN` | Bearer token for the chat caller (32+ chars; shorter is ignored and named in `turn_on_gaps`). Unset = feature off. Put it in `.guardian.env`, never in `ecosystem.config.cjs`. |
| `GUARDIAN_INTERACTIVE_PREEMPT` | `true` = take over a running request or queue job on the clerk. Default `false`. |
| `GUARDIAN_PREEMPT_MAX_PER_JOB` | Take-overs one queue job survives before it is protected (it then runs to the end and chat waits first in line). Default `3`, `0` = unlimited. |

Turn-on order, one change per restart: (1) `GUARDIAN_MODEL_SLOTS=true` (priority and take-over only exist with slots; `FLEET_ROUTER=true` is also required), (2) `GUARDIAN_INTERACTIVE_TOKEN`, (3) `GUARDIAN_INTERACTIVE_PREEMPT=true`. After each restart check `/__guardian/health`: `turn_on_gaps` is empty and `interactive` shows `enabled` / `preempt_enabled`.

What it does:
- A request with `Authorization: Bearer <token>` is allowed past the loopback-only guard and is served only by the clerk: a consult alias is served as clerk, `X-Guardian-Gate` is ignored, no swap is ever started, and a non-clerk occupant gives today's 409/503. The token is never forwarded to AIWA. Other callers still get the 403.
- Waiters for the clerk slot are served interactive first, FIFO within a priority. A swap is never preempted or starved.
- With preempt on, an interactive request that finds the clerk busy tells the holder to drop its AIWA connection and release the slot, then runs ahead of every other waiter. Consult-seat work and other interactive requests are never preempted.

What victims see:
- Direct request before any bytes: `503`, `guardian_error` code `preempted_by_interactive`, `Retry-After: 5`.
- Direct request already streaming: an SSE stream gets one final `data: {"error":{"code":"preempted_by_interactive",...}}` event, then ends; a non-SSE response is just cut short.
- Queue job: put back to `queued`, not counted as a failed attempt, and rerun by the worker loop. After `GUARDIAN_PREEMPT_MAX_PER_JOB` take-overs it is no longer preemptible. The count is in memory and resets on a Guardian restart.
- Health: `interactive.preemptions` and `interactive.last_preempt_at`; one log line per take-over (`interactive_preempt seat=... victim=... id=...`, no content, no token).

Rollback: remove `GUARDIAN_INTERACTIVE_PREEMPT` first (keeps the priority, ends take-overs), then the token (turns the feature off), then restart. Unset = behavior as before.

## Rollback
Remove the flags from `.guardian.env` and restart `llama-guardian`. Unset flags behave exactly as before.

## Still open (not blocking)
Workspace Pi dev jobs still pick models in Chief; Chief still holds the DeepSeek relay key; local-seat calls on :8080 remain unauthenticated (they spend nothing).
