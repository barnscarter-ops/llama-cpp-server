"""One machine's lane: wiring plus the stages (plan, preflight, smoke, tune, confirm, ladder, quality, swap).

A lane holds exactly one model server at a time. Every stage that touches the
machine runs inside `SafetyController.session()`, which stops the production
service first and always restores it.
"""

from __future__ import annotations

import dataclasses
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import ladder, smoke
from .backend import Backend, GuardedBackend
from .config import Candidate, LaneConfig, load_candidates, load_lanes
from .errors import AbortRun, BenchError, WindowError
from .fitcheck import MIN_CTX, check_fit
from .guard import NumericGuard
from .hermes_adapter import HermesBenchAdapter
from .ledger import Ledger
from .points import PointRunner
from .quality import ItemResult, QualityStage, role_verdict, summarize, toggle_effect
from .safety import SafetyController
from .server import BenchServer, ServerConfig
from .suites import builder, scoring
from .telemetry import Telemetry
from .tuning import LaneState, Tuner, TuneSettings
from .window import RunMarkers, Window, load_window

HERE = Path(__file__).resolve().parent.parent
SMOKE_SLOWER_LIMIT = 0.10


@dataclass
class LaneSettings:
    lane: str
    window_path: str | None = None
    ledger_dir: str = str(HERE / "ledger")
    runs_dir: str = str(HERE / "runs")
    server_bin: str | None = None
    bench_bin: str | None = None
    perplexity_bin: str | None = None
    models_dir: str | None = None
    lanes_path: str | None = None
    candidates_path: str | None = None
    tool_schemas_path: str = str(HERE / "data" / "tool_schemas.frozen.json")
    frozen_dir: str | None = None
    external: dict = field(default_factory=dict)       # {"maverick": jsonl, "humaneval": jsonl}
    only: list = field(default_factory=list)           # restrict to candidate ids
    run_id: str | None = None
    dry_run: bool = False
    skip_hash: bool = True
    allow_unguarded: bool = False
    tune: TuneSettings = field(default_factory=TuneSettings)
    judge: object = None
    sandbox_cmd: list = field(default_factory=list)   # e.g. a throwaway container prefix for model-generated code


