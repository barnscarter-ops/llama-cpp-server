"""Interactive (public website chat) priority: settings, caller identification, preemption bookkeeping.

Off unless GUARDIAN_INTERACTIVE_TOKEN holds a token of at least MIN_TOKEN_LEN characters. Settings are read
from the environment on each call so the .guardian.env loader and tests can set them after import.
No imports from the rest of the guardian: model_slots and fleet_router depend on this module, not the reverse.
"""

from __future__ import annotations

import hmac
import logging
import os
import time

log = logging.getLogger("guardian")

MIN_TOKEN_LEN = 32
PRIORITY_DEFAULT = 0
PRIORITY_INTERACTIVE = 10
DEFAULT_MAX_PREEMPTS_PER_JOB = 3

_TRUE = {"true", "1", "yes"}
_preemptions = 0
_last_preempt_at: float | None = None
_job_preempts: dict[str, int] = {}


def token() -> str | None:
    """The configured token, or None when unset or too short to trust."""
    value = os.environ.get("GUARDIAN_INTERACTIVE_TOKEN", "").strip()
    return value if len(value) >= MIN_TOKEN_LEN else None


def preempt_enabled() -> bool:
    return os.environ.get("GUARDIAN_INTERACTIVE_PREEMPT", "false").strip().lower() in _TRUE


def max_preempts_per_job() -> int:
    """0 means unlimited. A malformed value falls back to the default rather than disabling the cap."""
    raw = os.environ.get("GUARDIAN_PREEMPT_MAX_PER_JOB", "").strip()
    if not raw:
        return DEFAULT_MAX_PREEMPTS_PER_JOB
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_MAX_PREEMPTS_PER_JOB


def is_interactive(request) -> bool:
    """True when the request carries the interactive bearer token."""
    expected = token()
    if expected is None:
        return False
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    supplied = header[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def job_preemptible(job_id: str) -> bool:
    cap = max_preempts_per_job()
    return cap == 0 or _job_preempts.get(job_id, 0) < cap


def record_preemption(seat: str, kind: str, ident: str) -> None:
    global _preemptions, _last_preempt_at
    _preemptions += 1
    _last_preempt_at = time.time()
    if kind == "job":
        _job_preempts[ident] = _job_preempts.get(ident, 0) + 1
    log.info("interactive_preempt seat=%s victim=%s id=%s", seat, kind, ident)


def health() -> dict:
    return {
        "enabled": token() is not None,
        "preempt_enabled": preempt_enabled(),
        "preemptions": _preemptions,
        "last_preempt_at": _last_preempt_at,
    }


def reset() -> None:
    """Tests only."""
    global _preemptions, _last_preempt_at
    _preemptions, _last_preempt_at = 0, None
    _job_preempts.clear()
