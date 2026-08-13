"""
llama-guardian — on-demand lifecycle manager for llama-server.

ARCHITECTURE
============
This proxy owns port 8080 (where MCC points its local-LLM requests).
It forwards everything to llama-server, which listens on the
internal port 8081 (loopback only, not exposed).

Four jobs:
  1. STREAMING PROXY — port 8080 → 8081, preserving SSE token streams.
  2. PRE-WARM       — when MCC (3000) comes online, start llama so it's
                      hot before the first real request.
  3. IDLE REAPER    — stop llama after IDLE_TIMEOUT_MIN of no activity.
  4. QWEN QUEUE     — ask Hermes whether a bounded coding task should enter
                      a durable, single-worker local Qwen queue.

WHY A PROXY (not a process watcher)
===================================
MCC connects directly to port 8080. If llama is stopped and a request
arrives, the connection is refused instantly — no chance to start llama.
The proxy intercepts at the port level so it can cold-start on demand.

MCC needs ZERO changes — it keeps hitting localhost:8080.

PM2 LIFECYCLE
=============
  - qwen3-llama:   registered STOPPED. This guardian controls its lifecycle.
                   autorestart=true so it recovers from crashes while running,
                   but never auto-starts on boot.
  - llama-guardian: auto-starts on boot (this file). ~40MB RAM.

DEPENDENCIES
============
Python 3.10+, aiohttp.  (Both already installed on this system.)
"""

import asyncio
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import ClientError, ClientTimeout, web

from guardian_queue import HermesDecider, HermesDecisionError, JobStore, QueueJob

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG — tune here. All times in seconds unless noted.
# ─────────────────────────────────────────────────────────────────────────────

LLAMA_HOST = "127.0.0.1"          # llama-server binds loopback only (internal)
LLAMA_PORT = 8081                 # llama-server internal port
PROXY_HOST = "0.0.0.0"            # guardian listens on all interfaces (Tailscale)
PROXY_PORT = 8080                 # where MCC connects (unchanged)

# Listener watchdog. asyncio's proactor accept loop CLOSES the listening socket
# on any OSError from accept() (see CPython proactor_events.py, "Accept failed
# on a socket" → sock.close()) and never re-arms it. One aborted inbound
# connection therefore kills 8080 permanently while the process stays alive —
# background tasks keep the event loop running, so PM2 still reports "online"
# and never restarts. That is exactly what happened 2026-08-09 22:24:10:
# WinError 64 (ERROR_NETNAME_DELETED) on accept, then a live guardian with a
# dead front door until it was restarted by hand. Since PROXY_HOST is 0.0.0.0
# for Tailscale, any LAN client aborting a handshake can trigger it.
LISTENER_POLL_S = 15              # how often to confirm 8080 still accepts
LISTENER_FAIL_THRESHOLD = 2       # consecutive probe failures before acting
LISTENER_REARM_MAX = 3            # failed re-arms before exiting for PM2

# Services whose startup should trigger a llama pre-warm.
# When one of these ports transitions down→up, we start llama in the
# background so it's hot by the time a real request arrives.
PREWARM_PORTS = [3000]            # 3000 = MCC. 3012 (customer-chat/maverickforge) migrated to AIWA — cloud DeepSeek via hermes gateway, no longer local.
PREWARM_POLL_S = 5                # how often to check prewarm ports

# Qwen3.6 reasoning loops never self-terminate: a stuck trajectory burns the
# whole token budget and returns finish_reason=length (verified 2026-08-04 —
# tripling the cap reproduced the identical failures). A nudged seed takes a
# different trajectory and almost always completes. Applies only to
# non-streaming chat completions; streams can't be retried mid-flight.
LENGTH_RETRY_MAX = 2              # extra attempts after a finish_reason=length

IDLE_TIMEOUT_MIN = 60             # stop llama after this many idle minutes
# Allow override for testing (e.g. IDLE_TIMEOUT_MIN=1 to test the reaper fast)
if os.environ.get("IDLE_TIMEOUT_MIN"):
    IDLE_TIMEOUT_MIN = int(os.environ["IDLE_TIMEOUT_MIN"])
IDLE_POLL_S = 60                  # how often the reaper checks

# Minimum seconds between a llama stop and the next start. The R9700's Windows
# driver TDR'd (bugcheck 0x116 in amdkmdag.sys, 2026-08-06) when a model load
# began seconds after a ~26GB VRAM teardown. Give the driver time to finish
# unwinding before we load again. Cold-start callers just see the friendly
# 503 warming response a little longer — nothing breaks.
STOP_START_COOLDOWN_S = int(os.environ.get("STOP_START_COOLDOWN_S", "45"))

HEALTH_PATH = "/v1/models"        # llama endpoint we poll to confirm it's up
# Cold-load reality check (measured 2026-08-10, Q5_K_M 25GB on R9700):
#   warm page cache      ~17s
#   cold page cache      ~100-120s   ← blew the old 60s timeout, orphaned 26GB
#   cold + RAM pressure  ~13min      (21:14 start; codex session had RAM pegged)
# 300s covers the honest cold case. The pathological case is handled by
# stop-on-timeout below — never leave a loading orphan behind.
HEALTH_TIMEOUT_S = int(os.environ.get("GUARDIAN_HEALTH_TIMEOUT_S", "300"))
HEALTH_POLL_S = 1                 # how often to poll while waiting

# GPU placement probe. 2026-08-11: after hard llama teardowns the AMD Vulkan
# ICD can wedge while Device Manager still shows the R9700 healthy. llama then
# silently falls back to CPU, loads fine, answers /v1/health 200 — and serves
# ~10 t/s. A health check cannot see this, so after every successful start we
# time a tiny generation: the R9700 does 120-140 t/s, CPU ~10. The threshold
# sits well between. Set to 0 to disable the probe.
GPU_PROBE_MIN_TPS = float(os.environ.get("GUARDIAN_GPU_PROBE_MIN_TPS", "40"))
GPU_PROBE_TIMEOUT_S = 30          # CPU worst case: ~20 gen tokens at 10 t/s + prompt

PM2_APP = "qwen3-llama-vulkan"    # PM2 process name for llama-server (R9700 Vulkan build)

