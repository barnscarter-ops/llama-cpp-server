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
        cfg = json.loads(json.dumps(lane.raw["safety"]))
        for key in ("status_cmd", "stop_cmd", "start_cmd"):  # one setting names the PM2 home for all three commands
            cfg[key] = [part.replace("{pm2_home}", cfg.get("pm2_home", "")) for part in cfg[key]]
        self.cfg = cfg
        lock = cfg.get("lock_file") or str(markers.dir / f"{lane.name}.lock")  # every lane gets a lock, not just the R9700
        self.mutex = LaneMutex(lock) if use_mutex else None
        self.prior_active: list[str] = []
        self.consecutive_crashes = 0
        self.entered = False
        self.entered_ts = 0.0
        self.since_check = True
        self._pm2_uptime: dict = {}
        self._signal_abort: str | None = None
        self._recheck_on = False   # only after enter() has stopped everything
        self._last_recheck = 0.0
        self._unknown_rechecks: dict[str, int] = {}   # consecutive unknown readings per service

    # -- service state --
    def _state(self, name: str) -> tuple[str, str]:
        """("active" | "inactive" | "unknown", detail). Retries transient failures (pm2 jlist through gsudo times out now and then)."""
        tries = int(self.cfg.get("status_retries", 3))
        st, why = self._state_once(name)
        for _ in range(max(0, tries - 1)):
            if st != "unknown" or why.endswith("not in pm2 list"):
                break
            self.b.sleep(3.0)
            st, why = self._state_once(name)
        return st, why

    def _state_once(self, name: str) -> tuple[str, str]:
        kind = self.cfg["kind"]
        r = self.b.run(_fmt(self.cfg["status_cmd"], name), timeout=60)
        if kind == "systemd":
            out = r.stdout.strip()
            if r.rc == 0 and out == "active":
                return "active", out
            if r.rc == 3 and out in ("inactive", "failed", "deactivating"):
                return "inactive", out
            return "unknown", f"rc={r.rc} {out or r.stderr.strip()[:80]}"
        if kind == "pm2":
            if r.rc != 0:
                return "unknown", f"rc={r.rc} {r.stderr.strip()[:80]}"
            apps = None
            for line in reversed(r.stdout.splitlines()):  # the JSON array is the last bracketed line; banners come first
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    try:
                        apps = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if apps is None:
                return "unknown", "no pm2 jlist JSON in output"
            for app in apps:
                if app.get("name") == name:
                    env = app.get("pm2_env") or {}
                    self._pm2_uptime[name] = env.get("pm_uptime")
                    return ("active" if env.get("status") == "online" else "inactive"), str(env.get("status"))
            return "unknown", f"{name} not in pm2 list"
        raise BenchError(f"unknown safety kind {kind!r}")

    def _is_active(self, name: str) -> bool:
        return self._state(name)[0] == "active"

    def _services(self) -> list[str]:
        return [s["name"] for s in self.cfg["services"]]

    def _snapshot_services(self) -> list[str]:
        """Which production services are running now. Unknown status aborts: never assume 'not running'."""
        active = []
        for name in self._services():
            st, why = self._state(name)
            if st == "unknown":
                raise AbortRun(f"cannot tell whether {name} is running ({why}); refusing to proceed", "status_unknown")
            if st == "active":
                active.append(name)
        if not active and not self.window.allows("allow_no_production_service"):
            raise AbortRun("none of the configured production services is running; check the lane config "
                           "(or set allow_no_production_service in the window if that is expected)", "no_production_service")
        return active

    # -- resident-model identity (R9700): which model the production service really loaded --
    def identity_text(self) -> str | None:
        sw = self.lane.raw.get("swap") or {}
        cmd = sw.get("identity_cmd")
        if not cmd:
            return None
        r = self.b.run(cmd, timeout=30)
        return r.stdout if r.rc == 0 else ""

    def identity_is(self, which: str) -> tuple[bool, str]:
        """which: 'nemotron' (the resting model) or 'qwen'. Matches the running process command line."""
        sw = self.lane.raw.get("swap") or {}
        text = self.identity_text()
        if text is None:
            return True, "no identity check configured"
        want, other = (sw["nemotron_regex"], sw["qwen_regex"]) if which == "nemotron" else (sw["qwen_regex"], sw["nemotron_regex"])
        if not re.search(want, text):
            return False, f"resident model is not {which}: {text.strip()[:120] or 'no llama-server process'}"
        if re.search(other, text):
            return False, f"both models appear in the process list: {text.strip()[:120]}"
        return True, "ok"

    # -- enter / restore --
    def enter(self) -> None:
        self.window.require("stop_services")
        if self.markers.restore_pending():
            raise AbortRun("RESTORE_REQUIRED marker already present: restore the production service "
                           "(run_lane.py restore) before a new run", "restore_pending")
        if self.mutex:
            self.mutex.acquire()
        try:
            self.prior_active = self._snapshot_services()
            self.entered_ts = self.b.now()
            self.since_check = True
            self.markers.mark_restore_required({"lane": self.lane.name, "was_active": self.prior_active,
                                                "services": self._services(), "ts": self.entered_ts})
        except BaseException:
            if self.mutex:  # nothing stopped yet and no marker: the lock must not outlive this failed start
                self.mutex.release()
            raise
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
            st, why = self._state(name)
            if st != "inactive":
                raise AbortRun(f"{name} is {st} after stop ({why})", "stop_failed")
        # Guardian wakes an idle-stopped seat on demand, so a service that was stopped at the snapshot can have been
        # started again before Guardian itself went down. Stop any such latecomer; it is not restored (it was off).
        for name in self._services():
            if name in self.prior_active:
                continue
            st, why = self._state(name)
            if st == "unknown":
                raise AbortRun(f"cannot tell whether {name} is running ({why}); refusing to proceed", "status_unknown")
            if st == "active":
                r = self.b.run(_fmt(self.cfg["stop_cmd"], name), timeout=120)
                self.ledger.event("safety.stop_late", lane=self.lane.name, service=name, rc=r.rc)
                if r.rc != 0 or self._state(name)[0] != "inactive":
                    raise AbortRun(f"{name} started during the stop and could not be stopped again", "stop_failed")
        if self.telemetry:
            idle = self.telemetry.idle_vram_mib()
            if idle is not None and idle > GPU_IDLE_MAX_MIB:
                raise AbortRun(f"GPU not idle after stopping services ({idle:.0f} MiB in use)", "gpu_busy")
            self.telemetry.capture_baseline()
        self._check_disk()
        self._last_recheck = self.b.now()
        self._recheck_on = True

    def check_services(self, force: bool = False) -> None:
        """Public hook for the point runner: `force` re-reads the services right now (before every server launch)."""
        self._recheck_services(force)

    def _recheck_services(self, force: bool = False) -> None:
        """Every `recheck_s`, and before each launch, make sure nothing came back (Guardian's prewarm, a `pm2 start`
        that was in flight when Guardian was stopped): two models must never share the card."""
        every = float(self.cfg.get("recheck_s", 0) or 0)
        if not (self._recheck_on and every):
            return
        if not force and self.b.now() - self._last_recheck < every:
            return
        self._last_recheck = self.b.now()
        for name in self._services():
            st, why = self._state(name)
            if st == "active":
                raise AbortRun(f"{name} came back online during the window ({why}); stopping so two models never share the GPU",
                               "service_resurrected")
            if st == "unknown":
                self._unknown_rechecks[name] = self._unknown_rechecks.get(name, 0) + 1
                if self._unknown_rechecks[name] >= 3:
                    raise AbortRun(f"cannot read {name}'s status ({why}) on three checks in a row", "status_unknown")
            else:
                self._unknown_rechecks[name] = 0

    def _journal_cmd(self, name: str, since: float | None) -> list[str]:
        base = _fmt(self.cfg["journal_cmd"], name)
        if since is None:
            return base
        out, skip = [], 0
        for part in base:
            if skip:
                skip -= 1
            elif part in ("-n", "--lines"):
                skip = 1
            else:
                out.append(part)
        return out + ["--since", f"@{int(since)}"]

    def service_ready(self, name: str, since: float | None) -> tuple[bool, str]:
        """Active, and (when a journal pattern is configured) listening again since `since`."""
        st, why = self._state(name)
        if st != "active":
            return False, f"{name} is {st} ({why})"
        regex = self.cfg.get("restore_verify_log_regex")
        if self.cfg["kind"] == "systemd" and regex and self.cfg.get("journal_cmd"):
            r = self.b.run(self._journal_cmd(name, since), timeout=60)
            if not re.search(regex, r.stdout):
                return False, f"{name}: no '{regex}' line in the journal since the stop"
        if self.cfg["kind"] == "pm2" and since is not None:
            up = self._pm2_uptime.get(name)
            if up is None or float(up) < since * 1000.0 - 1000.0:
                return False, f"{name}: pm2 uptime predates the stop, so it was not restarted"
        return True, "ok"

    def _verify_restored(self) -> tuple[bool, str]:
        since = self.entered_ts if self.since_check else None
        for name in self.prior_active:
            ok, why = self.service_ready(name, since)
            if not ok:
                return False, why
        if (self.lane.raw.get("swap") or {}).get("identity_cmd"):
            ok, why = self.identity_is("nemotron")
            if not ok:
                return False, why
        return True, "ok"

    def restore(self, wait_s: float = 600.0) -> None:
        """Start what was active before, verify, then clear the marker. Idempotent."""
        if not self.entered and not self.markers.restore_pending():
            return
        prior = self.prior_active
        if not prior and self.markers.restore_pending():
            try:
                info = json.loads(self.markers.restore_file.read_text())
                prior = list(info["was_active"])
                self.entered_ts = float(info.get("ts", 0.0))
                self.since_check = info.get("mode") != "watch"
            except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                # A truncated marker must never read as "nothing was running": keep it and make a human look.
                raise RestoreFailed(f"RESTORE_REQUIRED marker is unreadable ({exc}); start the production services by hand, "
                                    "then delete the marker") from exc
            self.prior_active = prior
        for name in reversed(self._services()):
            if name in prior and self._state(name)[0] != "active":
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
        self._recheck_services()
        if self.telemetry:
            self.telemetry.observe()

    @contextmanager
    def _signals(self):
        """First SIGINT/SIGTERM sets a flag checked between points; a second one interrupts whatever is blocked."""
        handlers = {}

        def on_signal(signum, _frame):
            if self._signal_abort:
                raise KeyboardInterrupt(f"second {signal.Signals(signum).name}: interrupting")
            self._signal_abort = signal.Signals(signum).name

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                handlers[sig] = signal.signal(sig, on_signal)
            except ValueError:  # not the main thread
                pass
        try:
            yield
        finally:
            for sig, h in handlers.items():
                signal.signal(sig, h)

    @contextmanager
    def watch(self, before_restore=None):
        """For the R9700 swap test: nothing is stopped here (the swap scripts do that and take the
        lock themselves), but the marker is set so an interrupted run is recoverable. On the way out
        `before_restore` puts the resting model back, then restore verifies the resident model."""
        if self.markers.restore_pending():
            raise AbortRun("RESTORE_REQUIRED marker already present", "restore_pending")
        self.prior_active = self._snapshot_services()
        self.entered_ts = self.b.now()
        self.since_check = False
        self.markers.mark_restore_required({"lane": self.lane.name, "was_active": self.prior_active,
                                            "services": self._services(), "ts": self.entered_ts, "mode": "watch"})
        self.ledger.event("safety.watch", lane=self.lane.name, was_active=self.prior_active)
        self.entered = True
        with self._signals():
            try:
                yield self
            finally:
                try:
                    if before_restore:
                        before_restore()
                except Exception as exc:  # noqa: BLE001  restore below decides whether this mattered
                    self.ledger.event("safety.before_restore_failed", lane=self.lane.name, error=str(exc)[:200])
                self.restore()

    @contextmanager
    def session(self):
        """enter(); yield; always restore. A failed restore raises after the body's own error is recorded."""
        body_error: BaseException | None = None
        with self._signals():
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
