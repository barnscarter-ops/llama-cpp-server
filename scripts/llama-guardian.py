"""
llama-guardian — on-demand lifecycle manager for llama-server.

ARCHITECTURE
============
This proxy owns port 8080 (where MCC and maverickforge point their local-LLM
requests). It forwards everything to llama-server, which listens on the
internal port 8081 (loopback only, not exposed).

Three jobs:
  1. STREAMING PROXY — port 8080 → 8081, preserving SSE token streams.
  2. PRE-WARM       — when MCC (3000) or maverickforge (3012) comes online,
                      start llama so it's hot before the first real request.
  3. IDLE REAPER    — stop llama after IDLE_TIMEOUT_MIN of no activity.

WHY A PROXY (not a process watcher)
===================================
MCC and maverickforge connect directly to port 8080. If llama is stopped and
a request arrives, the connection is refused instantly — no chance to start
llama. The proxy intercepts at the port level so it can cold-start on demand.

MCC/maverickforge need ZERO changes — they keep hitting localhost:8080.

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
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone

import aiohttp
from aiohttp import ClientError, ClientTimeout, web

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG — tune here. All times in seconds unless noted.
# ─────────────────────────────────────────────────────────────────────────────

LLAMA_HOST = "127.0.0.1"          # llama-server binds loopback only (internal)
LLAMA_PORT = 8081                 # llama-server internal port
PROXY_HOST = "0.0.0.0"            # guardian listens on all interfaces (Tailscale)
PROXY_PORT = 8080                 # where MCC/maverickforge connect (unchanged)

# Services whose startup should trigger a llama pre-warm.
# When one of these ports transitions down→up, we start llama in the
# background so it's hot by the time a real request arrives.
PREWARM_PORTS = [3000, 3012]      # 3000 = MCC, 3012 = maverickforge
PREWARM_POLL_S = 5                # how often to check prewarm ports

IDLE_TIMEOUT_MIN = 30             # stop llama after this many idle minutes
# Allow override for testing (e.g. IDLE_TIMEOUT_MIN=1 to test the reaper fast)
if os.environ.get("IDLE_TIMEOUT_MIN"):
    IDLE_TIMEOUT_MIN = int(os.environ["IDLE_TIMEOUT_MIN"])
IDLE_POLL_S = 60                  # how often the reaper checks

HEALTH_PATH = "/v1/models"        # llama endpoint we poll to confirm it's up
HEALTH_TIMEOUT_S = 60             # max wait for llama to come up (model load)
HEALTH_POLL_S = 1                 # how often to poll while waiting

PM2_APP = "qwen3-llama"           # PM2 process name for llama-server

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
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

        # Cached "is llama up" state. Avoids hammering the health endpoint
        # on every single proxied request.
        self._llama_up = False

    @property
    def llama_target(self) -> str:
        """Base URL of the backing llama-server."""
        return f"http://{LLAMA_HOST}:{LLAMA_PORT}"

    async def is_llama_up(self) -> bool:
        """Quick health probe of the backing llama-server."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(LLAMA_HOST, LLAMA_PORT), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
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

        log.info(f"starting llama ({reason})...")
        await pm2("start")

        # Wait for llama to finish loading the model (the slow part).
        if await guardian.wait_until_llama_up():
            log.info(f"llama is up ({reason})")
            return True
        log.error(f"llama failed to come up within {HEALTH_TIMEOUT_S}s ({reason})")
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


