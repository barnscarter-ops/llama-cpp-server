"""Fleet alias router: seat_for_model, AIWA occupant probe, forward_aiwa.

Inference routing is ONLY seat_for_model(model). No prompt classifiers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from aiohttp import ClientError, ClientTimeout, web

log = logging.getLogger("guardian")

CLERK_ALIASES = frozenset({"nemotron-3.5-lightning-30b-a3b", "clerk"})
CONSULT_ALIASES = frozenset({"qwen3.8-27b", "consult"})
LEGACY_MODEL_ALIASES = frozenset({"qwen3.6-35b", "qwen3-llama"})
GLM_ALIASES = frozenset({"local-llm", "qwen3-14b"}) | LEGACY_MODEL_ALIASES

SERVING_IDS = {
    "clerk": "nemotron-3.5-lightning-30b-a3b",
    "consult": "qwen3.8-27b",
    "glm": "local-llm",
}

OCCUPANT_TTL_S = 2.0
OCCUPANT_TIMEOUT_S = 2.0
LOOPBACK_REMOTES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

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

# OpenAI/metadata paths that stay GLM-only when the fleet router is on.
GLM_METADATA_GET_PATHS = frozenset({"/v1/models", "/metrics", "/slots", "/health"})


def fleet_router_enabled() -> bool:
    """Read FLEET_ROUTER on each call so tests can toggle without reimport."""
    return os.environ.get("FLEET_ROUTER", "false").strip().lower() in {"true", "1", "yes"}


def aiwa_base() -> str:
    return os.environ.get("AIWA_BASE", "http://192.168.1.240:8080").rstrip("/")


def aiwa_proxy_loopback_only() -> bool:
    return os.environ.get("AIWA_PROXY_LOOPBACK_ONLY", "true").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def is_loopback_remote(remote: str | None) -> bool:
    return (remote or "") in LOOPBACK_REMOTES


def seat_for_model(model: str | None, *, default_seat: str = "glm") -> str:
    """Map request model alias → seat. Empty/omitted model maps to GLM (not clerk)."""
    if not model or not str(model).strip():
        return default_seat
    m = str(model).strip().lower()
    if m in {a.lower() for a in GLM_ALIASES}:
        return "glm"
    if m in CLERK_ALIASES:
        return "clerk"
    if m in CONSULT_ALIASES:
        return "consult"
    return "cloud"


def fleet_default_seat() -> str:
    """Empty-model seat. Clerk only when FLEET_ROUTER is on."""
    if not fleet_router_enabled():
        return "glm"
    raw = os.environ.get("FLEET_DEFAULT_SEAT", "clerk").strip().lower()
    if raw in {"clerk", "glm"}:
        return raw
    return "clerk"


def parse_primary_model_id(payload: Any) -> str | None:
    """Mirror Board ModelsJson.ParsePrimaryModelId."""
    if not isinstance(payload, dict):
        return None
    for array_name, field in (
        ("data", "id"),
        ("models", "id"),
        ("models", "name"),
        ("models", "model"),
    ):
        arr = payload.get(array_name)
        if not isinstance(arr, list):
            continue
        for item in arr:
            if not isinstance(item, dict):
                continue
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    root_id = payload.get("id")
    if isinstance(root_id, str) and root_id.strip():
        return root_id.strip()
    return None


def role_for_aiwa_model_id(model_id: str | None) -> str:
    """Map AIWA /v1/models primary id → clerk|consult|unknown."""
    if not model_id or not str(model_id).strip():
        return "unknown"
    m = str(model_id).strip().lower()
    if m == SERVING_IDS["clerk"] or "nemotron" in m:
        return "clerk"
    if m == SERVING_IDS["consult"] or "qwen" in m:
        return "consult"
    return "unknown"


def extract_model_from_body(body: bytes) -> str | None:
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if model is None:
        return None
    return str(model) if model != "" else ""


def rewrite_model_in_body(body: bytes, serving_id: str) -> bytes:
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    payload["model"] = serving_id
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def guardian_error(
    message: str,
    code: str,
    status: int,
    *,
    extra: dict | None = None,
) -> web.Response:
    payload: dict[str, Any] = {
        "error": {"message": message, "type": code, "code": code},
        "message": message,
    }
    if extra:
        payload.update(extra)
    return web.json_response(payload, status=status)


def llama_offline_response() -> web.Response:
    msg = "The local model is asleep (idle). Send a chat to wake it."
    return guardian_error(msg, "llama_offline", 503)


def seats_snapshot(
    *,
    occupant: str,
    model_id: str | None,
    reachable: bool,
    llama_up: bool,
    llama_target: str,
) -> dict[str, Any]:
    """Build the /__guardian/seats document (top-level keys aiwa/workbench).

    Pure — no I/O. Callers feed OccupantCache fields plus the in-process
    llama_up flag. An AIWA seat only counts when the occupant is a known
    fleet role; anything else collapses to unknown/unreachable so an
    unrecognized model id is never advertised as a loaded seat.
    """
    aiwa: dict[str, Any] = {
        "occupant": "unknown",
        "model_id": None,
        "reachable": False,
        "endpoint": aiwa_base(),
    }
    if reachable and occupant in {"clerk", "consult"}:
        aiwa.update(occupant=occupant, model_id=model_id, reachable=True)
    return {
        "aiwa": aiwa,
        "workbench": {
            "occupant": "glm",
            "model_id": SERVING_IDS["glm"] if llama_up else None,
            "llama_up": llama_up,
            "error_code": None if llama_up else "llama_offline",
            "endpoint": llama_target,
        },
    }


def aiwa_wrong_occupant_response(occupant: str, model_id: str | None, wanted: str) -> web.Response:
    shown = model_id or SERVING_IDS.get(occupant, occupant)
    msg = (
        f"AIWA occupant is {occupant} ({shown}); "
        f"{wanted} requires an operator swap."
    )
    return guardian_error(
        msg,
        "aiwa_wrong_occupant",
        409,
        extra={"occupant": occupant, "model_id": model_id},
    )


def is_glm_metadata_get(request: web.Request) -> bool:
    return request.method == "GET" and request.path in GLM_METADATA_GET_PATHS


class OccupantCache:
    """HTTP GET AIWA /v1/models with 2s timeout and 2s TTL. No SSH."""

    def __init__(self) -> None:
        self.occupant: str = "unknown"
        self.model_id: str | None = None
        self.reachable: bool = False
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._fetched_at = 0.0
        self.occupant = "unknown"
        self.model_id = None
        self.reachable = False

    async def get(self, client) -> tuple[str, str | None, bool]:
        now = time.time()
        if self._fetched_at and (now - self._fetched_at) < OCCUPANT_TTL_S:
            return self.occupant, self.model_id, self.reachable
        async with self._lock:
            now = time.time()
            if self._fetched_at and (now - self._fetched_at) < OCCUPANT_TTL_S:
                return self.occupant, self.model_id, self.reachable
            await self._refresh(client)
            return self.occupant, self.model_id, self.reachable

    async def _refresh(self, client) -> None:
        url = f"{aiwa_base()}/v1/models"
        try:
            async with client.get(url, timeout=ClientTimeout(total=OCCUPANT_TIMEOUT_S)) as resp:
                if resp.status < 200 or resp.status >= 300:
                    self.occupant = "unknown"
                    self.model_id = None
                    self.reachable = False
                    self._fetched_at = time.time()
                    return
                try:
                    payload = await resp.json(content_type=None)
                except (json.JSONDecodeError, ClientError, UnicodeDecodeError):
                    self.occupant = "unknown"
                    self.model_id = None
                    self.reachable = False
                    self._fetched_at = time.time()
                    return
                model_id = parse_primary_model_id(payload)
                role = role_for_aiwa_model_id(model_id)
                self.model_id = model_id
                self.occupant = role
                # Reachable but unparsed/unknown → still not proxyable.
                self.reachable = True
                self._fetched_at = time.time()
        except (ClientError, asyncio.TimeoutError, OSError):
            self.occupant = "unknown"
            self.model_id = None
            self.reachable = False
            self._fetched_at = time.time()


occupant_cache = OccupantCache()


async def forward_aiwa(
    request: web.Request,
    *,
    body: bytes,
    fwd_headers: dict,
    client,
    guardian,
    seat: str,
) -> web.StreamResponse:
    """Stream proxy to AIWA without GLM idle/lock/length-retry side effects."""
    upstream_url = f"{aiwa_base()}{request.path_qs}"
    timeout = ClientTimeout(total=None, sock_connect=10, sock_read=600)
    now = time.time()
    guardian.last_aiwa_activity = now
    if seat == "consult":
        guardian.last_aiwa_consult_activity = now
    try:
        async with client.request(
            request.method, upstream_url, headers=fwd_headers, data=body, timeout=timeout
        ) as upstream_resp:
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
        log.warning(f"AIWA upstream error: {e}")
        return guardian_error(
            f"AIWA upstream error: {e}",
            "aiwa_unreachable",
            503,
        )


async def handle_aiwa_completion(
    request: web.Request,
    *,
    body: bytes,
    seat: str,
    client,
    guardian,
) -> web.StreamResponse:
    """Occupant check + optional rewrite + forward_aiwa for clerk/consult."""
    if aiwa_proxy_loopback_only() and not is_loopback_remote(request.remote):
        return guardian_error(
            "AIWA reverse-proxy is loopback-only until Phase 3.",
            "aiwa_proxy_loopback_only",
            403,
        )

    occupant, model_id, reachable = await occupant_cache.get(client)
    if not reachable or occupant in {None, "", "unknown"}:
        return guardian_error(
            "AIWA is unreachable or occupant is unknown.",
            "aiwa_unreachable",
            503,
        )
    if occupant != seat:
        return aiwa_wrong_occupant_response(occupant, model_id, seat)

    serving_id = SERVING_IDS[seat]
    body = rewrite_model_in_body(body, serving_id)
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    }
    fwd_headers = {k: v for k, v in fwd_headers.items() if k.lower() != "content-length"}
    return await forward_aiwa(
        request,
        body=body,
        fwd_headers=fwd_headers,
        client=client,
        guardian=guardian,
        seat=seat,
    )
