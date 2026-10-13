"""Stop the production service before a lane, restore it after, abort safely.

The production Nemotron service on the R9700 and Guardian's seat on the
Workbench are touched only here, only inside an approved window, and every
step is written to the ledger. Restore runs in a `finally` and on SIGINT/SIGTERM.
"""

from __future__ import annotations

import json
import re
import signal
from contextlib import contextmanager

from .backend import Backend
from .config import LaneConfig
from .errors import AbortRun, BenchError
from .ledger import Ledger
from .window import LaneMutex, RunMarkers, Window

MIN_FREE_DISK_GB = 20.0
GPU_IDLE_MAX_MIB = 1500.0  # above this, something else is using the GPU


class RestoreFailed(BenchError):
    """The production service could not be verified as restored; marker left in place."""


def _fmt(cmd: list[str], name: str) -> list[str]:
    return [part.replace("{name}", name) for part in cmd]


class SafetyController:
    def __init__(self, backend: Backend, ledger: Ledger, lane: LaneConfig, window: Window,
                 markers: RunMarkers, telemetry=None, guarded=None, use_mutex: bool = True) -> None:
        self.b = backend
        self.ledger = ledger
        self.lane = lane
        self.window = window
        self.markers = markers
        self.telemetry = telemetry
        self.guarded = guarded  # GuardedBackend, for the leaving-the-port check
        cfg = lane.raw["safety"]
        self.cfg = cfg
        self.mutex = LaneMutex(cfg["lock_file"]) if (use_mutex and cfg.get("lock_file")) else None
        self.prior_active: list[str] = []
        self.consecutive_crashes = 0
        self.entered = False
        self._signal_abort: str | None = None

    # -- service state --
    def _is_active(self, name: str) -> bool:
        kind = self.cfg["kind"]
        r = self.b.run(_fmt(self.cfg["status_cmd"], name), timeout=60)
        if kind == "systemd":
            return r.rc == 0 and r.stdout.strip() == "active"
        if kind == "pm2":
            text = r.stdout
            start = text.find("[")
            if r.rc != 0 or start < 0:
                return False
            try:
                for app in json.loads(text[start:]):
                    if app.get("name") == name:
                        return (app.get("pm2_env") or {}).get("status") == "online"
            except json.JSONDecodeError:
                return False
            return False
        raise BenchError(f"unknown safety kind {kind!r}")

    def _services(self) -> list[str]:
        return [s["name"] for s in self.cfg["services"]]

    # -- enter / restore --
    def enter(self) -> None:
        self.window.require("stop_services")
        if self.markers.restore_pending():
            raise AbortRun("RESTORE_REQUIRED marker already present: restore the production service "
                           "(run_lane.py restore) before a new run", "restore_pending")
        if self.mutex:
            self.mutex.acquire()
        self.prior_active = [n for n in self._services() if self._is_active(n)]
        self.markers.mark_restore_required({"lane": self.lane.name, "was_active": self.prior_active,
                                            "services": self._services(), "ts": self.b.now()})
        self.ledger.event("safety.enter", lane=self.lane.name, was_active=self.prior_active)
        self.entered = True
        for name in self._services():
            if name not in self.prior_active:
                continue
            r = self.b.run(_fmt(self.cfg["stop_cmd"], name), timeout=120)
            self.ledger.event("safety.stop", lane=self.lane.name, service=name, rc=r.rc)
            if r.rc != 0:
                raise AbortRun(f"could not stop {name}: rc={r.rc} {r.stderr[:200]}", "stop_failed")
        for name in self.prior_active:
            if self._is_active(name):
                raise AbortRun(f"{name} is still active after stop", "stop_failed")
        if self.telemetry:
            idle = self.telemetry.idle_vram_mib()
            if idle is not None and idle > GPU_IDLE_MAX_MIB:
                raise AbortRun(f"GPU not idle after stopping services ({idle:.0f} MiB in use)", "gpu_busy")
        self._check_disk()

    def _verify_restored(self) -> tuple[bool, str]:
        for name in self.prior_active:
            if not self._is_active(name):
                return False, f"{name} not active"
        regex = self.cfg.get("restore_verify_log_regex")
        if self.cfg["kind"] == "systemd" and regex and self.cfg.get("journal_cmd"):
            for name in self.prior_active:
                r = self.b.run(_fmt(self.cfg["journal_cmd"], name), timeout=60)
                if not re.search(regex, r.stdout):
                    return False, f"{name}: no '{regex}' line in the journal yet"
        return True, "ok"

    def restore(self, wait_s: float = 600.0) -> None:
        """Start what was active before, verify, then clear the marker. Idempotent."""
        if not self.entered and not self.markers.restore_pending():
            return
        prior = self.prior_active
        if not prior and self.markers.restore_pending():
            try:
                prior = json.loads(self.markers.restore_file.read_text()).get("was_active", [])
            except (OSError, json.JSONDecodeError):
                prior = []
            self.prior_active = prior
        for name in reversed(self._services()):
            if name in prior and not self._is_active(name):
                r = self.b.run(_fmt(self.cfg["start_cmd"], name), timeout=180)
                self.ledger.event("safety.start", lane=self.lane.name, service=name, rc=r.rc)
        waited, ok, why = 0.0, False, "not checked"
        while True:
            ok, why = self._verify_restored()
            if ok or waited >= wait_s:
                break
            self.b.sleep(5.0)
            waited += 5.0
        self.ledger.event("safety.restore", lane=self.lane.name, ok=ok, detail=why, waited_s=waited)
        if not ok:
            raise RestoreFailed(f"restore not verified ({why}); RESTORE_REQUIRED left in place")
        self.markers.clear_restore_required()
        if self.mutex:
            self.mutex.release()
        self.entered = False

    # -- abort checks, called between every point --
    def _check_disk(self) -> None:
        runs = str(self.markers.dir)
        free = self.b.free_disk_gb(runs)
        if free < MIN_FREE_DISK_GB:
            raise AbortRun(f"only {free:.1f} GB free on the runs disk", "disk")

    def note_crash(self, crashed: bool) -> None:
        self.consecutive_crashes = self.consecutive_crashes + 1 if crashed else 0
        if self.consecutive_crashes >= 2:
            raise AbortRun("two consecutive crashes: pausing and asking Carter", "double_crash")

    def check(self) -> None:
        if self._signal_abort:
            raise AbortRun(f"received {self._signal_abort}", "signal")
        if self.markers.stop_requested():
            raise AbortRun("STOP file present", "stop_file")
        if self.window and self.b.now() >= self.window.end:
            raise AbortRun("approved window ended", "window_end")
        if self.guarded is not None and self.guarded.violations:
            raise AbortRun(f"bench request left its port: {self.guarded.violations[0]}", "forbidden_target")
        self._check_disk()
        if self.telemetry:
            self.telemetry.observe()

    @contextmanager
    def watch(self):
        """For the R9700 swap test: nothing is stopped here (the swap scripts do that and take the
        lock themselves), but the marker is set so an interrupted run is recoverable, and the
        production service is verified restored at the end."""
        if self.markers.restore_pending():
            raise AbortRun("RESTORE_REQUIRED marker already present", "restore_pending")
        self.prior_active = [n for n in self._services() if self._is_active(n)]
        self.markers.mark_restore_required({"lane": self.lane.name, "was_active": self.prior_active,
                                            "services": self._services(), "ts": self.b.now(), "mode": "watch"})
        self.ledger.event("safety.watch", lane=self.lane.name, was_active=self.prior_active)
        self.entered = True
        try:
            yield self
        finally:
            self.restore()

    @contextmanager
    def session(self):
        """enter(); yield; always restore. A failed restore raises after the body's own error is recorded."""
        handlers = {}

        def on_signal(signum, _frame):
            self._signal_abort = signal.Signals(signum).name

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                handlers[sig] = signal.signal(sig, on_signal)
            except ValueError:  # not the main thread
                pass
        body_error: BaseException | None = None
        try:
            self.enter()
            yield self
        except BaseException as exc:
            body_error = exc
            raise
        finally:
            try:
                if self.entered or self.markers.restore_pending():
                    self.restore()
            except RestoreFailed:
                if body_error is None:
                    raise
                self.ledger.event("safety.restore_failed_after_error", lane=self.lane.name)
            finally:
                for sig, h in handlers.items():
                    signal.signal(sig, h)