class Lane:
    def __init__(self, settings: LaneSettings, backend: Backend) -> None:
        self.s = settings
        self.lanes = load_lanes(settings.lanes_path)
        self.roles, self.all_cands = load_candidates(settings.candidates_path, self.lanes)
        if settings.lane not in self.lanes:
            raise BenchError(f"unknown lane {settings.lane!r}")
        self.lane: LaneConfig = self.lanes[settings.lane]
        self.raw_backend = backend
        self.backend = GuardedBackend(backend, {self.lane.port})
        self.run_id = settings.run_id or time.strftime("%Y%m%d-%H%M%S", time.gmtime(backend.now())) + f"-{settings.lane}"
        self.ledger = Ledger(Path(settings.ledger_dir) / f"{settings.lane}.jsonl", clock=backend.now)
        self.run_dir = str(Path(settings.runs_dir) / self.run_id)
        self.markers = RunMarkers(settings.runs_dir, settings.lane)
        self.telemetry = Telemetry(self.backend, self.lane.raw.get("temp_start_c", 65), self.lane.raw.get("temp_pause_c", 85),
                                   self.lane.raw.get("temp_resume_c", 70), self.lane.raw.get("temp_abort_c", 90),
                                   require_shared=bool(self.lane.raw.get("require_shared_reading")))
        self.window: Window | None = None
        self.safety: SafetyController | None = None
        self.snap = self._load_snapshot()

    # -- paths --
    def _load_snapshot(self):
        import json
        p = Path(self.s.tool_schemas_path)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @property
    def server_bin(self) -> str | None:
        return self.lane.path("server_bin", self.s.server_bin, "BENCH_SERVER_BIN")

    @property
    def perplexity_bin(self) -> str | None:
        return self.lane.path("perplexity_bin", self.s.perplexity_bin, "BENCH_PERPLEXITY_BIN")

    @property
    def models_dir(self) -> str | None:
        return self.lane.path("models_dir", self.s.models_dir, "BENCH_MODELS_DIR")

    def model_path(self, cand: Candidate) -> str:
        sep = "\\" if "\\" in (self.models_dir or "") and "/" not in (self.models_dir or "") else "/"
        return f"{(self.models_dir or '').rstrip('/' + chr(92))}{sep}{cand['file']}"

    # -- candidates --
    def candidates(self, include_exception: bool = False) -> list[Candidate]:
        w = self.window
        out = []
        for c in self.all_cands:
            if c.lane != self.lane.name:
                continue
            if self.s.only and c.id not in self.s.only:
                continue
            if c.get("optional") and not (w and w.allows("alternates")):
                continue
            if c.get("exception_only") and not include_exception:
                continue
            if c["role"] == "slow_reasoner" and not (w and w.allows("qwen_load")) and self.lane.name == "r9700":
                continue
            out.append(c)
        order = {"worker_bee": 0, "fast_executor": 1, "slow_reasoner": 2}
        out.sort(key=lambda c: (order[c.role], bool(c.get("control")), not c.get("incumbent")))
        return out

    def present(self, cands: list[Candidate]) -> list[Candidate]:
        out = []
        for c in cands:
            if not c.get("file") or not self.backend.file_exists(self.model_path(c)):
                self.ledger.event("skipped_not_on_disk", model=c.id, file=c.get("file"))
                continue
            out.append(c)
        return out

    # -- wiring --
    def load_window(self) -> Window:
        self.window = load_window(self.s.window_path, self.lane.name, now=self.backend.now())
        return self.window

    def make_safety(self, use_mutex: bool = True) -> SafetyController:
        self.safety = SafetyController(self.backend, self.ledger, self.lane, self.window, self.markers, self.telemetry,
                                       guarded=self.backend, use_mutex=use_mutex)
        return self.safety

    def runner_for(self, cand: Candidate, safety: SafetyController, state_guard=True) -> PointRunner:
        server = BenchServer(self.backend, self.lane)
        Path(self.run_dir).mkdir(parents=True, exist_ok=True)
        guard = None
        if state_guard:
            guard = NumericGuard(self.backend, self.lane, cand, self.perplexity_bin, self.model_path(cand),
                                 str(HERE / "data" / "guard_slice.txt"), self.run_dir, enabled=bool(self.perplexity_bin),
                                 allow_unguarded=self.s.allow_unguarded, server_running=server.alive)
        pr = PointRunner(self.backend, self.lane, cand, self.ledger, safety, self.telemetry, server, self.run_id,
                         self.server_bin or "llama-server", self.models_dir or ".", self.run_dir,
                         allow_moe_exception=bool(self.window and self.window.allows("moe_exception")) and bool(cand.get("exception_only")),
                         guard=guard)
        pr.background_telemetry = not self.s.dry_run
        return pr

    # -- stage: plan (no machine) --
    def plan(self) -> list[dict]:
        rows = []
        for c in self.all_cands:
            if c.lane != self.lane.name:
                continue
            for kv in ("f16", "q8_0"):
                for draft in ((False, True) if c.get("draft") else (False,)):
                    fi = c.fit_input(kv, MIN_CTX, with_draft=draft)
                    for allow in ((False, True) if c.get("moe") else (False,)):
                        fit = check_fit(fi, self.lane.fit_lane, allow_moe_offload=allow)
                        rows.append({"candidate": c.id, "role": c.role, "kv": kv, "draft": draft, "offload_allowed": allow,
                                     "fits": fit.fits, "total_gib": round(fit.total_gib, 2), "headroom_gib": round(fit.headroom_gib, 2),
                                     "n_cpu_moe": fit.n_cpu_moe, "reason": fit.reason, "status": c.get("status"),
                                     "optional": bool(c.get("optional")), "exception_only": bool(c.get("exception_only"))})
        return rows

    # -- stage: preflight (read-only: no stop, no model load) --
    def preflight(self) -> dict:
        problems, notes = [], []
        try:
            n = self.ledger.verify()
            notes.append(f"ledger verified: {n} records")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"ledger: {exc}")
        if self.markers.restore_pending():
            problems.append("RESTORE_REQUIRED marker present: restore the production service first")
        try:
            builder.load_frozen("worker_bee", Path(self.s.frozen_dir) if self.s.frozen_dir else None, self.snap, self.s.external)
            notes.append("frozen suites match their hashes")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"frozen suites: {exc}")
        for label, path in (("server_bin", self.server_bin), ("perplexity_bin", self.perplexity_bin)):
            if not path:
                (notes if label == "perplexity_bin" else problems).append(
                    f"{label} not set" + ("; numerics-changing values will be disqualified unless --allow-unguarded" if label == "perplexity_bin" else ""))
            elif not self.backend.file_exists(path):
                problems.append(f"{label} not found: {path}")
        if self.server_bin and self.backend.file_exists(self.server_bin):
            r = self.backend.run([self.server_bin, "--version"], timeout=30)
            notes.append("server version: " + (r.stdout + r.stderr).strip().splitlines()[-1][:80] if (r.stdout + r.stderr).strip() else "server version unknown")
        if self.perplexity_bin and self.backend.file_exists(self.perplexity_bin):
            h = self.backend.run([self.perplexity_bin, "--help"], timeout=30)
            if "kl-divergence-base" not in (h.stdout + h.stderr):
                problems.append("llama-perplexity has no --kl-divergence-base: the numeric guard cannot run")
        for c in self.candidates(include_exception=True):
            if not c.get("file"):
                notes.append(f"{c.id}: no file named yet (optional)")
            elif not self.backend.file_exists(self.model_path(c)):
                notes.append(f"{c.id}: NOT on disk ({c['status']}); it will be skipped")
        pa = self.lane.raw["power_alarm"]
        if pa.get("command"):
            ok, why = self.ups_probe()
            (notes if ok else problems).append(f"UPS monitor: {why}")
        else:
            notes.append("UPS monitor: not configured (power_monitor will be false)")
        if self.lane.raw.get("require_shared_reading"):
            gs = self.backend.gpu_sample()
            if gs is None or gs.shared_mib is None:
                problems.append("shared GPU memory is not readable (shared_mem_cmd); the no-spill rule would be unenforceable. "
                                "Also set the NVIDIA driver to 'Prefer No Sysmem Fallback'")
            else:
                notes.append(f"shared GPU memory readable ({gs.shared_mib:.0f} MiB now)")
        free = self.backend.free_disk_gb(self.s.runs_dir if Path(self.s.runs_dir).exists() else ".")
        if free < 20:
            problems.append(f"only {free:.0f} GB free")
        return {"ok": not problems, "problems": problems, "notes": notes}

    def ups_probe(self) -> tuple[bool, str]:
        """A dead monitor must not look like 'no alarm': the query has to return a status line now."""
        pa = self.lane.raw["power_alarm"]
        r = self.backend.run(pa["command"], timeout=15)
        if r.rc != 0 or not r.stdout.strip():
            return False, f"query failed (rc={r.rc}): {(r.stderr or 'no output').strip()[:120]}"
        return True, f"status '{r.stdout.strip()[:60]}'"

    def _power_monitor_working(self) -> bool:
        return bool(self.lane.raw["power_alarm"]["command"]) and self.ups_probe()[0]

    def write_header(self, hash_models: bool = False) -> None:
        models = {}
        for c in self.candidates(include_exception=True):
            if c.get("file") and self.backend.file_exists(self.model_path(c)):
                models[c.id] = {"file": c["file"], "sha256": self.backend.sha256(self.model_path(c)) if hash_models else None}
        s = self.backend.gpu_sample()
        self.ledger.append("header", run_id=self.run_id, machine=self.lane.name,
                           hw={"gpu": self.lane.raw["gpu"], "vram_total_mib": s.total_mib if s else None,
                               "temp_start_c": s.temp_c if s else None, "power_w": s.power_w if s else None},
                           power_monitor=self._power_monitor_working(), models=models,
                           frozen=getattr(self, "_frozen_hashes", None))

    # -- running stages inside the safety session --
    def run_stages(self, stages: list[str]) -> dict:
        if not self.s.dry_run:
            pre = self.preflight()
            if not pre["ok"]:
                raise AbortRun("preflight failed: " + "; ".join(pre["problems"]), "preflight")
        self.load_window()
        report: dict = {}
        safety = self.make_safety()
        try:
            with safety.session():
                self.write_header(hash_models=not self.s.skip_hash)
                for st in stages:
                    safety.check()
                    report[st] = getattr(self, f"stage_{st}")(safety)
        except AbortRun as exc:
            self.ledger.event("abort", lane=self.lane.name, code=exc.code, reason=exc.reason)
            report["aborted"] = {"code": exc.code, "reason": exc.reason}
        return report

    # -- smoke --
    def stage_smoke(self, safety: SafetyController) -> dict:
        cands = self.present(self.candidates())
        tool_items = []
        if self.snap:
            from .toolschemas import gen_tool_items
            tool_items = gen_tool_items(40, self.snap)
        results = {}
        for c in cands:
            runner = self.runner_for(c, safety, state_guard=False)
            tuner = Tuner(runner, c, self.lane, LaneState.load(self.ledger, self.lane.name), self.ledger, settings=self.s.tune)
            cfg, why = tuner.derive_base()
            if cfg is None:
                self.ledger.event("smoke_verdict", model=c.id, dropped=True, reason=f"does not fit: {why}")
                results[c.id] = {"dropped": True, "reason": why}
                continue
            est = runner.fit_estimate(cfg).total_gib
            checks_fn = smoke.make_checks(self.backend, c.id, tool_items, est, self.telemetry)
            p = runner.run(cfg, "smoke", phase="smoke", passes=1, during=checks_fn)
            ok, why = smoke.passes(p.checks) if p.status == "ok" else (False, f"{p.status}: {p.notes[:100]}")
            results[c.id] = {"point": p, "ok": ok, "why": why, "role": c.role}
        for role in self.roles:
            rs = {cid: r for cid, r in results.items() if r.get("role") == role and r.get("point") and r["point"].ok}
            inc = next((cid for cid, r in rs.items() if self.cand(cid).get("incumbent")), None)
            ref = rs[inc]["point"].score if inc else None
            for cid, r in results.items():
                if r.get("role") != role or "point" not in r:
                    continue
                c = self.cand(cid)
                dropped, reason = False, "ok"
                if not r["ok"]:
                    dropped, reason = True, r["why"]
                elif ref and r["point"].score > ref * (1 + SMOKE_SLOWER_LIMIT) and not c.get("incumbent") and not c.get("new_capability"):
                    dropped, reason = True, f"more than {int(SMOKE_SLOWER_LIMIT * 100)}% slower than the incumbent with no new capability"
                self.ledger.event("smoke_verdict", model=cid, dropped=dropped, reason=reason)
                r["dropped"], r["reason"] = dropped, reason
        return {cid: {k: v for k, v in r.items() if k != "point"} | ({"score": r["point"].score} if "point" in r else {})
                for cid, r in results.items()}

    def cand(self, cid: str) -> Candidate:
        return next(c for c in self.all_cands if c.id == cid)

    def _alive(self, cands: list[Candidate]) -> list[Candidate]:
        dropped = {r["model"] for r in self.ledger.records() if r.get("phase") == "event" and r.get("kind") == "smoke_verdict" and r.get("dropped")}
        kept_later = {r["model"] for r in self.ledger.records() if r.get("phase") == "event" and r.get("kind") == "smoke_verdict" and not r.get("dropped")}
        return [c for c in cands if c.id not in (dropped - kept_later)]

    def _guard_state(self, runner: PointRunner, state: LaneState) -> None:
        if runner.guard is not None:
            runner.guard.reference_kl = state.reference_kl

    # -- tune (S2) --
    def stage_tune(self, safety: SafetyController) -> dict:
        out = {}
        for c in self._alive(self.present(self.candidates(include_exception=self._exception_triggered()))):
            state = LaneState.load(self.ledger, self.lane.name)
            runner = self.runner_for(c, safety)
            tuner = Tuner(runner, c, self.lane, state, self.ledger, guard=runner.guard, settings=self.s.tune)
            res = tuner.run_levers()
            out[c.id] = {"status": res.status, "reason": res.reason, "decisions": [dataclasses.asdict(d) for d in res.decisions]}
            if res.status != "ok":
                self.ledger.event("tune_disqualified", model=c.id, reason=res.reason)
        return out

    # -- confirm (S3) --
    def stage_confirm(self, safety: SafetyController) -> dict:
        out = {}
        for c in self._alive(self.present(self.candidates(include_exception=self._exception_triggered()))):
            levers = self._last_event("levers_done", c.id)
            if levers is None:
                continue
            tuned = self._last_event("tuned", c.id)
            if tuned is not None and tuned["seq"] > levers["seq"]:
                continue  # confirmed after the latest sweep; a newer sweep (rerun after drift) is confirmed again
            state = LaneState.load(self.ledger, self.lane.name)
            runner = self.runner_for(c, safety)
            tuner = Tuner(runner, c, self.lane, state, self.ledger, guard=runner.guard, settings=self.s.tune)
            res = tuner.confirm()
            out[c.id] = {"status": res.status, "drift": res.drift, "needs_rerun": res.needs_rerun,
                         "decisions": [dataclasses.asdict(d) for d in res.decisions]}
        return out

    def _last_event(self, kind: str, cid: str) -> dict | None:
        last = None
        for r in self.ledger.records():
            if r.get("phase") == "event" and r.get("kind") == kind and r.get("model") == cid:
                last = r
        return last

    def tuned_cfg(self, cid: str) -> ServerConfig | None:
        """Latest confirmed config, and only if no newer sweep has been run since (stale results never feed ladder/quality)."""
        last = self._last_event("tuned", cid)
        levers = self._last_event("levers_done", cid)
        if not last or not last.get("final_ok") or (levers and levers["seq"] > last["seq"]):
            return None
        return ServerConfig(**last["cfg"])

    def _exception_triggered(self) -> bool:
        """Workbench exception path: window allows it AND the IQ3 incumbent missed the worker-bee criteria."""
        if not (self.window and self.window.allows("moe_exception")):
            return False
        for r in self.ledger.records():
            if r.get("phase") == "event" and r.get("kind") == "role_verdict" and r.get("model") == "qwen36-35b-a3b-iq3":
                return r["verdict"].get("pass") is False
        return False

    # -- ladder (S4) --
    def stage_ladder(self, safety: SafetyController) -> dict:
        out = {}
        for c in self.present(self.candidates(include_exception=self._exception_triggered())):
            cfg = self.tuned_cfg(c.id)
            if cfg is None:
                continue
            runner = self.runner_for(c, safety, state_guard=False)
            during = ladder.ladder_checks(self.backend, c.id, cfg.ctx, safety.check)
            p = runner.run(cfg, "ladder-128k", phase="ladder", passes=0, skip_speed=True, during=during)
            checks = p.checks if p.status == "ok" else {}
            safe = ladder.max_safe_ctx(checks, cfg.ctx) if checks else 0
            loss = ladder.decode_loss(checks, cfg.ctx) if checks else None
            cold = ladder.cold_loads(runner, cfg, 3, self.lane.raw.get("cold_cache_cmd"), self.backend)
            self.ledger.event("ladder_done", model=c.id, max_safe_ctx=safe, decode_loss=loss, peak_mib=p.peak_mib,
                              spill_mib=p.spill_mib, wake_seconds=cold.wake_seconds(), cold_s=cold.cold_s,
                              first_token_s=cold.first_token_s, cold_failures=cold.failures,
                              fails_128k=safe == 0 or (loss is not None and loss > 0.25))
            out[c.id] = {"max_safe_ctx": safe, "decode_loss": loss, "wake_seconds": cold.wake_seconds()}
        return out

    # -- quality (S5) --
    def stage_quality(self, safety: SafetyController) -> dict:
        out = {}
        hermes = HermesBenchAdapter(self.backend, self._hermes_cfg())
        for c in self.present(self.candidates(include_exception=self._exception_triggered())):
            cfg = self.tuned_cfg(c.id)
            if cfg is None:
                continue
            if any(r.get("kind") == "ladder_done" and r.get("model") == c.id and r.get("fails_128k")
                   for r in self.ledger.records() if r.get("phase") == "event"):
                self.ledger.event("role_verdict", model=c.id, role=c.role, verdict={"pass": False, "tier": None, "detail": "failed the 128k ladder check"})
                continue
            items = builder.load_frozen(c.role, Path(self.s.frozen_dir) if self.s.frozen_dir else None, self.snap, self.s.external)
            runner = self.runner_for(c, safety, state_guard=False)
            extra = None
            if hermes.configured:
                def extra(url, _c=c):  # noqa: E306
                    res = hermes.run(_c.id, self.lane.base_url, self.run_id)
                    return [ItemResult(f"hermes-{k}", "hermes", v, "bench task", 0, 0.0, "stop") for k, v in res["tasks"].items()]
            defer, why = self.code_deferred()
            if defer:
                self.ledger.event("code_deferred", model=c.id, reason=why)
            stage = QualityStage(self.backend, self.ledger, c, c.role, items, self.run_id, safety.check, self.s.judge, extra_runner=extra,
                                 defer_code=defer, raw_dir=str(Path(self.run_dir) / "raw"))
            prod = c["production_reasoning"]
            p = runner.run(dataclasses.replace(cfg, reasoning=prod), "quality-production", phase="quality", passes=0,
                           skip_speed=True, during=stage.production_pass(prod))
            summary = (p.checks or {}).get("production")
            other = next((m for m in c["reasoning_modes"] if m != prod), None)
            toggle = None
            if p.status == "ok" and other:
                p2 = runner.run(dataclasses.replace(cfg, reasoning=other), f"quality-{other}-subset", phase="quality", passes=0,
                                skip_speed=True, during=stage.other_mode_pass(other))
                if summary and p2.checks.get("subset"):
                    from .quality import subset
                    ids = {i["id"] for i in subset(items)}
                    prod_sub = summarize([r for r in stage.results.get(f"production-{prod}", []) if r.item_id in ids])
                    toggle = toggle_effect(p2.checks["subset"] if other == "on" else prod_sub,
                                           prod_sub if other == "on" else p2.checks["subset"])
                    self.ledger.event("reasoning_toggle", model=c.id, production=prod, other=other, effect=toggle)
            if summary:
                anchor = self._anchor()
                verdict = role_verdict(c.role, self.roles[c.role]["criteria"], summary, anchor)
                self.ledger.event("role_verdict", model=c.id, role=c.role, verdict=verdict, overall=summary.get("overall"))
                out[c.id] = {"verdict": verdict, "overall": summary.get("overall")}
            else:
                out[c.id] = {"error": p.status, "notes": p.notes}
        return out

    def code_deferred(self) -> tuple[bool, str]:
        """True when model-generated code must not run here (it is scored later by the score step)."""
        if not self.lane.raw.get("execute_model_code"):
            return True, "this lane never executes model-generated code (score step runs it on Linux as an unprivileged user)"
        scoring.configure_runner(drop_user=self.lane.raw.get("code_run_as", "nobody"), sandbox_cmd=self.s.sandbox_cmd or None)
        ok, why = scoring.can_execute()
        return (not ok), why

    def _anchor(self) -> dict | None:
        """Newest scored DeepSeek anchor summary from any ledger in the directory (the score step writes its own)."""
        from .ledger import read_all
        last = None
        for r in read_all(self.s.ledger_dir):
            if r.get("phase") == "event" and r.get("kind") == "anchor_summary" and not r.get("deferred"):
                last = r
        return last["summary"] if last else None

    def _hermes_cfg(self):
        import json
        p = Path(self.s.lanes_path) if self.s.lanes_path else HERE / "lanes.json"
        return json.loads(p.read_text(encoding="utf-8")).get("hermes_bench")

    # -- swap timing (R9700): Nemotron -> Qwen -> Nemotron through the real scripts --
    def _swap_ready(self, safety: SafetyController, which: str, since: float):
        """Ready when the service is listening again since `since` and the process list shows the wanted model.
        Looks at the service and its process only; nothing here sends a request to Guardian's port."""
        name = safety.cfg["services"][0]["name"]

        def probe() -> bool:
            return safety.service_ready(name, since)[0] and safety.identity_is(which)[0]
        return probe

    def swap_test(self) -> dict:
        self.load_window()
        self.window.require("qwen_load")
        self.window.require("swap_test")
        sw = self.lane.raw.get("swap")
        if not sw or not sw.get("identity_cmd"):
            raise WindowError(f"lane {self.lane.name} has no swap scripts and identity check configured")
        safety = self.make_safety(use_mutex=False)
        to_q, to_n, failed = [], [], None

        def put_back() -> None:
            # Whatever happened above (failed script, abort, signal), end with Nemotron resident and verified.
            if safety.identity_is("nemotron")[0]:
                return
            t = ladder.time_swap(self.backend, sw["to_nemotron"], self._swap_ready(safety, "nemotron", self.backend.now()))
            self.ledger.event("swap_put_back", seconds=t)

        with safety.watch(before_restore=put_back):
            for i in range(int(sw.get("round_trips", 3))):
                safety.check()
                t0 = self.backend.now()
                t = ladder.time_swap(self.backend, sw["to_qwen"], self._swap_ready(safety, "qwen", t0))
                if t is None:
                    failed = f"to_qwen failed or timed out on round trip {i + 1}"
                    break
                to_q.append(t)
                self.ledger.append("swap", run_id=self.run_id, direction="nemotron->qwen", round_trip=i + 1, seconds=t)
                safety.check()
                t0 = self.backend.now()
                t = ladder.time_swap(self.backend, sw["to_nemotron"], self._swap_ready(safety, "nemotron", t0))
                if t is None:
                    failed = f"to_nemotron failed or timed out on round trip {i + 1}"
                    break
                to_n.append(t)
                self.ledger.append("swap", run_id=self.run_id, direction="qwen->nemotron", round_trip=i + 1, seconds=t)
        self.ledger.event("swap_done", to_qwen_s=to_q, to_nemotron_s=to_n, failed=failed)
        return {"to_qwen_s": to_q, "to_nemotron_s": to_n, "failed": failed}

    # -- restore on demand --
    def restore_now(self) -> dict:
        """Recover after a crash: needs the marker, not a window (restoring is never blocked)."""
        if not self.markers.restore_pending():
            return {"restored": False, "detail": "no RESTORE_REQUIRED marker"}
        self.window = self.window or Window(self.lane.name, 0, 4_102_444_800.0, "restore", {"stop_services": True})
        safety = self.make_safety()
        safety.restore()
        return {"restored": True}
