"""Cloud model backend: Guardian holds the provider key (modeled on Chief's chief-deepseek-relay.ts).

Direct provider only. The key is attached server-side and never returned or logged.
Output tokens are clamped, calls are rate-limited per window, every call reserves its
worst-case cost against a per-model UTC-day cap (then settles to actual usage), and
concurrency honors the registry's max_concurrent. Behind GUARDIAN_CLOUD (default off).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jev_client import KeyLoader
from jev_secret import GsmAccessor, JevCredentialUnavailable, gsm_key_loader
from jev_spend import utc_day
from model_registry import CloudState, ModelRegistry, ModelSpec, cloud_flag_enabled

DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_DAILY_CAP_USD = 5.0
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_REQUEST_BYTES = 256 * 1024
DEFAULT_CALLS_PER_WINDOW = 200
DEFAULT_WINDOW_S = 3600.0
DEFAULT_TIMEOUT_S = 120.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# (url, headers, body, timeout_s) -> (status, response bytes). Raises on connection trouble.
CloudTransport = Callable[[str, dict, bytes, float], Awaitable[tuple[int, bytes]]]


class CloudRefused(Exception):
    """A call Guardian refused or the provider failed. `code` is stable; the message never carries provider text."""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class CloudResult:
    body: dict
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_microusd: int
    reserved_microusd: int


async def aiohttp_cloud_transport(url: str, headers: dict, body: bytes, timeout_s: float) -> tuple[int, bytes]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, data=body, allow_redirects=False) as response:
            data = await response.content.read(MAX_RESPONSE_BYTES + 1)
            return response.status, data


def _float_env(name: str, default: float, *, positive: bool = True) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    if math.isnan(value) or math.isinf(value) or (positive and value <= 0):
        return default
    return value


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


class CloudSpendLedger:
    """Per-model UTC-day spend in micro-USD. Reserve worst case before a call, settle to actual after."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cloud_daily_spend ("
            "model_id TEXT NOT NULL, utc_day TEXT NOT NULL, spent_microusd INTEGER NOT NULL, "
            "calls INTEGER NOT NULL, PRIMARY KEY (model_id, utc_day))"
        )

    @classmethod
    def open(cls, db_path: str) -> "CloudSpendLedger":
        # Own autocommit connection so BEGIN IMMEDIATE cannot collide with another store's transaction.
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return cls(conn)

    def spent(self, model_id: str, now_s: float) -> int:
        row = self._conn.execute(
            "SELECT spent_microusd FROM cloud_daily_spend WHERE model_id = ? AND utc_day = ?", (model_id, utc_day(now_s))
        ).fetchone()
        return row[0] if row else 0

    def reserve(self, model_id: str, amount: int, cap: int, now_s: float) -> bool:
        day = utc_day(now_s)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT spent_microusd FROM cloud_daily_spend WHERE model_id = ? AND utc_day = ?", (model_id, day)
            ).fetchone()
            if (row[0] if row else 0) + amount > cap:
                self._conn.execute("ROLLBACK")
                return False
            self._conn.execute(
                "INSERT INTO cloud_daily_spend (model_id, utc_day, spent_microusd, calls) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(model_id, utc_day) DO UPDATE SET spent_microusd = spent_microusd + excluded.spent_microusd, "
                "calls = calls + 1",
                (model_id, day, amount),
            )
            self._conn.execute("COMMIT")
            return True
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return False

    def adjust(self, model_id: str, delta: int, now_s: float) -> None:
        """Settle a reservation (negative delta gives back unused headroom). Best effort: over-counting is the safe side."""
        try:
            self._conn.execute(
                "UPDATE cloud_daily_spend SET spent_microusd = MAX(0, spent_microusd + ?) WHERE model_id = ? AND utc_day = ?",
                (delta, model_id, utc_day(now_s)),
            )
        except sqlite3.Error:
            pass


def key_present(spec: ModelSpec) -> bool:
    if not spec.key_env:
        return False
    if os.environ.get(spec.key_env, "").strip():
        return True
    return bool(os.environ.get(f"{spec.key_env}_SECRET_REF") and os.environ.get(f"{spec.key_env}_SECRET_PRINCIPAL"))


