"""Approval records for loading Qwen on the R9700.

Qwen never loads on a string gate: Carter's decision is a durable record that
a task consumes exactly once. Statuses: pending, approved, denied, used, expired.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from typing import Any, Callable

STATUSES = ("pending", "approved", "denied", "used", "expired")
DEFAULT_TTL_S = 1800.0


def qwen_approval_enabled() -> bool:
    return os.environ.get("GUARDIAN_QWEN_APPROVAL", "false").strip().lower() in {"true", "1", "yes"}


def approval_ttl_s() -> float:
    try:
        return max(1.0, float(os.environ.get("GUARDIAN_QWEN_APPROVAL_TTL_S", DEFAULT_TTL_S)))
    except ValueError:
        return DEFAULT_TTL_S


class ApprovalError(Exception):
    """An approval cannot be used; code is the guardian error code."""

    def __init__(self, code: str, message: str, status: int, approval: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.approval = code, message, status, approval


class ApprovalStore:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], float] = time.time) -> None:
        self._conn = conn
        self._clock = clock
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qwen_approvals (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','approved','denied','used','expired')),
                created REAL NOT NULL,
                decided_by TEXT,
                decided_at REAL,
                expires_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS qwen_approvals_task ON qwen_approvals(task_id, status)")
        conn.commit()

    def _expire_due(self) -> None:
        self._conn.execute(
            "UPDATE qwen_approvals SET status = 'expired' WHERE status IN ('pending','approved') AND expires_at <= ?",
            (self._clock(),),
        )
        self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def get(self, approval_id: str) -> dict[str, Any] | None:
        self._expire_due()
        return self._row(self._conn.execute("SELECT * FROM qwen_approvals WHERE id = ?", (approval_id,)).fetchone())

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        self._expire_due()
        if status:
            rows = self._conn.execute(
                "SELECT * FROM qwen_approvals WHERE status = ? ORDER BY created ASC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM qwen_approvals ORDER BY created ASC").fetchall()
        return [dict(r) for r in rows]

    def create_pending(self, task_id: str, ttl_s: float | None = None) -> dict[str, Any]:
        """One live approval per task: a retry gets the same record back."""
        self._expire_due()
        live = self._conn.execute(
            "SELECT * FROM qwen_approvals WHERE task_id = ? AND status IN ('pending','approved') "
            "ORDER BY created DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if live:
            return dict(live)
        now = self._clock()
        approval_id = f"qa_{uuid.uuid4().hex}"
        self._conn.execute(
            "INSERT INTO qwen_approvals (id, task_id, status, created, expires_at) VALUES (?, ?, 'pending', ?, ?)",
            (approval_id, task_id, now, now + (ttl_s if ttl_s is not None else approval_ttl_s())),
        )
        self._conn.commit()
        return self.get(approval_id)  # type: ignore[return-value]

    def decide(self, approval_id: str, approve: bool, decided_by: str) -> dict[str, Any]:
        self._expire_due()
        row = self.get(approval_id)
        if row is None:
            raise ApprovalError("approval_not_found", "No such approval.", 404)
        if row["status"] != "pending":
            raise ApprovalError(
                "approval_not_pending", f"Approval is {row['status']}; only pending approvals can be decided.", 409, row
            )
        self._conn.execute(
            "UPDATE qwen_approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            ("approved" if approve else "denied", decided_by, self._clock(), approval_id),
        )
        self._conn.commit()
        return self.get(approval_id)  # type: ignore[return-value]

    def check_usable(self, approval_id: str | None) -> dict[str, Any]:
        """Raise ApprovalError unless the approval is approved and live. Does not consume it."""
        if not approval_id:
            raise ApprovalError("approval_required", "Loading Qwen needs Carter's approval.", 409)
        row = self.get(approval_id)
        if row is None:
            raise ApprovalError("approval_not_found", "No such approval.", 404)
        status = row["status"]
        if status == "approved":
            return row
        codes = {
            "pending": ("needs_approval", "Waiting for Carter's decision.", 409),
            "denied": ("approval_denied", "Carter denied this Qwen load.", 403),
            "used": ("approval_used", "This approval was already used.", 409),
            "expired": ("approval_expired", "This approval expired.", 409),
        }
        code, message, http = codes[status]
        raise ApprovalError(code, message, http, row)

    def consume(self, approval_id: str) -> dict[str, Any]:
        """approved -> used, once. A second call, or an expired approval, is rejected."""
        self._expire_due()
        cursor = self._conn.execute(
            "UPDATE qwen_approvals SET status = 'used' WHERE id = ? AND status = 'approved' AND expires_at > ?",
            (approval_id, self._clock()),
        )
        self._conn.commit()
        if cursor.rowcount != 1:
            self.check_usable(approval_id)  # raises with the precise reason
            raise ApprovalError("approval_used", "This approval was already used.", 409)
        return self.get(approval_id)  # type: ignore[return-value]