async def proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward a single request to llama, streaming the response back.

    Three things make this non-trivial:
      1. SSE streaming — llama emits tokens as they're generated. We must
         stream chunks back to the client immediately, not buffer the whole
         response. (aiohttp's StreamResponse + async iteration handles this.)
      2. Cold start — if llama is down, we start it and wait. The client's
         HTTP connection stays open the whole time; from MCC's perspective
         the request just takes longer.
      3. Active-request accounting — we bump a counter so the idle reaper
         knows not to kill llama mid-generation.
    """
    # If llama is down, start it (deduped) and wait for health.
    if not await guardian.is_llama_up():
        started = await ensure_llama_started(reason="request")
        if not started:
            return web.json_response(
                {"error": "llama-server unavailable"},
                status=503,
            )

    # Build the upstream request.
    upstream_url = f"{guardian.llama_target}{request.path_qs}"
    # Strip hop-by-hop headers, keep everything else (auth, content-type, etc.)
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    body = await request.read()

    # Only count ACTUAL generation requests toward idle timeout, not health
    # checks. MCC's dashboard polls /v1/models and /metrics every few seconds
    # — those are "is llama alive?" probes, not real work. If we counted them,
    # llama would never idle out while the dashboard is open.
    #
    # Real work = POST to /v1/chat/completions or /v1/completions (the only
    # endpoints that actually generate tokens). Everything else is metadata.
    is_real_work = (
        request.method == "POST"
        and ("/chat/completions" in request.path or "/completions" in request.path)
    )

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
        return web.json_response({"error": f"upstream error: {e}"}, status=502)
    finally:
        guardian.active_requests -= 1
        if is_real_work:
            guardian.last_request_time = time.time()


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
    """Watch MCC/maverickforge ports. On down→up transition, pre-warm llama.

    Why this matters: at boot, PM2 resurrects guardian + MCC + maverickforge
    at roughly the same time. By the time MCC/maverickforge are listening,
    the guardian sees it and starts llama. So llama is hot ~20s after boot,
    BEFORE any real request arrives — no cold-start latency for users.

    Same logic applies if MCC/maverickforge are manually restarted later.
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

    Runs on startup to kill strays, then polls periodically. If someone
    launches llama manually (not via PM2), or if PM2 somehow spawns a
    duplicate, the extra process gets killed.

    We keep the process that's listening on the expected port (8081).
    If multiple are on 8081 (shouldn't happen), keep the oldest.
    """
    import subprocess

    # Run once at startup immediately, then poll every 30s.
    FIRST_RUN = True
    POLL_S = 30
    LLAMA_EXE = "llama-server.exe"

    log.info("single-llama enforcer started")

    async def _enforce():
        nonlocal FIRST_RUN
        try:
            # Get all llama-server PIDs via tasklist (fast, no WMI overhead).
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {LLAMA_EXE}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            pids = []
            for line in result.stdout.strip().split("\n"):
                parts = line.replace('"', '').split(",")
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    pids.append(int(parts[1].strip()))

            if len(pids) <= 1 and not FIRST_RUN:
                return  # all good, nothing to do

            if FIRST_RUN and len(pids) == 0:
                log.info("enforcer: no llama processes found on startup")
                FIRST_RUN = False
                return

            if len(pids) > 1:
                # Find which one holds port 8081 — that's the PM2-managed one.
                keep_pid = None
                for pid in pids:
                    port_result = subprocess.run(
                        ["netstat", "-ano"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    for line in port_result.stdout.split("\n"):
                        if f":8081" in line and "LISTENING" in line and str(pid) in line:
                            keep_pid = pid
                            break
                    if keep_pid:
                        break
                # If none hold 8081 yet (both loading?), keep oldest.
                if keep_pid is None:
                    keep_pid = min(pids)

                for pid in pids:
                    if pid != keep_pid:
                        log.warning(f"enforcer: killing duplicate llama PID {pid} (keeping {keep_pid})")
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
            elif FIRST_RUN and len(pids) == 1:
                log.info(f"enforcer: 1 llama process found (PID {pids[0]})")

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
            "idle_seconds": int(time.time() - guardian.last_request_time),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


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
    log.info(
        f"guardian up on {PROXY_HOST}:{PROXY_PORT} → {LLAMA_HOST}:{LLAMA_PORT} "
        f"(idle timeout {IDLE_TIMEOUT_MIN} min, prewarm ports {PREWARM_PORTS})"
    )


async def on_cleanup(app: web.Application) -> None:
    """Clean shutdown — cancel background tasks, close client."""
    for t in ("prewarm_task", "slot_poller_task", "enforcer_task", "reaper_task"):
        app[t].cancel()
    await asyncio.gather(
        app["prewarm_task"], app["slot_poller_task"], app["enforcer_task"], app["reaper_task"],
        return_exceptions=True,
    )
    await guardian._client.close()
    log.info("guardian shut down")


def make_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024 * 100)  # 100MB max request
    # Guardian's own health endpoint. Everything else proxies to llama.
    app.router.add_get("/__guardian/health", guardian_health)
    # Catch-all proxy: any method, any path → llama.
    app.router.add_route("*", "/{tail:.*}", proxy_handler)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    # aiohttp's run_app manages the event loop for us.
    web.run_app(
        make_app(),
        host=PROXY_HOST,
        port=PROXY_PORT,
        access_log=None,  # we log transitions, not every request
        print=None,       # silence the default "======== Running on..." banner
    )
