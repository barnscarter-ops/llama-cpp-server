"""Durable, Hermes-decided work queue for llama-guardian.

The queue deliberately owns scheduling only.  It never gives a model direct
filesystem access and it never chooses a cloud provider: Hermes returns the
routing verdict and the calling harness owns any fallback execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROUTES = frozenset({"queue_qwen", "bypass", "fallback_cloud"})


class HermesDecisionError(RuntimeError):
    """Hermes could not produce a valid, policy-safe routing decision."""


@dataclass(frozen=True)
class QueueJob:
    job_id: str
    idempotency_key: str
    source: str
    priority: int
    request: dict[str, Any]
    decision: dict[str, Any]
    status: str
    attempts: int
    created_at: float
    started_at: float | None
    finished_at: float | None
    result: dict[str, Any] | None
    error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueJob":
        return cls(
            job_id=row["job_id"],
            idempotency_key=row["idempotency_key"],
            source=row["source"],
            priority=row["priority"],
            request=json.loads(row["request_json"]),
            decision=json.loads(row["decision_json"]),
            status=row["status"],
            attempts=row["attempts"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
        )

    def as_api(self, *, include_result: bool = True) -> dict[str, Any]:
        result = {
            "job_id": self.job_id,
            "source": self.source,
            "priority": self.priority,
            "decision": self.decision,
            "status": self.status,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }
        if include_result:
            result["result"] = self.result
        return result


class JobStore:
    """Small SQLite job ledger with atomic claims and restart recovery."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guardian_jobs (
                job_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                priority INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                started_at REAL,
                finished_at REAL,
                result_json TEXT,
                error TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS guardian_jobs_next ON guardian_jobs(status, priority DESC, created_at ASC)"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guardian_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                source TEXT,
                route TEXT NOT NULL,
                reason TEXT,
                priority INTEGER,
                decision_context_json TEXT
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def recover_interrupted(self) -> int:
        """Requeue a job interrupted by a guardian restart without duplication."""
        cursor = self._conn.execute(
            """
            UPDATE guardian_jobs
            SET status = 'queued', started_at = NULL,
                error = 'Requeued after guardian restart before a terminal result.'
            WHERE status = 'running'
            """
        )
        self._conn.commit()
        return cursor.rowcount

    def get(self, job_id: str) -> QueueJob | None:
        row = self._conn.execute(
            "SELECT * FROM guardian_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return QueueJob.from_row(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> QueueJob | None:
        row = self._conn.execute(
            "SELECT * FROM guardian_jobs WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return QueueJob.from_row(row) if row else None

    def submit(
        self,
        *,
        idempotency_key: str,
        source: str,
        priority: int,
        request: dict[str, Any],
        decision: dict[str, Any],
    ) -> tuple[QueueJob, bool]:
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing, False

        now = time.time()
        job_id = f"qj_{uuid.uuid4().hex}"
        self._conn.execute(
            """
            INSERT INTO guardian_jobs (
                job_id, idempotency_key, source, priority, request_json,
                decision_json, status, attempts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?)
            """,
            (
                job_id,
                idempotency_key,
                source,
                priority,
                json.dumps(request, separators=(",", ":")),
                json.dumps(decision, separators=(",", ":")),
                now,
            ),
        )
        self._conn.commit()
        return self.get(job_id), True  # type: ignore[return-value]

    def claim_next(self) -> QueueJob | None:
        """Atomically claim the highest-priority oldest queued job."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                SELECT job_id FROM guardian_jobs
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                self._conn.commit()
                return None
            now = time.time()
            self._conn.execute(
                """
                UPDATE guardian_jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = ?, finished_at = NULL, error = NULL
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, row["job_id"]),
            )
            self._conn.commit()
            return self.get(row["job_id"])
        except Exception:
            self._conn.rollback()
            raise

    def finish(self, job_id: str, result: dict[str, Any]) -> QueueJob:
        self._conn.execute(
            """
            UPDATE guardian_jobs
            SET status = 'succeeded', finished_at = ?, result_json = ?, error = NULL
            WHERE job_id = ? AND status = 'running'
            """,
            (time.time(), json.dumps(result, separators=(",", ":")), job_id),
        )
        self._conn.commit()
        return self.get(job_id)  # type: ignore[return-value]

    def fail(self, job_id: str, error: str) -> QueueJob:
        self._conn.execute(
            """
            UPDATE guardian_jobs
            SET status = 'failed', finished_at = ?, error = ?
            WHERE job_id = ? AND status = 'running'
            """,
            (time.time(), error[:2000], job_id),
        )
        self._conn.commit()
        return self.get(job_id)  # type: ignore[return-value]

    def cancel(self, job_id: str) -> QueueJob | None:
        self._conn.execute(
            """
            UPDATE guardian_jobs
            SET status = 'cancelled', finished_at = ?, error = 'Cancelled before execution.'
            WHERE job_id = ? AND status = 'queued'
            """,
            (time.time(), job_id),
        )
        self._conn.commit()
        return self.get(job_id)

    def summary(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS count FROM guardian_jobs GROUP BY status"
        ).fetchall()
        result = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
        result.update({row["status"]: row["count"] for row in rows})
        return result

    def has_queued_work(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM guardian_jobs WHERE status = 'queued' LIMIT 1"
        ).fetchone()
        return row is not None

    def log_decision(self, source: str, decision: dict[str, Any], context: dict[str, Any]) -> None:
        """Record every routing decision for auditability, including rejections."""
        self._conn.execute(
            """
            INSERT INTO guardian_decisions (ts, source, route, reason, priority, decision_context_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                source[:80] if source else None,
                decision.get("route", "unknown"),
                (decision.get("reason") or "")[:600],
                decision.get("priority"),
                json.dumps(context, separators=(',', ':'))[:8000],
            ),
        )
        self._conn.commit()


def parse_hermes_decision(output: str) -> dict[str, Any]:
    """Validate Hermes's one-object response before it can control routing."""
    text = output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)

    candidates = [text]
    candidates.extend(match.group(0) for match in re.finditer(r"\{.*?\}", text, re.DOTALL))
    for candidate in candidates:
        try:
            decision = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(decision, dict):
            continue
        route = decision.get("route")
        reason = decision.get("reason")
        priority = decision.get("priority")
        if route not in ROUTES or not isinstance(reason, str) or not reason.strip():
            continue
        if isinstance(priority, bool):
            continue
        try:
            priority = int(priority)
        except (TypeError, ValueError):
            continue
        if not 0 <= priority <= 100:
            continue
        return {"route": route, "reason": reason.strip()[:600], "priority": priority}
    raise HermesDecisionError("Hermes did not return the required routing JSON object.")


