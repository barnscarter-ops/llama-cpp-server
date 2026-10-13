"""Window file, STOP file, RESTORE_REQUIRED marker, per-machine mutex.

Nothing real starts without a window file Carter created. The harness never
writes one; `run_lane.py window-template` only prints an example.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import WindowError

REQUIRED_APPROVALS = {
    "workbench": ("stop_services",),
    "r9700": ("stop_services", "guardian_quiet"),
}

WINDOW_TEMPLATE = {
    "lane": "workbench",
    "start": "2026-10-14T01:00:00Z",
    "end": "2026-10-14T05:00:00Z",
    "approved_by": "Carter",
    "approvals": {
        "stop_services": True,
        "guardian_quiet": True,
        "qwen_load": False,
        "moe_exception": False,
        "alternates": False,
        "cloud_spend_cap_usd": 0
    },
    "note": "Created by Carter. The harness refuses to start without this file."
}


def _parse(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


@dataclass
class Window:
    lane: str
    start: float
    end: float
    approved_by: str
    approvals: dict = field(default_factory=dict)
    path: str = ""

    def allows(self, key: str) -> bool:
        return bool(self.approvals.get(key))

    def require(self, key: str) -> None:
        if not self.allows(key):
            raise WindowError(f"window does not approve {key!r}")

    def remaining_s(self, now: float) -> float:
        return self.end - now


def load_window(path: str | os.PathLike | None, lane: str, now: float | None = None) -> Window:
    if not path or not Path(path).exists():
        raise WindowError("no window file: nothing real may start without one that Carter created")
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        win = Window(doc["lane"], _parse(doc["start"]), _parse(doc["end"]), str(doc.get("approved_by", "")).strip(),
                     dict(doc.get("approvals") or {}), str(path))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise WindowError(f"unreadable window file: {exc}") from exc
    if win.lane != lane:
        raise WindowError(f"window is for lane {win.lane!r}, not {lane!r}")
    if not win.approved_by:
        raise WindowError("window has no approved_by")
    t = time.time() if now is None else now
    if not (win.start <= t < win.end):
        raise WindowError("outside the approved window")
    for key in REQUIRED_APPROVALS.get(lane, ()):
        win.require(key)
    return win


class RunMarkers:
    """RESTORE_REQUIRED and STOP files for one lane."""

    def __init__(self, runs_dir: str | os.PathLike, lane: str) -> None:
        self.dir = Path(runs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.restore_file = self.dir / f"{lane}.RESTORE_REQUIRED"
        self.stop_file = self.dir / f"{lane}.STOP"
        self.global_stop = self.dir / "STOP"

    def stop_requested(self) -> bool:
        return self.stop_file.exists() or self.global_stop.exists()

    def restore_pending(self) -> bool:
        return self.restore_file.exists()

    def mark_restore_required(self, info: dict) -> None:
        self.restore_file.write_text(json.dumps(info, indent=1), encoding="utf-8")

    def clear_restore_required(self) -> None:
        try:
            self.restore_file.unlink()
        except FileNotFoundError:
            pass


class LaneMutex:
    """One model server per machine. Uses flock where available, else an O_EXCL lock file."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = str(path)
        self._fd: int | None = None
        self._excl = False

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if fcntl is not None:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                raise WindowError(f"lane lock {self.path} is held (another swap or run?)") from exc
            self._fd = fd
        else:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                self._excl = True
            except FileExistsError as exc:
                raise WindowError(f"lane lock {self.path} exists") from exc

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            import fcntl
            if not self._excl:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except ImportError:
            pass
        os.close(self._fd)
        self._fd = None
        if self._excl:
            try:
                os.unlink(self.path)
            except OSError:
                pass
