"""JevSelector: Jev decides the model, Guardian only checks the answer is one it offered.

Every failure is a `selection_failed` reason here; the rules fallback is the FallbackSelector wrapper (step 10).
Only the compact task summary and candidate profiles leave the machine, never the messages, and the summary
leaves only when Chief marked it cleared for Jev, after a redaction pass.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Awaitable, Callable, Sequence

from candidates import Candidate
from jev_client import (
    JEV_DEFAULT_TIMEOUT_S, JevChoiceAnswer, JevChoiceRequest, JevChoiceUnavailable, JevTransport,
    KeyLoader, aiohttp_transport, request_jev_choice,
)
from jev_secret import JevCredentialUnavailable, build_key_loader
from jev_spend import JevSpendLedger
from summary_redaction import redact_summary
from task_dispatch import SelectorAnswer, TaskSpec

log = logging.getLogger("guardian.jev")

MAX_SUMMARY_CHARS = 2_000
NOT_CLEARED = "jev_summary_not_cleared"
MAX_CANDIDATES = 8  # Jev's contract; more would have to be silently dropped, so refuse instead.
INSTRUCTIONS = (
    "Choose the one listed model best suited to this task, or abstain. Prefer the cheaper model "
    "when both fit. The list is already filtered for data clearance and availability; you do not grant execution."
)
ABSTAIN = "Abstain when no listed model clearly fits the task or the summary is insufficient."

_FAILURE_REASON = {
    "request_invalid": "jev_request_invalid",
    "credential_unavailable": "jev_credential_unavailable",
    "timeout": "jev_timeout",
    "http_error": "jev_error",
    "response_invalid": "jev_invalid_response",
}


def selector_name() -> str:
    return os.environ.get("GUARDIAN_SELECTOR", "rules").strip().lower() or "rules"


def profile_for(c: Candidate) -> str:
    return json.dumps({
        "readiness": c.readiness.state, "wake_seconds": c.wake_seconds, "cost_estimate_usd": round(c.cost_estimate_usd, 6),
        "quality_tier": c.quality_tier, "needs_approval": c.needs_approval, "egress": c.egress,
        "context_tokens": c.context_tokens,
    }, separators=(",", ":"), sort_keys=True)


class JevSelector:
    name = "jev"

    def __init__(
        self,
        ledger: JevSpendLedger,
        load_key: KeyLoader,
        *,
        transport: JevTransport = aiohttp_transport,
        timeout_s: float = JEV_DEFAULT_TIMEOUT_S,
        confidence_threshold: float = 0.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ledger, self._load_key, self._transport = ledger, load_key, transport
        self._timeout_s, self._threshold, self._clock = timeout_s, confidence_threshold, clock

    async def choose(self, task: TaskSpec, candidates: Sequence[Candidate]) -> SelectorAnswer:
        ids = [c.model_id for c in candidates]
        if not ids or len(ids) > MAX_CANDIDATES or "abstain" in ids or len(set(ids)) != len(ids):
            return SelectorAnswer(None, "jev_request_invalid")
        if not task.summary_cleared_for_jev:
            # Checked before anything else: no key load, no spend, no network.
            return SelectorAnswer(None, NOT_CLEARED)
        summary, redactions = redact_summary((task.summary or "").strip()[:MAX_SUMMARY_CHARS])
        summary = summary.strip()
        if not summary.replace("[redacted]", "").strip():
            return SelectorAnswer(None, "jev_request_invalid")
        try:
            reserved = self._ledger.reserve(self._clock())
        except sqlite3.Error:
            log.exception("jev spend ledger unavailable")
            return SelectorAnswer(None, "jev_ledger_unavailable")
        if not reserved:
            return SelectorAnswer(None, "jev_cap_reached")
        criteria = {"abstain": ABSTAIN, **{c.model_id: profile_for(c) for c in candidates}}
        try:
            answer: JevChoiceAnswer = await request_jev_choice(
                self._load_key, JevChoiceRequest(task.task_id, summary, INSTRUCTIONS, criteria),
                transport=self._transport, timeout_s=self._timeout_s,
            )
        except JevChoiceUnavailable as exc:
            log.warning("jev pick failed: %s", exc.code)
            return SelectorAnswer(None, _FAILURE_REASON.get(exc.code, "jev_error"))
        raw = {"choice": answer.choice, "confidence": answer.confidence,
               "input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens,
               "summary_redactions": redactions}
        if answer.choice == "abstain":
            return SelectorAnswer(None, "jev_abstain", raw)
        if answer.choice not in ids:  # parse_response already enforces this; kept as the dispatch-side guarantee
            return SelectorAnswer(None, "jev_invalid_choice", raw)
        if answer.confidence < self._threshold:
            return SelectorAnswer(None, "jev_low_confidence", raw)
        return SelectorAnswer(answer.choice, "jev_pick", raw)


def confidence_threshold_from_env() -> float:
    try:
        value = float(os.environ.get("GUARDIAN_JEV_MIN_CONFIDENCE", "0"))
    except ValueError:
        return 0.0
    return value if 0 <= value <= 1 else 0.0


def timeout_from_env() -> float:
    try:
        value = float(os.environ.get("GUARDIAN_JEV_TIMEOUT_S", JEV_DEFAULT_TIMEOUT_S))
    except ValueError:
        return JEV_DEFAULT_TIMEOUT_S
    return value if 0 < value <= 30 else JEV_DEFAULT_TIMEOUT_S


def build_jev_selector(db_path: str, *, transport: JevTransport = aiohttp_transport, accessor=None) -> JevSelector:
    """Raises if no key source is configured, so a misconfigured flag fails loudly at the first task."""
    return JevSelector(
        JevSpendLedger.open(db_path), build_key_loader(accessor), transport=transport,
        timeout_s=timeout_from_env(), confidence_threshold=confidence_threshold_from_env(),
    )


class UnavailableSelector:
    """Stands in when the Jev flag is on but no key source is configured: every task fails with a reason."""

    name = "jev"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def choose(self, task: TaskSpec, candidates: Sequence[Candidate]) -> SelectorAnswer:
        return SelectorAnswer(None, self._reason)


def selector_for_env(db_path: str, *, transport: JevTransport = aiohttp_transport, accessor=None):
    """GUARDIAN_SELECTOR=jev picks Jev; anything else (including typos) stays on the rules selector."""
    from task_dispatch import RulesSelector

    if selector_name() != "jev":
        return RulesSelector()
    try:
        return build_jev_selector(db_path, transport=transport, accessor=accessor)
    except JevCredentialUnavailable as exc:
        log.error("jev selector unavailable: %s", exc.code)
        return UnavailableSelector(f"jev_{exc.code}")
    except (sqlite3.Error, OSError):
        # The shared queue database can be locked or unwritable; that is a failed selection, not a 500.
        log.exception("jev spend ledger could not be opened")
        return UnavailableSelector("jev_ledger_unavailable")