# Hermes-decided durable job queue.  It is intentionally loopback-only by
# default because the guardian also listens on Tailscale-facing interfaces.
QUEUE_DB_PATH = os.environ.get(
    "GUARDIAN_QUEUE_DB", str(Path(__file__).resolve().parent.parent / "logs" / "guardian-queue.sqlite3")
)
QUEUE_MAX_REQUEST_BYTES = int(os.environ.get("GUARDIAN_QUEUE_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
QUEUE_MAX_RESULT_BYTES = int(os.environ.get("GUARDIAN_QUEUE_MAX_RESULT_BYTES", str(2 * 1024 * 1024)))
QUEUE_MODEL_ALIAS = os.environ.get("GUARDIAN_QUEUE_MODEL", "local-llm")
QUEUE_ALLOW_REMOTE = os.environ.get("GUARDIAN_QUEUE_ALLOW_REMOTE", "false").lower() == "true"
QUEUE_AUTH_TOKEN = os.environ.get("GUARDIAN_QUEUE_TOKEN", "")
QUEUE_JOB_TIMEOUT_S = max(30, int(os.environ.get("GUARDIAN_QUEUE_JOB_TIMEOUT_S", "900")))

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setLevel(logging.INFO)
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_stdout_handler, _stderr_handler],
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# aiohttp access log is noisy — silence it, we log transitions ourselves.
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
log = logging.getLogger("guardian")

# ─────────────────────────────────────────────────────────────────────────────
#  GUARDIAN STATE
# ─────────────────────────────────────────────────────────────────────────────


class Guardian:
    """Holds the shared state that the proxy + background tasks coordinate on."""

    def __init__(self) -> None:
        # Active in-flight request count. The idle reaper will NOT stop llama
        # while this is > 0 — protects mid-generation requests.
        self.active_requests = 0

        # Epoch seconds of the last completed request. Seeded to "now" so we
        # don't immediately reap a freshly-booted llama that hasn't been hit.
        self.last_request_time = time.time()

        # Serialize llama start attempts. If 3 requests arrive while llama is
        # cold, only ONE pm2 start fires; the others wait on this lock.
        self.start_lock = asyncio.Lock()

        # llama.cpp is configured with --parallel 1. This lock serializes both
        # normal proxied generations and Hermes-approved queued work.
        self.generation_lock = asyncio.Lock()

        # Cached "is llama up" state. Avoids hammering the health endpoint
        # on every single proxied request.
        self._llama_up = False

        # Timestamp of the last enforcer stale-PID recovery. Prevents the
        # enforcer from entering a kill loop when transient PID mismatches
        # occur during model load (child processes, MTP draft contexts, etc.).
        self.last_enforcer_recovery = 0.0

        # Epoch seconds of the last successful llama stop. Every start path
        # waits out STOP_START_COOLDOWN_S from this mark (see _gpu_cooldown)
        # so the AMD driver never sees a rapid VRAM unload→load cycle.
        self.last_stop_time = 0.0

        self.job_store = JobStore(QUEUE_DB_PATH)
        self.hermes_decider = HermesDecider()
        self.queue_event = asyncio.Event()

    @property
    def llama_target(self) -> str:
        """Base URL of the backing llama-server."""
        return f"http://{LLAMA_HOST}:{LLAMA_PORT}"

    async def is_llama_up(self) -> bool:
        """Inference-ready probe of the backing llama-server.

        A plain TCP connect is NOT sufficient: llama-server opens its listening
        socket before the GGUF finishes mmap-loading into VRAM, and answers
        503 ("Loading model") to any completion sent in that window. Hitting
        /v1/health (the canonical llama-server load-status endpoint) and
        requiring HTTP 200 ensures we only treat the server as up once it can
        actually serve generations. This is what makes the queue's cold-start
        path reliable. See llama.cpp docs: /v1/health returns 503 while
        `loading`/`downloading`, 200 {"status":"ok"} when `ready`.
        """
        client = getattr(self, "_client", None)
        if client is None:
            # Defensive: should never happen post-startup, but never block.
            return False
        try:
            async with client.get(
                f"{self.llama_target}/v1/health", timeout=ClientTimeout(total=2.0)
            ) as resp:
                return resp.status == 200
        except (ClientError, asyncio.TimeoutError, OSError):
            return False

    async def wait_until_llama_up(self, timeout: float = HEALTH_TIMEOUT_S) -> bool:
        """Poll llama until it accepts connections or timeout. Returns success."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.is_llama_up():
                self._llama_up = True
                return True
            await asyncio.sleep(HEALTH_POLL_S)
        return False


guardian = Guardian()


# ─────────────────────────────────────────────────────────────────────────────
#  PM2 HELPERS
# ─────────────────────────────────────────────────────────────────────────────


async def pm2(action: str) -> tuple[bool, str]:
    """Run a pm2 start/stop command. Returns (success, output).

    We shell out to pm2 rather than using the programmatic API because pm2's
    daemon is the source of truth for process state — using the CLI keeps us
    consistent with everything else on this box.

    Windows note: `pm2` is installed as `pm2.cmd` (a batch wrapper around the
    Node script). create_subprocess_exec can't resolve bare `pm2` on Windows,
    so we resolve the full path to pm2.cmd at startup and use that. On Linux/
    macOS the `.cmd` suffix doesn't exist so we fall back to `pm2`.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *_pm2_cmd(action),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out_bytes = await proc.communicate()
        stdout = out_bytes[0].decode(errors="replace").strip() if out_bytes[0] else ""
        success = proc.returncode == 0
        if success and action == "stop":
            guardian.last_stop_time = time.time()
        log.info(f"pm2 {action} {PM2_APP} -> rc={proc.returncode}")
        return success, stdout
    except Exception as e:
        log.error(f"pm2 {action} failed: {e}")
        return False, str(e)


def _pm2_cmd(action: str) -> list[str]:
    """Build the pm2 command for the current platform.

    On Windows, npm installs shims as <name>.cmd batch wrappers. Python's
    subprocess can't execute these via the bare name (it doesn't consult
    PATHEXT), so we look up the .cmd path once and reuse it.
    """
    if sys.platform == "win32":
        # Resolve once, cache on the function attribute.
        if not hasattr(_pm2_cmd, "_pm2_exe"):
            import shutil

            pm2_path = shutil.which("pm2") or shutil.which("pm2.cmd")
            if not pm2_path:
                raise FileNotFoundError(
                    "pm2 not found on PATH (looked for pm2 / pm2.cmd)"
                )
            _pm2_cmd._pm2_exe = pm2_path
            log.info(f"resolved pm2 executable: {pm2_path}")
        return [_pm2_cmd._pm2_exe, action, PM2_APP]
    return ["pm2", action, PM2_APP]


def _pm2_exe_path() -> str:
    """Resolved pm2 executable path (uses same cache as _pm2_cmd)."""
    if sys.platform == "win32":
        if not hasattr(_pm2_cmd, "_pm2_exe"):
            import shutil
            pm2_path = shutil.which("pm2") or shutil.which("pm2.cmd")
            if not pm2_path:
                raise FileNotFoundError("pm2 not found on PATH")
            _pm2_cmd._pm2_exe = pm2_path
        return _pm2_cmd._pm2_exe
    return "pm2"


def _pm2_get_llama_pid() -> tuple[str, int]:
    """Query PM2 for qwen3-llama's status and PID.

    Source of truth for the single-llama enforcer. Returns (status, pid):
      - status: 'online' | 'stopped' | 'errored' | 'unknown'
      - pid: PM2-tracked PID, or 0 if not online
    """
    import subprocess
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [_pm2_exe_path(), "jlist"],
            capture_output=True, text=True, timeout=10,
            creationflags=creationflags,
        )
        data = json.loads(result.stdout)
        for app in data:
            if app.get("name") == PM2_APP:
                env = app.get("pm2_env") or {}
                status = env.get("status", "unknown")
                pid = int(app.get("pid") or 0)
                return status, pid
        return "unknown", 0
    except Exception as e:
        log.warning(f"pm2 jlist failed: {e}")
        return "unknown", 0


