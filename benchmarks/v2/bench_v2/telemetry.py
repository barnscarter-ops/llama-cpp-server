"""GPU telemetry: peak/idle VRAM, spill, thermal gate, power alarm."""

from __future__ import annotations

import threading
import time

import dataclasses

from .backend import Backend, GpuSample
from .errors import AbortRun


class Telemetry:
    def __init__(self, backend: Backend, temp_start_c: float = 65, temp_pause_c: float = 85,
                 temp_resume_c: float = 70, temp_abort_c: float = 90, require_shared: bool = False) -> None:
        self.b = backend
        self.temp_start_c, self.temp_pause_c = temp_start_c, temp_pause_c
        self.temp_resume_c, self.temp_abort_c = temp_resume_c, temp_abort_c
        self.require_shared = require_shared
        self.shared_baseline = 0.0
        self.reset()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def reset(self) -> None:
        self.peak_used_mib = 0.0
        self.max_shared_mib = 0.0
        self.max_temp_c = 0.0
        self.last: GpuSample | None = None
        self.samples = 0
        self.power_alarm: str | None = None

    def idle_vram_mib(self) -> float | None:
        s = self.b.gpu_sample()
        return s.used_mib if s else None

    def capture_baseline(self) -> None:
        """Shared GPU memory with everything stopped; later readings are measured above this. Fails closed when unreadable."""
        s = self.b.gpu_sample()
        if s is not None and s.shared_mib is None:
            if self.require_shared:
                raise AbortRun("shared GPU memory is not readable here, so the no-spill rule cannot be enforced "
                               "(check the shared_mem_cmd in lanes.json)", "no_spill_reading")
            return
        self.shared_baseline = s.shared_mib if s is not None else 0.0

    def observe(self) -> GpuSample | None:
        """One sample. Raises AbortRun on over-temperature or a power alarm."""
        s = self.b.gpu_sample()
        alarm = self.b.power_alarm()
        if alarm:
            self.power_alarm = alarm
            raise AbortRun(f"power alarm: {alarm}", "power_alarm")
        if s is None:
            return None
        if s.shared_mib is None:
            if self.require_shared:
                raise AbortRun("shared GPU memory reading was lost mid-run; spill cannot be ruled out", "no_spill_reading")
            s = dataclasses.replace(s, shared_mib=0.0)
        else:
            s = dataclasses.replace(s, shared_mib=max(0.0, s.shared_mib - self.shared_baseline))
        self.last = s
        self.samples += 1
        self.peak_used_mib = max(self.peak_used_mib, s.used_mib)
        self.max_shared_mib = max(self.max_shared_mib, s.shared_mib)
        self.max_temp_c = max(self.max_temp_c, s.temp_c)
        if s.temp_c >= self.temp_abort_c:
            raise AbortRun(f"GPU temperature {s.temp_c:.0f} C >= {self.temp_abort_c:.0f}", "overtemp")
        return s

    def wait_until_cool(self, target_c: float | None = None, max_wait_s: float = 1800.0) -> float:
        """Block until edge temp is under target (start 65 C; after a >85 C pause, 70 C)."""
        target = self.temp_start_c if target_c is None else target_c
        waited = 0.0
        while True:
            s = self.observe()
            if s is None or s.temp_c < target:
                return waited
            if waited >= max_wait_s:
                raise AbortRun(f"GPU never cooled below {target:.0f} C", "overtemp")
            self.b.sleep(10.0)
            waited += 10.0

    def start_background(self, interval_s: float = 1.0) -> None:
        """1 Hz sampler for real runs. Aborts are surfaced via `background_error`."""
        self.background_error: AbortRun | None = None
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.observe()
                except AbortRun as exc:
                    self.background_error = exc
                    return
                time.sleep(interval_s)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        err = getattr(self, "background_error", None)
        if err:
            raise err

    def summary(self) -> dict:
        return {"peak_mib": self.peak_used_mib, "spill_mib": self.max_shared_mib,
                "max_temp_c": self.max_temp_c, "samples": self.samples}
