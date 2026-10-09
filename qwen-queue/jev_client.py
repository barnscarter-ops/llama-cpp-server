"""Jev transport for Guardian (port of Chief's chief-jev-client.ts, same contract).

One POST per call: no retries, no redirects. Errors carry a code only, never provider
bodies or the key. Tests inject the transport; nothing here is imported on the hot path
unless GUARDIAN_SELECTOR=jev.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

JEV_MODEL = "jev-1.13.0"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_TIMEOUT_S = 10.0
JEV_MAX_TIMEOUT_S = 30.0
MAX_BODY_BYTES = 12_000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class JevChoiceUnavailable(Exception):
    """code: request_invalid | credential_unavailable | timeout | http_error | response_invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Jev choice unavailable: {code}")
        self.code = code


# (url, headers, body) -> (status, decoded JSON). Raises on connection trouble.
JevTransport = Callable[[str, dict, bytes], Awaitable[tuple[int, Any]]]
KeyLoader = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class JevChoiceRequest:
    task_id: str
    summary: str
    instructions: str
    criteria: Mapping[str, str]  # option id -> criteria text; must include "abstain"


@dataclass(frozen=True)
class JevChoiceAnswer:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float
    input_tokens: int
    output_tokens: int


async def aiohttp_transport(url: str, headers: dict, body: bytes) -> tuple[int, Any]:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=body, allow_redirects=False) as response:
            if response.status != 200:
                return response.status, None
            return response.status, await response.json(content_type=None)


def _prob(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _exact(obj: Any, keys: set[str]) -> bool:
    return isinstance(obj, dict) and set(obj) == keys


def parse_response(raw: Any, offered: list[str]) -> JevChoiceAnswer:
    """Strict: any shape or consistency problem is response_invalid, never a partial answer."""
    bad = JevChoiceUnavailable("response_invalid")
    if not _exact(raw, {"model", "answers", "usage"}) or raw["model"] != JEV_MODEL:
        raise bad
    if not _exact(raw["answers"], {"route"}) or not _exact(raw["answers"]["route"], {"type", "choice", "probabilities", "confidence"}):
        raise bad
    route, usage = raw["answers"]["route"], raw["usage"]
    if route["type"] != "choice" or not isinstance(route["choice"], str) or not _prob(route["confidence"]):
        raise bad
    probs = route["probabilities"]
    if not isinstance(probs, dict) or not all(_prob(v) for v in probs.values()):
        raise bad
    if sorted(probs) != sorted(offered) or route["choice"] not in offered:
        raise bad
    if abs(sum(probs.values()) - 1) > 0.001 or probs[route["choice"]] != max(probs.values()):
        raise bad
    if not _exact(usage, {"input_tokens", "output_tokens"}) or not _count(usage["input_tokens"]) or not _count(usage["output_tokens"]):
        raise bad
    return JevChoiceAnswer(route["choice"], dict(probs), float(route["confidence"]), usage["input_tokens"], usage["output_tokens"])


async def request_jev_choice(
    load_key: KeyLoader,
    request: JevChoiceRequest,
    *,
    transport: JevTransport = aiohttp_transport,
    timeout_s: float = JEV_DEFAULT_TIMEOUT_S,
) -> JevChoiceAnswer:
    offered = sorted(request.criteria)
    if not (0 < timeout_s <= JEV_MAX_TIMEOUT_S) or "abstain" not in offered or len(offered) < 2:
        raise JevChoiceUnavailable("request_invalid")
    body = json.dumps({
        "model": JEV_MODEL,
        "state": {"taskId": request.task_id, "summary": request.summary},
        "questions": {"route": {"type": "choice", "instructions": request.instructions, "criteria": dict(request.criteria)}},
    }, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise JevChoiceUnavailable("request_invalid")
    try:
        key = await load_key()
    except Exception:  # noqa: BLE001 - loader errors may echo the secret reference
        raise JevChoiceUnavailable("credential_unavailable") from None
    if not key or len(key) > 8192 or _CONTROL.search(key):
        raise JevChoiceUnavailable("credential_unavailable")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}
    del key
    try:
        status, raw = await asyncio.wait_for(transport(JEV_URL, headers, body), timeout_s)
    except asyncio.TimeoutError:
        raise JevChoiceUnavailable("timeout") from None
    except Exception:  # noqa: BLE001 - transport errors can carry headers or bodies
        raise JevChoiceUnavailable("http_error") from None
    finally:
        headers.pop("Authorization", None)
    if status != 200:
        raise JevChoiceUnavailable("http_error")
    return parse_response(raw, offered)