def _llama_pids() -> list[int]:
    """Return every running llama-server PID without relying on PM2 state."""
    import subprocess

    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids = []
    for line in result.stdout.strip().split("\n"):
        parts = line.replace('"', '').split(",")
        if len(parts) >= 2 and parts[1].strip().isdigit():
            pids.append(int(parts[1].strip()))
    return pids


def _llama_listener_pid() -> int | None:
    """Return the PID listening on llama's internal port, if there is one."""
    import subprocess

    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if (
            len(fields) >= 5
            and fields[3].upper() == "LISTENING"
            and fields[1].endswith(f":{LLAMA_PORT}")
            and fields[4].isdigit()
        ):
            return int(fields[4])
    return None


async def _gpu_cooldown(reason: str) -> None:
    """Wait out STOP_START_COOLDOWN_S since the last llama stop.

    The R9700 Windows driver TDR'd on a fast VRAM unload→load cycle; every
    start path funnels through here so that can't recur. No-op when the last
    stop is old (the common case).
    """
    remaining = STOP_START_COOLDOWN_S - (time.time() - guardian.last_stop_time)
    if remaining > 0:
        log.info(f"GPU cooldown: waiting {remaining:.0f}s before start ({reason})")
        await asyncio.sleep(remaining)


async def _gpu_placement_ok(reason: str) -> bool:
    """Confirm a freshly started llama is generating at GPU speed.

    Sends one tiny non-streaming completion straight to 8081 and reads
    timings.predicted_per_second. Only a *confirmed slow* generation returns
    False — probe errors or missing timings assume OK, so a flaky probe can
    never take down a healthy server.
    """
    if GPU_PROBE_MIN_TPS <= 0:
        return True
    payload = {
        "model": QUEUE_MODEL_ALIAS,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 24,
        "stream": False,
    }
    try:
        async with guardian._client.post(
            f"{guardian.llama_target}/v1/chat/completions",
            json=payload,
            timeout=ClientTimeout(total=GPU_PROBE_TIMEOUT_S),
        ) as resp:
            body = await resp.json()
    except Exception as e:
        log.warning(f"GPU placement probe errored ({reason}): {e} — assuming OK")
        return True
    tps = ((body or {}).get("timings") or {}).get("predicted_per_second")
    if tps is None:
        log.warning(f"GPU placement probe: no timings in response ({reason}) — assuming OK")
        return True
    if tps >= GPU_PROBE_MIN_TPS:
        log.info(f"GPU placement probe OK ({reason}): {tps:.1f} t/s")
        return True
    log.critical(
        f"GPU placement probe FAILED ({reason}): {tps:.1f} t/s < {GPU_PROBE_MIN_TPS} — "
        f"llama is almost certainly on CPU (wedged AMD Vulkan ICD; Device Manager "
        f"will still show the card healthy). Recovery: double "
        f"`pnputil /restart-device` on the R9700 instance, see "
        f"benchmarks/bench-q4-vs-q5-perf-20260811.md"
    )
    return False


async def ensure_llama_started(reason: str) -> bool:
    """Start llama if it's down. Dedups concurrent starts via a lock.

    Returns True if llama is (or became) up within the health-check window.
    """
    # Fast path: already up.
    if await guardian.is_llama_up():
        return True

    # Slow path: serialize. Only one caller actually issues pm2 start;
    # everyone else waits on the lock, then re-checks.
    async with guardian.start_lock:
        # Re-check inside the lock — a concurrent caller may have started it.
        if await guardian.is_llama_up():
            log.info(f"llama already up ({reason}) — started by concurrent caller")
            return True

        pm2_status, pm2_pid = _pm2_get_llama_pid()
        if pm2_status == "online" and pm2_pid > 0:
            # PM2 has already launched llama, but the model has not finished
            # loading yet. `pm2 start` would restart that in-flight load.
            log.info(
                f"llama is already loading ({reason}; pm2 pid={pm2_pid}) — waiting"
            )
        else:
            await _gpu_cooldown(reason)
            log.info(f"starting llama ({reason})...")
            await pm2("start")

        # Wait for llama to finish loading the model (the slow part).
        if await guardian.wait_until_llama_up():
            if not await _gpu_placement_ok(reason):
                # Serving 10 t/s off a CPU fallback is worse than being down:
                # every queue job would burn its timeout. Fail loudly instead.
                await pm2("stop")
                return False
            # A guardian may have been alive while llama was idle for hours.
            # Treat a completed cold start (including a pre-warm) as activity
            # so the idle reaper grants the fresh server its full window.
            guardian.last_request_time = time.time()
            log.info(f"llama is up ({reason})")
            return True
        # Stop what we started. Without this, a load that outlives the health
        # window keeps loading unowned and parks ~26GB of commit on the box
        # until the idle reaper fires (observed 2026-08-10, twice: the model
        # finished loading ~50s after the caller had already been failed).
        # We hold start_lock here, so no concurrent caller is mid-start.
        log.error(
            f"llama failed to come up within {HEALTH_TIMEOUT_S}s ({reason}) — "
            f"stopping the in-flight load so it cannot orphan"
        )
        await pm2("stop")  # pm2() stamps last_stop_time, so cooldown applies
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  STREAMING PROXY  (port 8080 → 8081)
# ─────────────────────────────────────────────────────────────────────────────

# Hop-by-hop headers that must not be forwarded (RFC 7230 §6.1).
# The proxy is the connection terminator on both sides, so these reset.
HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    )
)


WARMING_MSG = (
    "Qwen is getting dressed — model is loading onto the GPU. "
    "Wait ~20–40 seconds and send your request again."
)


def warming_response(extra=None) -> web.Response:
    """Friendly 503 for cold-start — not a scary error code dump."""
    msg = WARMING_MSG if not extra else f"{WARMING_MSG} ({extra})"
    return web.json_response(
        {
            "error": {
                "message": msg,
                "type": "llama_warming",
                "code": "llama_warming",
            },
            "message": msg,
        },
        status=503,
        headers={"Retry-After": "30"},
    )