def key_loader_for(spec: ModelSpec, accessor: GsmAccessor | None = None) -> KeyLoader:
    """Env var first (read per call so rotation needs no restart), else one Secret Manager version."""
    env_name = spec.key_env or ""

    async def load() -> str:
        value = os.environ.get(env_name, "").strip() if env_name else ""
        if value:
            return value
        loader = gsm_key_loader(
            os.environ.get(f"{env_name}_SECRET_REF"), os.environ.get(f"{env_name}_SECRET_PRINCIPAL"), accessor
        )
        return await loader()

    return load


def worst_case_microusd(spec: ModelSpec, request_bytes: int, max_tokens: int) -> int:
    # Why: a token is at least one byte, so bytes bound the input tokens.
    usd = request_bytes / 1000 * spec.cost_per_1k_in + max_tokens / 1000 * spec.cost_per_1k_out
    return max(1, math.ceil(usd * 1_000_000))


def actual_microusd(spec: ModelSpec, prompt_tokens: int, completion_tokens: int) -> int:
    usd = prompt_tokens / 1000 * spec.cost_per_1k_in + completion_tokens / 1000 * spec.cost_per_1k_out
    return max(0, math.ceil(usd * 1_000_000))


class CloudBackend:
    def __init__(
        self,
        registry: ModelRegistry,
        ledger: CloudSpendLedger,
        *,
        transport: CloudTransport = aiohttp_cloud_transport,
        accessor: GsmAccessor | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry, self._ledger, self._transport, self._accessor, self._clock = registry, ledger, transport, accessor, clock
        self._limits = {s.id: self._limit_for(s) for s in registry if s.locality == "cloud"}
        self._sems = {mid: asyncio.Semaphore(n) for mid, n in self._limits.items()}
        self._in_flight = {mid: 0 for mid in self._limits}
        self._calls: dict[str, deque] = {mid: deque() for mid in self._limits}

    @staticmethod
    def _limit_for(spec: ModelSpec) -> int:
        return _int_env("GUARDIAN_CLOUD_MAX_CONCURRENT", spec.max_concurrent)

    # Read at call time so an operator can change a cap with a restart, not a redeploy.
    @staticmethod
    def cap_microusd() -> int:
        return round(_float_env("GUARDIAN_CLOUD_DAILY_CAP_USD", DEFAULT_DAILY_CAP_USD) * 1_000_000)

    def max_concurrent(self, model_id: str) -> int:
        return self._limits.get(model_id, 0)

    def in_flight(self, model_id: str) -> int:
        return self._in_flight.get(model_id, 0)

    def state(self, spec: ModelSpec) -> CloudState:
        """What the registry's readiness needs; never calls the provider."""
        now = self._clock()
        # Why: a call needs headroom for a full-length reply; offering a model that will refuse is worse than hiding it.
        floor = worst_case_microusd(spec, 0, _int_env("GUARDIAN_CLOUD_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))
        return CloudState(
            enabled=cloud_flag_enabled(),
            key_present=key_present(spec),
            cap_left=self._ledger.spent(spec.id, now) + floor <= self.cap_microusd(),
            # Why: a busy cloud model queues like a busy local one; only the cap and key make it unavailable.
            slot_free=True,
        )

    def _rate_ok(self, model_id: str, now: float) -> bool:
        window = _float_env("GUARDIAN_CLOUD_WINDOW_S", DEFAULT_WINDOW_S)
        limit = _int_env("GUARDIAN_CLOUD_MAX_CALLS_PER_WINDOW", DEFAULT_CALLS_PER_WINDOW)
        calls = self._calls[model_id]
        while calls and calls[0] <= now - window:
            calls.popleft()
        if len(calls) >= limit:
            return False
        calls.append(now)
        return True

    async def complete(self, spec: ModelSpec, request: dict, *, wait: bool) -> CloudResult:
        """One non-streaming completion. wait=False refuses with cloud_busy instead of queueing for a slot."""
        if spec.id not in self._limits:
            raise CloudRefused("not_a_cloud_model", f"{spec.id} is not a cloud model.", 400)
        if not cloud_flag_enabled():
            raise CloudRefused("cloud_disabled", "Cloud dispatch is disabled (GUARDIAN_CLOUD).", 409)
        if not isinstance(request.get("messages"), list) or not request["messages"]:
            raise CloudRefused("invalid_request", "messages must be a non-empty array.", 400)
        body = dict(request)
        body.update(model=spec.id, stream=False)
        body.pop("max_completion_tokens", None)
        limit = _int_env("GUARDIAN_CLOUD_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)
        asked = body.get("max_tokens")
        asked = asked if isinstance(asked, int) and not isinstance(asked, bool) and asked > 0 else limit
        body["max_tokens"] = min(asked, limit)
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if len(payload) > _int_env("GUARDIAN_CLOUD_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES):
            raise CloudRefused("request_too_large", "Request exceeds the cloud request size limit.", 413)
        if not key_present(spec):
            raise CloudRefused("cloud_key_missing", f"No credential configured for {spec.id}.", 503)

        sem = self._sems[spec.id]
        if not wait and sem.locked():
            raise CloudRefused("cloud_busy", f"{spec.id} is at its concurrency limit.", 429)
        async with sem:
            self._in_flight[spec.id] += 1
            try:
                return await self._call(spec, payload, body["max_tokens"])
            finally:
                self._in_flight[spec.id] -= 1

    async def _call(self, spec: ModelSpec, payload: bytes, max_tokens: int) -> CloudResult:
        now = self._clock()
        if not self._rate_ok(spec.id, now):
            raise CloudRefused("rate_limited", f"{spec.id} call limit for this window reached.", 429)
        reserved = worst_case_microusd(spec, len(payload), max_tokens)
        if not self._ledger.reserve(spec.id, reserved, self.cap_microusd(), now):
            raise CloudRefused("spend_cap", f"{spec.id} daily spend cap reached.", 429)
        refund = True  # give the reservation back unless the provider may have billed us
        try:
            try:
                key = await key_loader_for(spec, self._accessor)()
            except JevCredentialUnavailable:
                raise CloudRefused("cloud_key_unavailable", "Credential could not be loaded.", 503) from None
            if not key or len(key) > 8192 or _CONTROL.search(key):
                raise CloudRefused("cloud_key_unavailable", "Credential could not be loaded.", 503)
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}
            del key
            try:
                timeout_s = _float_env("GUARDIAN_CLOUD_TIMEOUT_S", DEFAULT_TIMEOUT_S)
                # Backstop in case a transport ignores its own timeout.
                status, raw = await asyncio.wait_for(
                    self._transport(DEEPSEEK_ENDPOINT, headers, payload, timeout_s), timeout_s + 5
                )
            except asyncio.TimeoutError:
                refund = False  # the provider may have processed it
                raise CloudRefused("cloud_timeout", "Provider call timed out.", 504) from None
            except CloudRefused:
                raise
            except Exception:  # noqa: BLE001 - transport errors can carry headers
                raise CloudRefused("cloud_unreachable", "Provider unreachable.", 502) from None
            finally:
                headers.pop("Authorization", None)
            if status != 200:
                raise CloudRefused("cloud_upstream_error", f"Provider returned HTTP {status}.", 502)
            refund = False  # a 200 was billed whatever we make of the body
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CloudRefused("cloud_response_invalid", "Provider response too large.", 502)
            try:
                data = json.loads(raw)
            except ValueError:
                raise CloudRefused("cloud_response_invalid", "Provider response was not JSON.", 502) from None
            if not isinstance(data, dict):
                raise CloudRefused("cloud_response_invalid", "Provider response was not an object.", 502)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in (tin, tout)):
                cost = actual_microusd(spec, tin, tout)
                self._ledger.adjust(spec.id, cost - reserved, now)
            else:
                tin = tout = None
                cost = reserved  # unknown usage: keep the worst case
            return CloudResult(data, tin, tout, cost, reserved)
        finally:
            if refund:
                self._ledger.adjust(spec.id, -reserved, now)