class HermesDecider:
    """Runs Hermes in one-shot mode and accepts only strict routing JSON."""

    def __init__(self) -> None:
        installed_hermes = (
            Path.home()
            / "AppData"
            / "Local"
            / "hermes"
            / "hermes-agent"
            / "venv"
            / "Scripts"
            / "hermes.exe"
        )
        self.executable = os.environ.get(
            "HERMES_DECIDER_EXE", str(installed_hermes) if installed_hermes.exists() else "hermes"
        )
        self.provider = os.environ.get("HERMES_DECIDER_PROVIDER", "").strip()
        self.model = os.environ.get("HERMES_DECIDER_MODEL", "").strip()
        self.timeout_s = max(10, int(os.environ.get("HERMES_DECIDER_TIMEOUT_S", "60")))

    async def decide(self, context: dict[str, Any], queue: dict[str, int]) -> dict[str, Any]:
        prompt = (
            "You are the routing decider for a single-slot local Qwen coding queue. "
            "Do not use tools and do not propose code. Return exactly one JSON object with "
            "route, reason, and priority. route must be one of queue_qwen, bypass, or "
            "fallback_cloud. priority must be an integer 0-100. "
            "Default to queue_qwen for any concrete coding task \u2014 bugfixes, features, "
            "refactors, tests, docs, config. Reserve bypass for truly trivial changes "
            "(a single line, a typo, a rename). Reserve fallback_cloud for work that is "
            "genuinely architectural, security-sensitive, or needs capabilities the local "
            "model lacks. The local queue processes jobs in seconds to minutes; do not "
            "route to cloud merely because one job is running. "
            "Tasks from automated harnesses (codex, claude, pi, hermes) are pre-scoped by "
            "their own planning step \u2014 weight their summaries toward queue_qwen.\n\n"
            f"Queue snapshot: {json.dumps(queue, separators=(',', ':'))}\n"
            f"Task metadata: {json.dumps(context, separators=(',', ':'))}"
        )
        command = [self.executable]
        if self.provider:
            command.extend(["--provider", self.provider])
        if self.model:
            command.extend(["--model", self.model])
        command.extend(["--oneshot", prompt, "--ignore-rules"])
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise HermesDecisionError(f"Hermes executable not found: {self.executable}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise HermesDecisionError(f"Hermes decision timed out after {self.timeout_s}s.") from exc
        if proc.returncode != 0:
            detail = (stderr or stdout).decode(errors="replace").strip()
            raise HermesDecisionError(f"Hermes decision failed (exit {proc.returncode}): {detail[:600]}")
        return parse_hermes_decision(stdout.decode(errors="replace"))