def _queue_authorized(request: web.Request) -> bool:
    """Queue control is local-only unless an explicit token is configured."""
    remote = request.remote or ""
    if remote in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        return True
    if not QUEUE_ALLOW_REMOTE or not QUEUE_AUTH_TOKEN:
        return False
    authorization = request.headers.get("Authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip()
    return bool(supplied) and hmac.compare_digest(supplied, QUEUE_AUTH_TOKEN)


def _queue_forbidden() -> web.Response:
    return web.json_response(
        {"error": {"message": "Queue endpoints require loopback or a configured bearer token.", "code": "queue_forbidden"}},
        status=403,
    )


async def _validate_queue_submission(request: web.Request) -> tuple[str, str, dict, dict]:
    """Validate and normalize a safe, non-streaming queued completion."""
    if request.content_length and request.content_length > QUEUE_MAX_REQUEST_BYTES:
        raise ValueError(f"Queue request exceeds {QUEUE_MAX_REQUEST_BYTES} bytes.")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Queue request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Queue request body must be a JSON object.")

    source = payload.get("source", "unknown")
    if not isinstance(source, str) or not source.strip() or len(source) > 80:
        raise ValueError("source must be a non-empty string of at most 80 characters.")
    idempotency_key = payload.get("idempotency_key") or request.headers.get("Idempotency-Key")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 160:
        raise ValueError("idempotency_key is required and must be at most 160 characters.")

    decision_context = payload.get("decision_context")
    if not isinstance(decision_context, dict):
        raise ValueError("decision_context must be an object for Hermes to classify.")
    summary = decision_context.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 12000:
        raise ValueError("decision_context.summary is required and must be at most 12000 characters.")

    completion = payload.get("request")
    if not isinstance(completion, dict) or not isinstance(completion.get("messages"), list) or not completion["messages"]:
        raise ValueError("request.messages must be a non-empty OpenAI-compatible message array.")
    if completion.get("stream") is True:
        raise ValueError("Queued jobs must use stream=false so their result can be durable and pollable.")
    if completion.get("tools") or completion.get("tool_choice"):
        raise ValueError("Queued jobs return a patch/result; model tools are not permitted.")
    if len(json.dumps(completion, separators=(",", ":")).encode("utf-8")) > QUEUE_MAX_REQUEST_BYTES:
        raise ValueError(f"Queued completion exceeds {QUEUE_MAX_REQUEST_BYTES} bytes.")

    # Do not let a caller silently route a queue job to an arbitrary model.
    completion = json.loads(json.dumps(completion))
    completion["model"] = QUEUE_MODEL_ALIAS
    completion["stream"] = False
    return idempotency_key.strip(), source.strip(), decision_context, completion


async def queue_submit(request: web.Request) -> web.Response:
    """Ask Hermes to route a candidate task, then enqueue Qwen work if approved."""
    if not _queue_authorized(request):
        return _queue_forbidden()
    try:
        idempotency_key, source, context, completion = await _validate_queue_submission(request)
    except ValueError as exc:
        return web.json_response({"error": {"message": str(exc), "code": "invalid_queue_job"}}, status=400)

    existing = guardian.job_store.get_by_idempotency_key(idempotency_key)
    if existing:
        return web.json_response({"idempotent": True, **existing.as_api()})

    try:
        decision = await guardian.hermes_decider.decide(context, guardian.job_store.summary())
    except HermesDecisionError as exc:
        log.warning(f"Hermes declined to decide queue routing: {exc}")
        return web.json_response(
            {"error": {"message": str(exc), "code": "hermes_decision_unavailable"}}, status=503
        )

    guardian.job_store.log_decision(source, decision, context)
    if decision["route"] != "queue_qwen":
        return web.json_response({"status": "not_queued", "decision": decision})

    job, created = guardian.job_store.submit(
        idempotency_key=idempotency_key,
        source=source,
        priority=decision["priority"],
        request=completion,
        decision=decision,
    )
    guardian.queue_event.set()
    log.info(f"queue accepted {job.job_id} from {source} at priority {job.priority}")
    return web.json_response(job.as_api(), status=202 if created else 200)


async def queue_status(request: web.Request) -> web.Response:
    if not _queue_authorized(request):
        return _queue_forbidden()
    job = guardian.job_store.get(request.match_info["job_id"])
    if not job:
        return web.json_response({"error": {"message": "Queue job not found.", "code": "queue_job_not_found"}}, status=404)
    return web.json_response(job.as_api())


async def queue_cancel(request: web.Request) -> web.Response:
    if not _queue_authorized(request):
        return _queue_forbidden()
    job = guardian.job_store.get(request.match_info["job_id"])
    if not job:
        return web.json_response({"error": {"message": "Queue job not found.", "code": "queue_job_not_found"}}, status=404)
    if job.status != "queued":
        return web.json_response(
            {"error": {"message": f"Only queued jobs can be cancelled (currently {job.status}).", "code": "queue_not_cancellable"}},
            status=409,
        )
    return web.json_response(guardian.job_store.cancel(job.job_id).as_api())


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward a single request to llama, streaming the response back.

    Three things make this non-trivial:
      1. SSE streaming — llama emits tokens as they're generated. We must
         stream chunks back to the client immediately, not buffer the whole
         response. (aiohttp's StreamResponse + async iteration handles this.)
      2. Cold start — ONLY for real generation (POST .../chat/completions or
         .../completions). Metadata probes (/v1/models, /metrics) never start
         llama — MCC dashboard polls those every ~15s and must not wake GPU.
      3. Active-request accounting — we bump a counter so the idle reaper
         knows not to kill llama mid-generation.
    """
    # Real work = POST that actually generates tokens. Everything else is
    # metadata (status panel, metrics scrapes, health).
    is_real_work = (
        request.method == "POST"
        and ("/chat/completions" in request.path or "/completions" in request.path)
    )

    # If llama is down: only generation may cold-start it. Probes get a clean
    # offline response so the dashboard shows offline without loading the GGUF.
    if not await guardian.is_llama_up():
        if not is_real_work:
            # 503 so MCC getLlamaStatus → state offline (empty 200 list would look "online").
            return web.json_response(
                {
                    "error": {
                        "message": "Local Qwen is asleep (idle). Send a chat to wake it.",
                        "type": "llama_offline",
                        "code": "llama_offline",
                    },
                    "message": "Local Qwen is asleep (idle). Send a chat to wake it.",
                },
                status=503,
            )
        # Real work while cold: kick the load, return a friendly 503 immediately.
        # Holding the HTTP request for 30–60s of GGUF load usually just produces
        # client timeouts / ugly status codes. Agents/orchestrator/MCC retry once
        # (or the human resends) — by then Qwen is dressed.
        log.info("cold generation request — dressing Qwen (async start + friendly 503)")
        asyncio.create_task(ensure_llama_started(reason="request"))
        return warming_response()

    # Build the upstream request.
    upstream_url = f"{guardian.llama_target}{request.path_qs}"
    # Strip hop-by-hop headers, keep everything else (auth, content-type, etc.)
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    body = await request.read()

    # Length-retry applies only to non-streaming chat completions we can parse.
    req_json = None
    if is_real_work and "/chat/completions" in request.path:
        try:
            parsed = json.loads(body) if body else None
            if isinstance(parsed, dict) and parsed.get("stream") is not True:
                req_json = parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    async def forward_with_length_retry() -> web.StreamResponse:
        """Buffered (non-streaming) forward that retries reasoning-loop deaths.

        finish_reason=length on this model means the trajectory looped and
        will never finish — more tokens don't help, a different seed does.
        """
        guardian.active_requests += 1
        guardian.last_request_time = time.time()
        # aiohttp recomputes Content-Length from data; the original header
        # would be wrong for a reseeded (different-length) retry body.
        retry_headers = {k: v for k, v in fwd_headers.items() if k.lower() != "content-length"}
        try:
            timeout = ClientTimeout(total=None, sock_connect=10, sock_read=600)
            attempt_body = body
            status = 502
            raw = b""
            up_headers: dict = {}
            for attempt in range(1 + LENGTH_RETRY_MAX):
                async with guardian._client.request(
                    request.method, upstream_url, headers=retry_headers,
                    data=attempt_body, timeout=timeout,
                ) as up:
                    status = up.status
                    up_headers = dict(up.headers)
                    raw = await up.read()
                guardian.last_request_time = time.time()
                if status != 200 or attempt >= LENGTH_RETRY_MAX:
                    break
                try:
                    result = json.loads(raw)
                    finish = result["choices"][0].get("finish_reason")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    break
                if finish != "length":
                    break
                nudged = dict(req_json)
                base_seed = nudged.get("seed") if isinstance(nudged.get("seed"), int) else 42
                nudged["seed"] = base_seed + 1000003 * (attempt + 1)
                attempt_body = json.dumps(nudged).encode()
                log.warning(
                    f"finish_reason=length (reasoning loop?) — "
                    f"retry {attempt + 1}/{LENGTH_RETRY_MAX} with seed {nudged['seed']}"
                )
            resp_headers = {
                k: v for k, v in up_headers.items()
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-length"
            }
            return web.Response(status=status, body=raw, headers=resp_headers)
        except (ClientError, asyncio.TimeoutError) as e:
            guardian._llama_up = False
            log.warning(f"upstream error: {e}")
            err = str(e).lower()
            if any(x in err for x in ("connect", "refused", "closing", "reset", "timeout", "unreachable")):
                return warming_response(str(e)[:80])
            return web.json_response(
                {
                    "error": {
                        "message": f"Local Qwen hiccup: {e}",
                        "type": "upstream_error",
                        "code": "upstream_error",
                    },
                    "message": f"Local Qwen hiccup: {e}",
                },
                status=502,
            )
        finally:
            guardian.active_requests -= 1
            guardian.last_request_time = time.time()

    async def forward() -> web.StreamResponse:
        guardian.active_requests += 1
        if is_real_work:
            guardian.last_request_time = time.time()
        try:
            # Long timeout — llama generation can take minutes for long outputs.
            timeout = ClientTimeout(total=None, sock_connect=10, sock_read=600)
            async with guardian._client.request(
                request.method, upstream_url, headers=fwd_headers, data=body, timeout=timeout
            ) as upstream_resp:
                # Stream the response back chunk-by-chunk.
                resp = web.StreamResponse(
                    status=upstream_resp.status,
                    reason=upstream_resp.reason,
                )
                for k, v in upstream_resp.headers.items():
                    if k.lower() not in HOP_BY_HOP:
                        resp.headers[k] = v
                await resp.prepare(request)

                async for chunk in upstream_resp.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except (ClientError, asyncio.TimeoutError) as e:
            guardian._llama_up = False
            log.warning(f"upstream error: {e}")
            # During cold start the port can flap / refuse briefly — sound human.
            err = str(e).lower()
            if any(x in err for x in ("connect", "refused", "closing", "reset", "timeout", "unreachable")):
                return warming_response(str(e)[:80])
            return web.json_response(
                {
                    "error": {
                        "message": f"Local Qwen hiccup: {e}",
                        "type": "upstream_error",
                        "code": "upstream_error",
                    },
                    "message": f"Local Qwen hiccup: {e}",
                },
                status=502,
            )
        finally:
            guardian.active_requests -= 1
            if is_real_work:
                guardian.last_request_time = time.time()

    if is_real_work:
        # A direct generation cannot bypass the one-slot queue and evict its
        # context. It waits here rather than competing with queued work.
        async with guardian.generation_lock:
            if req_json is not None:
                return await forward_with_length_retry()
            return await forward()
    return await forward()


# ─────────────────────────────────────────────────────────────────────────────
#  HERMES-DECIDED QWEN QUEUE
# ─────────────────────────────────────────────────────────────────────────────


async def run_queued_job(job: QueueJob) -> None:
    """Run one durable job while holding the same one-slot generation lock."""
    log.info(f"queue running {job.job_id} from {job.source}")
    try:
        async with guardian.generation_lock:
            if not await ensure_llama_started(reason=f"queue:{job.job_id}"):
                guardian.job_store.fail(job.job_id, "Qwen did not become healthy before the queue timeout.")
                return

            guardian.active_requests += 1
            guardian.last_request_time = time.time()
            try:
                timeout = ClientTimeout(
                    total=QUEUE_JOB_TIMEOUT_S + 15, sock_connect=10, sock_read=QUEUE_JOB_TIMEOUT_S
                )
                async with guardian._client.post(
                    f"{guardian.llama_target}/v1/chat/completions", json=job.request, timeout=timeout
                ) as upstream_resp:
                    body = await upstream_resp.read()
                    if len(body) > QUEUE_MAX_RESULT_BYTES:
                        guardian.job_store.fail(
                            job.job_id,
                            f"Qwen result exceeded durable queue limit of {QUEUE_MAX_RESULT_BYTES} bytes.",
                        )
                        return
                    text = body.decode(errors="replace")
                    if upstream_resp.status >= 400:
                        guardian.job_store.fail(job.job_id, f"Qwen returned HTTP {upstream_resp.status}: {text[:1200]}")
                        return
                    try:
                        result = json.loads(text)
                    except json.JSONDecodeError:
                        result = {"text": text}
                    guardian.job_store.finish(
                        job.job_id,
                        {"upstream_status": upstream_resp.status, "response": result},
                    )
                    log.info(f"queue succeeded {job.job_id}")
            except (ClientError, asyncio.TimeoutError) as exc:
                guardian._llama_up = False
                guardian.job_store.fail(job.job_id, f"Qwen request failed: {exc}")
                log.warning(f"queue failed {job.job_id}: {exc}")
            finally:
                guardian.active_requests -= 1
                guardian.last_request_time = time.time()
    except Exception as exc:
        # Never leave a claimed job in running state due to a guardian bug.
        guardian.job_store.fail(job.job_id, f"Guardian queue worker failed: {exc}")
        log.exception(f"queue worker crashed while running {job.job_id}")


async def queue_worker(app: web.Application) -> None:
    """Claim queued work in priority/FIFO order and retain it across restarts."""
    recovered = guardian.job_store.recover_interrupted()
    if recovered:
        log.warning(f"queue requeued {recovered} interrupted job(s) after guardian restart")
    log.info("Hermes-decided Qwen queue worker started")
    try:
        while True:
            job = guardian.job_store.claim_next()
            if job:
                await run_queued_job(job)
                continue

            guardian.queue_event.clear()
            # Prevent a submit between claim_next() and clear() from being
            # missed and waiting for the next polling timeout.
            if guardian.job_store.has_queued_work():
                continue
            try:
                await asyncio.wait_for(guardian.queue_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        log.info("Hermes-decided Qwen queue worker cancelled")
        raise


async def guardian_sleep(request: web.Request) -> web.Response:
    """Stop an idle Qwen cleanly after a verification run or manual drain."""
    if not _queue_authorized(request):
        return _queue_forbidden()
    summary = guardian.job_store.summary()
    if guardian.active_requests or summary["queued"] or summary["running"]:
        return web.json_response(
            {
                "error": {
                    "message": "Qwen cannot sleep while a generation or queue job is active.",
                    "code": "queue_not_idle",
                }
            },
            status=409,
        )

    async with guardian.generation_lock:
        async with guardian.start_lock:
            if guardian.active_requests:
                return web.json_response(
                    {"error": {"message": "Qwen became active while draining.", "code": "queue_not_idle"}},
                    status=409,
                )
            if not await guardian.is_llama_up():
                return web.json_response({"status": "already_asleep", "llama_up": False})
            stopped, detail = await pm2("stop")
            if not stopped:
                return web.json_response(
                    {"error": {"message": f"PM2 could not stop Qwen: {detail[:600]}", "code": "pm2_stop_failed"}},
                    status=502,
                )
            guardian._llama_up = False
            guardian.last_request_time = time.time()
            log.info("Qwen put to sleep by local guardian control request")
            return web.json_response({"status": "asleep", "llama_up": False})


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND TASK: PRE-WARM ON SERVICE STARTUP
# ─────────────────────────────────────────────────────────────────────────────


async def _port_open(host: str, port: int) -> bool:
    """True if something is listening on host:port."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=1.0
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def prewarm_watcher(app: web.Application) -> None:
    """Watch the MCC port. On down→up transition, pre-warm llama.

    Why this matters: at boot, PM2 resurrects guardian + MCC at roughly
    the same time. By the time MCC is listening, the guardian sees it and
    starts llama. So llama is hot ~20s after boot, BEFORE any real request
    arrives — no cold-start latency for users.

    Same logic applies if MCC is manually restarted later.
    """
    # Track the last-seen state of each watched port so we only fire on
    # the down→up EDGE, not every poll while it stays up.
    last_state: dict[int, bool] = {p: False for p in PREWARM_PORTS}

    log.info(f"prewarm watcher started — watching ports {PREWARM_PORTS}")
    try:
        while True:
            for port in PREWARM_PORTS:
                up_now = await _port_open("127.0.0.1", port)
                was_up = last_state[port]
                if up_now and not was_up:
                    log.info(f"port {port} came up — pre-warming llama")
                    # Fire-and-forget: don't block the watcher on llama startup.
                    # ensure_llama_started is idempotent + deduped.
                    asyncio.create_task(ensure_llama_started(reason=f"prewarm:{port}"))
                last_state[port] = up_now
            await asyncio.sleep(PREWARM_POLL_S)
    except asyncio.CancelledError:
        log.info("prewarm watcher cancelled")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND TASK: SLOT ACTIVITY POLLER
# ─────────────────────────────────────────────────────────────────────────────


async def slot_activity_poller(app: web.Application) -> None:
    """Poll llama /slots to detect direct activity (bypassing the proxy).

    When pi or any other tool hits 8081 directly, the guardian's proxy handler
    never fires — so last_request_time never updates and the idle reaper kills
    llama mid-generation. This task polls the slots endpoint every few seconds;
    if any slot is actively processing, it bumps last_request_time.
    """
    poll_s = 5  # check every 5s — slots status is near-instant
    log.info("slot activity poller started")
    try:
        while True:
            await asyncio.sleep(poll_s)
            if not await guardian.is_llama_up():
                continue
            try:
                async with guardian._client.get(
                    f"{guardian.llama_target}/slots", timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    if resp.status == 200:
                        slots = await resp.json()
                        for s in slots:
                            if s.get("is_processing"):
                                guardian.last_request_time = time.time()
                                break
            except Exception:
                pass  # transient — slot poll failing shouldn't crash the guardian
    except asyncio.CancelledError:
        log.info("slot activity poller cancelled")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND TASK: SINGLE LLAMA ENFORCER
# ─────────────────────────────────────────────────────────────────────────────


async def enforce_single_llama(app: web.Application) -> None:
    """Ensure only ONE llama-server process exists at any time.

    Runs on startup to kill strays, then polls periodically. PM2 is the
    source of truth for which llama-server.exe is authoritative:
      - PM2 online → keep the PM2-tracked PID, kill every other
      - PM2 stopped → every running llama-server.exe is an orphan
      - PM2 unknown/errored → make no destructive change until PM2 is observable

    Why: an orphan llama-server holding port 8081 will refuse PM2's fresh
    spawns' bind attempts. The old logic "keep whoever holds :8081" made
    the orphan authoritative and killed the healthy PM2 process, which
    PM2 then autorestarted — 30 s later the enforcer killed it again. The
    crash-loop stopped only on manual intervention. PM2 as source of
    truth breaks that feedback loop.
    """
    import subprocess

    # Run once at startup immediately, then poll every 30s.
    FIRST_RUN = True
    POLL_S = 30
    RECOVERY_COOLDOWN_S = 300  # 5 min — prevent enforcer kill loops
    log.info("single-llama enforcer started")

    async def _enforce():
        nonlocal FIRST_RUN
        try:
            pids = _llama_pids()

            if not pids:
                if FIRST_RUN:
                    log.info("enforcer: no llama processes found on startup")
                FIRST_RUN = False
                return

            pm2_status, pm2_pid = _pm2_get_llama_pid()

            if pm2_status == "online" and pm2_pid > 0:
                if pm2_pid in pids:
                    keep_pid = pm2_pid
                else:
                    # Transient race: PM2 says online but its PID isn't in the
                    # tasklist snapshot yet. Skip this tick — resolves on the next.
                    log.debug(
                        f"enforcer: pm2 pid {pm2_pid} not in tasklist yet, skipping"
                    )
                    FIRST_RUN = False
                    return
            elif pm2_status == "stopped":
                keep_pid = None  # every running llama-server.exe is an orphan
            else:
                # A failed PM2 query must never make the enforcer kill a
                # possibly healthy server. Wait for PM2 to become observable.
                log.warning(
                    f"enforcer: PM2 state is {pm2_status}; leaving llama PIDs alone"
                )
                FIRST_RUN = False
                return

            listener_pid = _llama_listener_pid()
            if (
                pm2_status == "online"
                and keep_pid is not None
                and listener_pid is not None
                and listener_pid != keep_pid
            ):
                # A stale process owns :8081 while PM2 is trying to replace it.
                # BUT: transient PID mismatches during model load (child
                # processes, MTP draft contexts) are normal and must NOT
                # trigger recovery. Only recover if the model is actually
                # broken AND we haven't recovered recently.
                elapsed = time.time() - guardian.last_enforcer_recovery
                if elapsed < RECOVERY_COOLDOWN_S:
                    log.info(
                        f"enforcer: port {LLAMA_PORT} PID mismatch "
                        f"(listener={listener_pid}, pm2={keep_pid}) "
                        f"but recovery cooldown active ({elapsed:.0f}s ago); skipping"
                    )
                    FIRST_RUN = False
                    return

                # Check if the model is actually serving. If it responds to
                # health checks, the PID mismatch is cosmetic — don't kill a
                # working server over a pidof artifact.
                listener_alive = False
                import aiohttp as _ah
                try:
                    async with guardian._client.get(
                        f"http://127.0.0.1:{LLAMA_PORT}/v1/health",
                        timeout=_ah.ClientTimeout(total=2.0),
                    ) as _resp:
                        listener_alive = _resp.status == 200
                except Exception:
                    pass

                if listener_alive:
                    log.info(
                        f"enforcer: port {LLAMA_PORT} PID mismatch "
                        f"(listener={listener_pid}, pm2={keep_pid}) "
                        f"but /v1/health is 200 — model is serving; skipping recovery"
                    )
                    FIRST_RUN = False
                    return

                # Model is NOT responding AND there's a stale PID. Recover.
                log.warning(
                    f"enforcer: port {LLAMA_PORT} is owned by stale PID {listener_pid} "
                    f"and /v1/health is NOT 200; recovering PM2 PID {keep_pid}"
                )
                async with guardian.start_lock:
                    await pm2("stop")
                    for stale_pid in _llama_pids():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(stale_pid)],
                            capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    for _ in range(20):
                        if not _llama_pids():
                            break
                        await asyncio.sleep(1)
                    if _llama_pids():
                        log.error("enforcer: stale llama process survived recovery; not restarting")
                        FIRST_RUN = False
                        return
                    await _gpu_cooldown("enforcer recovery")
                    await pm2("start")
                guardian.last_enforcer_recovery = time.time()
                FIRST_RUN = False
                return

            killed = 0
            for pid in pids:
                if pid != keep_pid:
                    log.warning(
                        f"enforcer: killing orphan llama PID {pid} "
                        f"(pm2 status={pm2_status}, pm2 pid={pm2_pid}, keep={keep_pid})"
                    )
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    killed += 1

            if FIRST_RUN:
                if keep_pid and killed == 0:
                    log.info(
                        f"enforcer: 1 llama process found (PID {keep_pid}, "
                        f"pm2 status={pm2_status})"
                    )
                elif killed:
                    log.info(f"enforcer: reaped {killed} orphan(s) on startup")
            FIRST_RUN = False
        except Exception as e:
            log.warning(f"enforcer check failed: {e}")

    try:
        while True:
            await _enforce()
            await asyncio.sleep(POLL_S)
    except asyncio.CancelledError:
        log.info("single-llama enforcer cancelled")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  BACKGROUND TASK: IDLE REAPER
# ─────────────────────────────────────────────────────────────────────────────


async def idle_reaper(app: web.Application) -> None:
    """Stop llama after IDLE_TIMEOUT_MIN of no activity.

    Activity is tracked three ways:
      1. Proxy traffic (requests through guardian on port 8080)
      2. Slot polling (direct usage on 8081 from pi or other tools)
      3. Active request count (mid-generation protection)

    Never fires while active_requests > 0.
    """
    timeout_s = IDLE_TIMEOUT_MIN * 60
    log.info(f"idle reaper started — timeout {IDLE_TIMEOUT_MIN} min")
    try:
        while True:
            await asyncio.sleep(IDLE_POLL_S)
            if guardian.active_requests > 0:
                continue  # something is in flight — leave it alone
            if not await guardian.is_llama_up():
                continue  # already stopped — nothing to do
            idle_for = time.time() - guardian.last_request_time
            if idle_for >= timeout_s:
                log.info(
                    f"llama idle for {idle_for/60:.1f} min — stopping "
                    f"(threshold {IDLE_TIMEOUT_MIN} min)"
                )
                await pm2("stop")
                guardian._llama_up = False
                # Reset last_request_time so we don't immediately re-fire.
                guardian.last_request_time = time.time()
    except asyncio.CancelledError:
        log.info("idle reaper cancelled")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  HEALTH ENDPOINT (for the guardian itself, not llama)
# ─────────────────────────────────────────────────────────────────────────────


async def guardian_health(request: web.Request) -> web.Response:
    """Lightweight liveness probe for the guardian process itself.

    PM2 / homelab-agent can hit this to confirm the proxy is alive without
    caring whether llama is currently up. (Llama's health is at /v1/models,
    which proxies through.)
    """
    llama_up = await guardian.is_llama_up()
    return web.json_response(
        {
            "status": "ok",
            "llama_up": llama_up,
            "active_requests": guardian.active_requests,
            "queue": guardian.job_store.summary(),
            "idle_seconds": int(time.time() - guardian.last_request_time),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
#  LISTENER WATCHDOG  (keeps port 8080 alive)
# ─────────────────────────────────────────────────────────────────────────────


def install_accept_failure_handler(
    loop: asyncio.AbstractEventLoop, suspect: asyncio.Event
) -> None:
    """Wake the watchdog the moment asyncio kills the listening socket.

    asyncio reports the fatal accept error through the loop exception handler
    rather than raising it anywhere we can catch, so this is the only hook that
    fires at the instant 8080 dies. Without it we'd wait up to a poll interval
    to notice. Everything else is left to the default handler.
    """
    default = loop.get_exception_handler()

    def handler(loop_, context):
        if context.get("message") == "Accept failed on a socket":
            suspect.set()
        if default is None:
            loop_.default_exception_handler(context)
        else:
            default(loop_, context)

    loop.set_exception_handler(handler)


async def listener_watchdog(site: web.TCPSite, suspect: asyncio.Event) -> None:
    """Confirm 8080 still accepts connections; re-arm it in place if not.

    Probes the proxy port on a timer, waking early when the accept handler
    above signals trouble. On a confirmed dead listener it stops and restarts
    the TCPSite, which builds a fresh listening socket through public aiohttp
    API — in-place recovery keeps the job queue, llama process tracking and
    idle timer intact, which a process restart would disturb. If re-arming
    fails LISTENER_REARM_MAX times we exit non-zero so PM2 rebuilds us; a dead
    gateway is worse than a restarted one.
    """
    consecutive_fail = 0
    rearm_failures = 0

    log.info(
        f"listener watchdog started — probing {PROXY_PORT} every {LISTENER_POLL_S}s"
    )
    try:
        while True:
            # Wake on the timer OR immediately if the accept loop just died.
            try:
                await asyncio.wait_for(suspect.wait(), timeout=LISTENER_POLL_S)
                log.error("listener watchdog: accept failure reported — checking 8080")
            except asyncio.TimeoutError:
                pass
            finally:
                suspect.clear()

            if await _port_open("127.0.0.1", PROXY_PORT):
                consecutive_fail = 0
                rearm_failures = 0
                continue

            consecutive_fail += 1
            log.warning(
                f"listener watchdog: {PROXY_PORT} not accepting "
                f"({consecutive_fail}/{LISTENER_FAIL_THRESHOLD})"
            )
            # One failed probe can just be load or a transient; require a repeat.
            if consecutive_fail < LISTENER_FAIL_THRESHOLD:
                continue

            log.error(f"listener watchdog: {PROXY_PORT} is DOWN — re-arming listener")
            try:
                await site.stop()
            except Exception as exc:
                # Expected when the socket is already closed under us; the
                # start() below is what actually matters.
                log.warning(f"listener watchdog: site.stop() failed: {exc}")
            try:
                await site.start()
            except Exception as exc:
                rearm_failures += 1
                log.error(
                    f"listener watchdog: re-arm failed "
                    f"({rearm_failures}/{LISTENER_REARM_MAX}): {exc}"
                )
            else:
                if await _port_open("127.0.0.1", PROXY_PORT):
                    log.info(f"listener watchdog: {PROXY_PORT} re-armed and accepting")
                    consecutive_fail = 0
                    rearm_failures = 0
                    continue
                rearm_failures += 1
                log.error(
                    f"listener watchdog: re-arm reported success but "
                    f"{PROXY_PORT} still refuses "
                    f"({rearm_failures}/{LISTENER_REARM_MAX})"
                )

            if rearm_failures >= LISTENER_REARM_MAX:
                log.critical(
                    f"listener watchdog: cannot restore {PROXY_PORT} after "
                    f"{rearm_failures} attempts — exiting so PM2 restarts the guardian"
                )
                # _exit, not sys.exit: we're inside a task, and a raised
                # SystemExit here would just be swallowed as a task exception.
                os._exit(1)
    except asyncio.CancelledError:
        log.info("listener watchdog cancelled")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  APP FACTORY + LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────


async def on_startup(app: web.Application) -> None:
    """Called once when the guardian boots. Kicks off background tasks."""
    # Shared aiohttp client session for upstream proxying + health checks.
    guardian._client = aiohttp.ClientSession()
    app["prewarm_task"] = asyncio.create_task(prewarm_watcher(app))
    app["slot_poller_task"] = asyncio.create_task(slot_activity_poller(app))
    app["enforcer_task"] = asyncio.create_task(enforce_single_llama(app))
    app["reaper_task"] = asyncio.create_task(idle_reaper(app))
    app["queue_worker_task"] = asyncio.create_task(queue_worker(app))
    log.info(
        f"guardian up on {PROXY_HOST}:{PROXY_PORT} → {LLAMA_HOST}:{LLAMA_PORT} "
        f"(idle timeout {IDLE_TIMEOUT_MIN} min, prewarm ports {PREWARM_PORTS})"
    )


async def on_cleanup(app: web.Application) -> None:
    """Clean shutdown — cancel background tasks, close client."""
    for t in ("prewarm_task", "slot_poller_task", "enforcer_task", "reaper_task", "queue_worker_task"):
        app[t].cancel()
    await asyncio.gather(
        app["prewarm_task"], app["slot_poller_task"], app["enforcer_task"], app["reaper_task"], app["queue_worker_task"],
        return_exceptions=True,
    )
    await guardian._client.close()
    guardian.job_store.close()
    log.info("guardian shut down")


def make_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024 * 100)  # 100MB max request
    # Guardian's own health endpoint. Everything else proxies to llama.
    app.router.add_get("/__guardian/health", guardian_health)
    app.router.add_post("/__guardian/jobs", queue_submit)
    app.router.add_get("/__guardian/jobs/{job_id}", queue_status)
    app.router.add_post("/__guardian/jobs/{job_id}/cancel", queue_cancel)
    app.router.add_post("/__guardian/sleep", guardian_sleep)
    # Catch-all proxy: any method, any path → llama.
    app.router.add_route("*", "/{tail:.*}", proxy_handler)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


async def serve() -> None:
    """Run the guardian under a watchdog that can rebuild the listener.

    This is the AppRunner/TCPSite expansion of what web.run_app() does, kept
    explicit for one reason: run_app owns its site privately, and recovering
    from a closed listening socket requires calling stop()/start() on that
    site. See LISTENER_POLL_S for the failure this exists to survive.
    """
    runner = web.AppRunner(
        make_app(),
        access_log=None,  # we log transitions, not every request
    )
    await runner.setup()  # fires on_startup
    site = web.TCPSite(runner, host=PROXY_HOST, port=PROXY_PORT)
    await site.start()

    suspect = asyncio.Event()
    install_accept_failure_handler(asyncio.get_running_loop(), suspect)
    watchdog = asyncio.create_task(listener_watchdog(site, suspect))

    try:
        await asyncio.Event().wait()  # serve until killed
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        await runner.cleanup()  # fires on_cleanup


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
