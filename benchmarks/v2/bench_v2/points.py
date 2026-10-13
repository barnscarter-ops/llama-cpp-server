"""Measure one config: fit estimate, launch, verify on-GPU, warm-up, speed passes, ledger."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from . import speed
from .backend import Backend
from .config import Candidate, LaneConfig
from .errors import AbortRun
from .fitcheck import check_fit, check_loaded
from .ledger import Ledger
from .safety import SafetyController
from .server import BenchServer, ServerConfig, build_argv
from .telemetry import Telemetry


@dataclass
class Point:
    label: str
    status: str                     # ok | oom | crash | timeout | fit_reject | not_on_gpu | unstable | guard_reject
    cfg: dict
    score: float | None = None
    pp_tps: float | None = None
    tg_tps: float | None = None
    ttft_ms: float | None = None
    shapes: dict = field(default_factory=dict)
    cold_s: float | None = None
    peak_mib: float | None = None
    idle_mib: float | None = None
    spill_mib: float | None = None
    notes: str = ""
    probe_outputs: list = field(default_factory=list)
    record_only: bool = False
    seq: int | None = None
    checks: dict = field(default_factory=dict)
    first_token_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.score is not None


class PointRunner:
    """Real implementation of the interface the tuner uses: `run(cfg, label, ...) -> Point`."""

    def __init__(self, backend: Backend, lane: LaneConfig, cand: Candidate, ledger: Ledger,
                 safety: SafetyController, telemetry: Telemetry, server: BenchServer, run_id: str,
                 server_bin: str, models_dir: str, run_dir: str, allow_moe_exception: bool = False,
                 seed: int = 20261013, guard=None) -> None:
        self.b, self.lane, self.cand, self.ledger = backend, lane, cand, ledger
        self.safety, self.telemetry, self.server = safety, telemetry, server
        self.run_id, self.server_bin, self.models_dir, self.run_dir = run_id, server_bin, models_dir, run_dir
        self.allow_moe_exception = allow_moe_exception
        self.seed = seed
        self.jobs = speed.build_speed_set()
        self.prompt_set_hash = speed.speed_set_hash(self.jobs)
        self.guard = guard
        self.background_telemetry = False
        self.n = 0
        self.version: str | None = None

    # -- helpers --
    def fit_estimate(self, cfg: ServerConfig):
        fi = self.cand.fit_input(cfg.kv, cfg.ctx, with_draft=cfg.spec != "off",
                                 with_mmproj=cfg.mmproj and "mmproj" not in (self.cand.get("spec_conflicts") or {}).get(cfg.spec, []))
        return check_fit(fi, self.lane.fit_lane, allow_moe_offload=self.allow_moe_exception)

    def mode_extra(self, cfg: ServerConfig) -> dict:
        mode = self.cand["reasoning_modes"][cfg.reasoning or self.cand["production_reasoning"]]
        extra = dict(self.cand.get("extra_body") or {})
        ctk = dict(mode.get("chat_template_kwargs") or {})
        if ctk:
            merged = dict(extra.get("chat_template_kwargs") or {})
            merged.update(ctk)
            extra["chat_template_kwargs"] = merged
        return extra

    def _record(self, point: Point, phase: str, cfg: ServerConfig, argv: list[str], flag_changed, extra: dict) -> Point:
        rec = self.ledger.append(
            phase, run_id=self.run_id, machine=self.lane.name, model={"id": self.cand.id, "file": self.cand.get("file")},
            build={"version": self.version}, args=argv, flag_changed=flag_changed, config_id=cfg.key(),
            label=point.label, prompt_set_hash=self.prompt_set_hash, seed=self.seed, status=point.status,
            speed={"score": point.score, "pp_tps": point.pp_tps, "tg_tps": point.tg_tps, "ttft_ms": point.ttft_ms,
                   "shapes": point.shapes},
            mem={"idle_mib": point.idle_mib, "peak_mib": point.peak_mib, "spill_mib": point.spill_mib},
            load={"cold_s": point.cold_s}, record_only=point.record_only, cfg=cfg.as_dict(), notes=point.notes, **extra)
        point.seq = rec["seq"]
        return point

    # -- the one public method --
    def run(self, cfg: ServerConfig, label: str, phase: str = "tune", passes: int = 3, probe: bool = False,
            record_only: bool = False, flag_changed: dict | None = None, guard_result=None,
            during=None, skip_speed: bool = False) -> Point:
        """`during(url, mode_extra) -> dict` runs while the server is up (smoke checks, ladder)."""
        self.n += 1
        self.safety.check()
        self.telemetry.wait_until_cool()
        argv = build_argv(self.lane, self.cand, cfg, self.server_bin, self.models_dir)
        point = Point(label, "ok", cfg.as_dict(), record_only=record_only)
        extra = {"guard": guard_result.__dict__ if guard_result else None}

        fit = self.fit_estimate(cfg)
        if not fit.fits and not record_only:
            point.status, point.notes = "fit_reject", fit.reason
            return self._record(point, phase, cfg, argv, flag_changed, extra)
        idle = self.telemetry.idle_vram_mib()
        self.telemetry.reset()
        log_path = f"{self.run_dir}/{self.cand.id}-{self.n:03d}.log"
        lr = self.server.launch(argv, log_path, on_poll=self.safety.check)
        self.version = lr.version or self.version
        point.cold_s = lr.cold_s
        point.idle_mib = idle
        if lr.status != "ok":
            point.status, point.notes = lr.status, " | ".join(lr.log_tail[-6:])
            self.safety.note_crash(True)
            self.ledger.append("event", kind="launch_failed", run_id=self.run_id, config_id=cfg.key(),
                               status=lr.status, tail=lr.log_tail[-40:])
            self.server.stop(idle_baseline_mib=idle)
            self.safety.check()
            return self._record(point, phase, cfg, argv, flag_changed, extra)
        self.safety.note_crash(False)
        try:
            s = self.telemetry.observe()
            moe_offload = cfg.n_cpu_moe > 0
            if s is not None and not record_only:
                ok, why = check_loaded(lr.buffers, s.used_mib, s.total_mib, s.shared_mib, self.lane.fit_lane, moe_offload)
                if not ok:
                    point.status, point.notes = "not_on_gpu", why
                    return self._record(point, phase, cfg, argv, flag_changed, extra)
            url = f"{self.lane.base_url}/v1/chat/completions"
            if self.background_telemetry:
                self.telemetry.start_background()
            rng = random.Random(int(hashlib.sha256(f"{self.seed}|{label}".encode()).hexdigest()[:10], 16))
            mode = self.mode_extra(cfg)
            hook = self._abort_hook
            if skip_speed:
                summary = None
            else:
                speed.run_pass(self.b, url, self.cand.id, self.jobs[:3], rng, hook, mode)  # warm-up, discarded
                results = [speed.run_pass(self.b, url, self.cand.id, self.jobs, rng, hook, mode) for _ in range(passes)]
                summary = speed.summarize(results)
            if summary is None:
                pass
            elif not summary["ok"]:
                point.status, point.notes = "unstable", f"request failures: {summary['failures']}"
                self.safety.note_crash(not self.server.alive())
            else:
                point.score, point.pp_tps, point.tg_tps = summary["score"], summary["pp_tps"], summary["tg_tps"]
                point.ttft_ms, point.shapes = summary["ttft_ms"], summary["shape_e2e_s"]
            if during is not None and point.status == "ok":
                point.checks = during(url, mode) or {}
                extra["checks"] = point.checks
            if probe and point.status == "ok":
                point.probe_outputs = self.run_probe(url, mode)
                extra["probe_sha256"] = [hashlib.sha256(o.encode()).hexdigest() for o in point.probe_outputs]
            self.telemetry.observe()
            point.peak_mib, point.spill_mib = self.telemetry.peak_used_mib, self.telemetry.max_shared_mib
            if point.spill_mib and point.spill_mib > 256 and not record_only:
                point.status, point.notes = "not_on_gpu", f"shared GPU memory {point.spill_mib:.0f} MiB (spill)"
        finally:
            try:
                if self.background_telemetry:
                    self.telemetry.stop_background()
            finally:
                self.server.stop(idle_baseline_mib=idle)
        return self._record(point, phase, cfg, argv, flag_changed, extra)

    def _abort_hook(self) -> None:
        self.safety.check()

    def run_probe(self, url: str, mode_extra: dict) -> list[str]:
        outs = []
        for prompt in speed.PROBE_PROMPTS:
            self.safety.check()
            body = speed.request_body(self.cand.id, [{"role": "user", "content": prompt}], 64, extra=mode_extra)
            r = self.b.chat(url, body, timeout=300.0)
            outs.append(r.text if r.ok else f"<error:{r.error}>")
        return outs
