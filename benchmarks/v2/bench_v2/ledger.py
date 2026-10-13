"""Append-only results ledger (one JSON object per line, hash-chained).

Records are only ever appended. Each carries the hash of the previous line, so
an edited or deleted line is detected by `verify()`, and the harness refuses to
run on a broken ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterator

from . import SCHEMA_VERSION

GENESIS = "0" * 64
PHASES = {"header", "event", "smoke", "tune", "confirm", "ladder", "quality", "guard", "jev", "swap"}


class LedgerError(Exception):
    pass


def _digest(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str | os.PathLike, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- reading --
    def records(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _last(self) -> tuple[int, str]:
        seq, h = 0, GENESIS
        for rec in self.records():
            seq, h = rec["seq"], rec["hash"]
        return seq, h

    def verify(self) -> int:
        """Return the record count; raise LedgerError on any break in the chain."""
        prev, n = GENESIS, 0
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"line {lineno}: not JSON ({exc})") from exc
                claimed = rec.pop("hash", None)
                if rec.get("prev") != prev:
                    raise LedgerError(f"line {lineno}: chain broken (prev mismatch)")
                body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
                if _digest(prev, body) != claimed:
                    raise LedgerError(f"line {lineno}: hash mismatch (edited record)")
                prev, n = claimed, n + 1
                if rec.get("seq") != n:
                    raise LedgerError(f"line {lineno}: seq {rec.get('seq')} != {n} (deleted or reordered)")
        return n

    # -- writing --
    def append(self, phase: str, **fields) -> dict:
        if phase not in PHASES:
            raise LedgerError(f"unknown phase {phase!r}")
        with self._locked():
            return self._append_locked(phase, fields)

    def _append_locked(self, phase: str, fields: dict) -> dict:
        seq, prev = self._last()
        rec = {"schema": SCHEMA_VERSION, "seq": seq + 1, "ts": round(self._clock(), 3), "phase": phase,
               "prev": prev, **fields}
        rec = json.loads(json.dumps(rec))  # int dict keys become strings now, so the stored line hashes the same on re-read
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        rec_hash = _digest(prev, body)
        line = json.dumps({**rec, "hash": rec_hash}, sort_keys=True, separators=(",", ":"))
        with open(self.path, "a", encoding="utf-8") as fh:  # O_APPEND semantics
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return {**rec, "hash": rec_hash}

    def _locked(self):
        """Advisory lock so two processes cannot interleave a chain (best effort off POSIX)."""
        import contextlib

        @contextlib.contextmanager
        def lock():
            lock_path = str(self.path) + ".lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except ImportError:
                    pass
                yield
            finally:
                os.close(fd)

        return lock()

    def event(self, kind: str, **fields) -> dict:
        return self.append("event", kind=kind, **fields)

    def where(self, **match) -> list[dict]:
        out = []
        for r in self.records():
            if all(r.get(k) == v for k, v in match.items()):
                out.append(r)
        return out


def read_all(directory: str | os.PathLike) -> list[dict]:
    """Every record of every lane ledger in a directory, verified, oldest first."""
    out = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        led = Ledger(path)
        led.verify()
        out.extend(led.records())
    return sorted(out, key=lambda r: (r["ts"], r["seq"]))
