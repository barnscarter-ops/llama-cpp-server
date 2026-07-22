# Hermes-decided Qwen queue

`llama-guardian` owns Qwen's lifecycle and one-at-a-time queue. Hermes alone
decides whether a coding task should enter that queue. The queue never chooses
or invokes a cloud fallback; it returns Hermes's `bypass` or `fallback_cloud`
verdict to the calling harness.

```mermaid
flowchart LR
  H["Codex, Pi, ACP editor, Hermes, or other harness"] --> C["POST /__guardian/jobs"]
  C --> D["Hermes one-shot decision"]
  D -->|"queue_qwen"| Q["SQLite FIFO priority queue"]
  D -->|"bypass or fallback_cloud"| H
  Q --> L["guardian lifecycle and generation lock"]
  L --> M["llama.cpp Qwen on 127.0.0.1:8081"]
  M --> R["patch/result and verification evidence"]
  R --> H
```

## Safety boundaries

- Queue endpoints accept loopback requests only by default. Remote queueing
  requires `GUARDIAN_QUEUE_ALLOW_REMOTE=true` and a bearer token.
- Each job is durable in `logs/guardian-queue.sqlite3`, idempotent, and is
  requeued after a guardian restart if it had not reached a terminal state.
- Qwen receives a completion request and returns a proposed patch/result. It
  does not receive unrestricted filesystem tools. The calling harness retains
  its existing approval and write gate.
- The guardian's generation lock serializes queued jobs and normal proxied
  generation requests. Port `8081` remains internal; clients must not bypass
  the guardian with direct requests.

## Hermes contract

Guardian invokes the installed `hermes` executable in one-shot mode with task
metadata only: summary, expected line/file count, risk, source, and queue
snapshot. Hermes must return exactly:

```json
{"route":"queue_qwen","reason":"bounded implementation","priority":50}
```

Allowed routes are `queue_qwen`, `bypass`, and `fallback_cloud`; priority is
an integer from 0 through 100. Invalid, timed-out, or failed Hermes decisions
reject the submission rather than silently routing code work somewhere else.

Optional environment overrides belong to the guardian's PM2 environment:

```text
HERMES_DECIDER_EXE=hermes
HERMES_DECIDER_PROVIDER=
HERMES_DECIDER_MODEL=
HERMES_DECIDER_TIMEOUT_S=60
GUARDIAN_QUEUE_DB=C:\Workspace\Infrastructure\llama-cpp-server\logs\guardian-queue.sqlite3
GUARDIAN_QUEUE_ALLOW_REMOTE=false
GUARDIAN_QUEUE_TOKEN=
```

An empty provider/model uses Hermes's active configuration. Do not put tokens
in this repository or in the command line.

## Submit and poll

For a manual smoke test, submit a bounded request and wait for its result:

```powershell
.\scripts\qwen-submit.ps1 -Task "Add a function that returns the larger of two integers, with one unit test." -Source "codex-smoke" -ExpectedLines 20 -ExpectedFiles 2 -Wait
```

Every harness should use this protocol for a non-trivial code-execution task:

1. Submit a short decision context and a non-streaming OpenAI-compatible
   completion request to `POST /__guardian/jobs`.
2. If the response contains `job_id`, poll `GET /__guardian/jobs/{job_id}`.
3. Apply the returned patch only through the harness's normal approval gate.
4. If Hermes returns `bypass` or `fallback_cloud`, let that harness retain or
   select its own execution route. Do not post directly to `:8081`.

Queued work may be cancelled before it begins with
`POST /__guardian/jobs/{job_id}/cancel`.

The restored `scripts/acp-qwen-agent` uses this protocol in diff-only mode:
it does not expose model tools, direct `:8081` access, or automatic writes.
