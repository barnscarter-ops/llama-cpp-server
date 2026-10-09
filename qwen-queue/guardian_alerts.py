"""Durable alert events Guardian emits for Chief to relay (Jev fallback today)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class AlertStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS guardian_alerts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, kind TEXT NOT NULL, "
            "task_id TEXT, detail_json TEXT NOT NULL)"
        )
        conn.commit()

    def emit(self, kind: str, task_id: str | None, detail: dict[str, Any]) -> int:
        cursor = self._conn.execute(
            "INSERT INTO guardian_alerts (created, kind, task_id, detail_json) VALUES (?, ?, ?, ?)",
            (time.time(), kind, task_id, json.dumps(detail, separators=(",", ":"))),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def list(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created, kind, task_id, detail_json FROM guardian_alerts WHERE id > ? ORDER BY id LIMIT ?",
            (after, max(1, min(limit, 500))),
        ).fetchall()
        return [
            {"id": r[0], "created": r[1], "kind": r[2], "task_id": r[3], "detail": json.loads(r[4])} for r in rows
        ]
