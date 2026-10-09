"""Counts how work reaches Guardian so Carter can see who still bypasses the task API.

Observation only: nothing here changes routing. Totals live in memory (since process start)
and every call writes one log line carrying them.
"""

from __future__ import annotations

import re
from typing import Any

PATHS = ("direct_named", "direct_default", "queue_job", "task_api")
_UNSAFE = re.compile(r"[^\w.:/ -]")


def _clean(value: Any, limit: int = 60) -> str:
    return _UNSAFE.sub("?", str(value if value not in (None, "") else "-"))[:limit]


class DispatchPathCounter:
    def __init__(self) -> None:
        self.totals = {path: 0 for path in PATHS}

    def note(self, path: str, **who: Any) -> str:
        """Count one arrival and return the log line (also what tests assert on)."""
        self.totals[path] += 1
        totals = " ".join(f"{p}={n}" for p, n in self.totals.items())
        detail = " ".join(f"{k}={_clean(v)}" for k, v in who.items())
        return f"dispatch_path {path} {detail} | totals {totals}"
