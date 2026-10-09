"""Daily Jev reservation ledger (port of chief-jev-spend.ts). Micro-USD integers.

Reservations are estimates and never released: a failed call still counts. Do not run
this ledger and Chief's at once; they are separate stores with separate caps.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone

JEV_DEFAULT_CAP_USD = 2.0
JEV_RESERVATION_MICROUSD = 4_000


def utc_day(now_s: float) -> str:
    return datetime.fromtimestamp(now_s, tz=timezone.utc).strftime("%Y-%m-%d")


def cap_microusd_from_env() -> int:
    """Bad or non-positive values fall back to the default; the cap must never silently become unlimited."""
    try:
        usd = float(os.environ.get("GUARDIAN_JEV_DAILY_CAP_USD", JEV_DEFAULT_CAP_USD))
    except ValueError:
        return round(JEV_DEFAULT_CAP_USD * 1_000_000)
    if not usd > 0 or usd == float("inf"):
        return round(JEV_DEFAULT_CAP_USD * 1_000_000)
    return round(usd * 1_000_000)


class JevSpendLedger:
    def __init__(self, conn: sqlite3.Connection, cap_microusd: int | None = None,
                 reservation_microusd: int = JEV_RESERVATION_MICROUSD) -> None:
        self._conn = conn
        self.cap = cap_microusd if cap_microusd is not None else cap_microusd_from_env()
        self.reservation = reservation_microusd
        if self.cap <= 0 or self.reservation <= 0:
            raise ValueError("Jev spend limits must be positive")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jev_daily_spend ("
            "utc_day TEXT PRIMARY KEY, reserved_microusd INTEGER NOT NULL, decisions INTEGER NOT NULL)"
        )
        conn.commit()

    @classmethod
    def open(cls, db_path: str, **kwargs) -> "JevSpendLedger":
        # Own autocommit connection: explicit BEGIN IMMEDIATE must not collide with another store's implicit transaction.
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return cls(conn, **kwargs)

    def reserve(self, now_s: float | None = None) -> bool:
        day = utc_day(time.time() if now_s is None else now_s)
        try:
            # IMMEDIATE so two Guardian processes cannot both read the same headroom.
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute("SELECT reserved_microusd FROM jev_daily_spend WHERE utc_day = ?", (day,)).fetchone()
            if (row[0] if row else 0) + self.reservation > self.cap:
                self._conn.execute("ROLLBACK")
                return False
            self._conn.execute(
                "INSERT INTO jev_daily_spend (utc_day, reserved_microusd, decisions) VALUES (?, ?, 1) "
                "ON CONFLICT(utc_day) DO UPDATE SET reserved_microusd = reserved_microusd + excluded.reserved_microusd, "
                "decisions = decisions + 1",
                (day, self.reservation),
            )
            self._conn.execute("COMMIT")
            return True
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return False

    def reserved_for(self, now_s: float | None = None) -> dict:
        row = self._conn.execute(
            "SELECT reserved_microusd, decisions FROM jev_daily_spend WHERE utc_day = ?",
            (utc_day(time.time() if now_s is None else now_s),),
        ).fetchone()
        return {"reserved_microusd": row[0] if row else 0, "decisions": row[1] if row else 0}
