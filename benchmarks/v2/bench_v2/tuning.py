"""The decision tree for one model (protocol 17.2).

Levers: A placement (MoE that does not fit only), B speculative decoding (only
where a draft exists), C KV type, D batch/ubatch. Everything else is derived by
rule. After the levers: leave-one-out confirmation, final re-measure, drift check.
The tree talks to a `runner` with `run(cfg, label, ...) -> Point`, so it is tested
with scripted points and runs for real through `points.PointRunner`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from .config import Candidate, LaneConfig
from .fitcheck import MIN_CTX
from .guard import GuardResult, spec_identical
from .ledger import Ledger
from .server import ServerConfig
from .stats import noise_floor

D_POINTS = [(2048, 512), (2048, 1024), (2048, 2048)]
MAX_COMPONENT_REGRESSION = 0.10
DRIFT_LIMIT = 0.03


@dataclass
class TuneSettings:
    passes: int = 3
    final_passes: int = 5
    baseline_launches: int = 3
    allow_moe_exception: bool = False
    skip_levers: tuple = ()


@dataclass
class LaneState:
    noise_floor: float | None = None
    kv_decision: str | None = None        # "f16" | "q8_0" decided on the lane's first tested model
    flat_levers: list = field(default_factory=list)
    fa_checked: bool = False
    kv_record_done: bool = False
    reference_kl: float | None = None

    @classmethod
    def load(cls, ledger: Ledger, lane: str) -> "LaneState":
        last = None
        for r in ledger.records():
            if r.get("phase") == "event" and r.get("kind") == "lane_state" and r.get("lane") == lane:
                last = r
        if not last:
            return cls()
        return cls(**{k: last["state"][k] for k in last["state"] if k in cls.__dataclass_fields__})

    def save(self, ledger: Ledger, lane: str) -> None:
        ledger.event("lane_state", lane=lane, state=dataclasses.asdict(self))


@dataclass
class Decision:
    lever: str
    value: str
    outcome: str     # adopted | kept_base | rejected | skipped | forced | recorded
    detail: str = ""


@dataclass
class TuneResult:
    status: str                      # ok | disqualified
    reason: str = ""
    cfg: ServerConfig | None = None
    final_point: object = None
    decisions: list = field(default_factory=list)
    points: list = field(default_factory=list)
    drift: float | None = None
    needs_rerun: bool = False


def better_than(cand, base, floor: float) -> bool:
    """Beats base by more than the noise floor with no component more than 10% worse."""
    if not (cand and cand.ok and base and base.ok):
        return False
    if cand.score >= base.score * (1.0 - floor):
        return False
    lim = MAX_COMPONENT_REGRESSION
    return (cand.pp_tps >= base.pp_tps * (1 - lim) and cand.tg_tps >= base.tg_tps * (1 - lim)
            and cand.ttft_ms <= base.ttft_ms * (1 + lim))


def worse_than(cand, base, floor: float) -> bool:
    return bool(cand and cand.ok and base and base.ok and cand.score > base.score * (1.0 + floor))


class Tuner:
    def __init__(self, runner, cand: Candidate, lane: LaneConfig, state: LaneState, ledger: Ledger,
                 guard=None, settings: TuneSettings | None = None) -> None:
        self.r, self.cand, self.lane, self.state, self.ledger = runner, cand, lane, state, ledger
        self.guard = guard
        self.s = settings or TuneSettings()
        self.decisions: list[Decision] = []
        self.points: list = []
        self.floor = state.noise_floor
        self._first_model_levers_tried: set = set()

    # -- helpers --
    def _note(self, lever: str, value: str, outcome: str, detail: str = "") -> None:
        d = Decision(lever, value, outcome, detail)
        self.decisions.append(d)
        self.ledger.event("decision", model=self.cand.id, lever=lever, value=value, outcome=outcome, detail=detail)

    def _run(self, cfg: ServerConfig, label: str, **kw):
        p = self.r.run(cfg, label, **kw)
        self.points.append(p)
        return p

    def _tuned(self, lever: str) -> bool:
        if lever in self.s.skip_levers:
            return False
        if self.cand.get("tuned", True):
            return True
        return lever in (self.cand.get("partial_tune") or [])

    def _fits(self, cfg: ServerConfig):
        return self.r.fit_estimate(cfg)

    def _guard(self, cfg: ServerConfig) -> GuardResult:
        if self.guard is None:
            return GuardResult(True, "no guard configured", checked=False)
        res = self.guard.check(cfg)
        self.ledger.append("guard", model=self.cand.id, config_id=cfg.key(), ok=res.ok, reason=res.reason,
                           ppl=res.ppl, kl=res.kl, ceiling_kl=res.ceiling_kl, checked=res.checked)
        return res

    # -- base config --
    def derive_base(self) -> tuple[ServerConfig | None, str]:
        cand = self.cand
        cfg = ServerConfig(ctx=MIN_CTX, kv="f16", fa=True, b=2048, ub=512, spec="off", np=1)
        policy = cand["kv_policy"]
        f16_fit = self._fits(cfg)
        q8_cfg = dataclasses.replace(cfg, kv="q8_0")
        if policy == "fixed_q8":
            cfg = q8_cfg
            self._note("C", "q8_0", "forced", "kv_policy fixed_q8 (f16 does not fit with everything loaded)")
        elif policy == "forced_q8":
            if not f16_fit.fits:
                cfg = q8_cfg
                self._note("C", "q8_0", "forced", f"f16 fails the fit rule: {f16_fit.reason}")
            else:
                self._note("C", "f16", "kept_base", "f16 fits; q8_0 measured as a confirmation")
        else:  # tested
            if self.state.kv_decision == "q8_0" or not f16_fit.fits:
                cfg = q8_cfg
                self._note("C", "q8_0", "forced" if not f16_fit.fits else "adopted",
                           f16_fit.reason if not f16_fit.fits else "lane decision from its first model")
        fit = self._fits(cfg)
        if not fit.fits:
            return None, fit.reason
        if fit.offloaded:
            cfg = dataclasses.replace(cfg, n_cpu_moe=fit.n_cpu_moe, threads=self.lane.raw.get("physical_cores"))
        return cfg, fit.reason

    # -- the tree --
    def tune(self) -> TuneResult:
        """S2 then S3 in one go (tests and --dry-run). The CLI runs them as separate stages."""
        res = self.run_levers()
        return res if res.status != "ok" else self.confirm()

    def run_levers(self) -> TuneResult:
        """S2: placement, speculative decoding, KV type, batch/ubatch; writes `levers_done` to the ledger."""
        base_cfg, why = self.derive_base()
        if base_cfg is None:
            self._note("fit", "-", "rejected", why)
            return TuneResult("disqualified", why, decisions=self.decisions)

        # The reference KL gap (f16 vs q8_0 on the lane's first model) sets the ceiling for everything after it.
        if self.guard is not None and self.state.reference_kl is None and base_cfg.kv != "f16":
            ref = self.guard.measure_reference_gap(base_cfg)
            if ref.ok:
                self.state.reference_kl = ref.kl
            self._note("guard", "reference", "recorded", f"{ref.reason}; KL {ref.kl}")
        elif self.guard is not None:
            self.guard.reference_kl = self.state.reference_kl

        # Guard the forced/derived KV type before spending any time on it.
        if base_cfg.kv != "f16":
            g = self._guard(base_cfg)
            if not g.ok:
                self._note("C", base_cfg.kv, "rejected", g.reason)
                return TuneResult("disqualified", f"required KV type {base_cfg.kv} fails the numeric guard: {g.reason}",
                                  decisions=self.decisions)

        # Lever A: placement, MoE offload ladder.
        if base_cfg.n_cpu_moe:
            base_cfg = self._lever_a(base_cfg)
            if base_cfg is None:
                return TuneResult("disqualified", "every expert-offload value failed", decisions=self.decisions,
                                  points=self.points)

        has_spec = len(self.cand["spec_options"]) > 1 and self._tuned("B")
        base = self._baseline(base_cfg, probe=has_spec)
        if not base.ok:
            return TuneResult("disqualified", f"baseline did not run: {base.status} {base.notes[:120]}",
                              decisions=self.decisions, points=self.points)
        floor = self.floor if self.floor is not None else noise_floor([base.score])
        was_first = self.state.noise_floor is None

        cfg = base_cfg
        changed: list[str] = []

        # Lever B: speculative decoding.
        if has_spec and "B" not in self.state.flat_levers:
            cfg, was_changed = self._lever_b(cfg, base, floor)
            if was_changed:
                changed.append("B")
        elif "B" in self.state.flat_levers:
            self._note("B", "-", "skipped", "flat on this lane's first model")

        # Lever C: KV type.
        cfg, c_changed = self._lever_c(cfg, base, floor)
        if c_changed:
            changed.append("C")

        # Flash-attention spot-check, once per lane (record only).
        if not self.state.fa_checked and self._tuned("fa"):
            p = self._run(dataclasses.replace(base_cfg, fa=False), "fa-off-spot-check", record_only=True,
                          passes=1, flag_changed={"name": "fa", "value": "off"})
            self.state.fa_checked = True
            self._note("FA", "off", "recorded", f"{p.status}; score {p.score}")

        # Lever D: batch / ubatch.
        if self._tuned("D") and "D" not in self.state.flat_levers:
            cfg, d_changed = self._lever_d(cfg, base, floor)
            if d_changed:
                changed.append("D")
        elif "D" in self.state.flat_levers:
            self._note("D", "-", "skipped", "flat on this lane's first model")

        if self.state.noise_floor is None:
            self.state.noise_floor = floor
        if was_first:  # stop rule: a lever with no effect on the lane's first model is skipped on later ones
            for lever in ("B", "D"):
                if lever in self._first_model_levers_tried and lever not in changed and lever not in self.state.flat_levers:
                    self.state.flat_levers.append(lever)
        self.state.save(self.ledger, self.lane.name)
        self.ledger.event("levers_done", model=self.cand.id, lane=self.lane.name, base_cfg=base_cfg.as_dict(),
                          cfg=cfg.as_dict(), changed=changed, base_score=base.score, floor=floor)
        self._lv = {"base_cfg": base_cfg, "cfg": cfg, "changed": changed, "base_score": base.score, "floor": floor}
        return TuneResult("ok", cfg=cfg, decisions=self.decisions, points=self.points)

    def load_levers(self) -> bool:
        """Resume S3 from the ledger when S2 ran in an earlier invocation."""
        last = None
        for r in self.ledger.records():
            if r.get("phase") == "event" and r.get("kind") == "levers_done" and r.get("model") == self.cand.id:
                last = r
        if not last:
            return False
        self._lv = {"base_cfg": ServerConfig(**last["base_cfg"]), "cfg": ServerConfig(**last["cfg"]),
                    "changed": list(last["changed"]), "base_score": last["base_score"], "floor": last["floor"]}
        return True

    def confirm(self) -> TuneResult:
        """S3: leave-one-out, final re-measure, drift check; writes `tuned` to the ledger."""
        if not hasattr(self, "_lv") and not self.load_levers():
            return TuneResult("disqualified", "no levers_done record: run the tune stage first")
        base_cfg, cfg, changed = self._lv["base_cfg"], self._lv["cfg"], self._lv["changed"]
        floor, base_score = self._lv["floor"], self._lv["base_score"]
        cfg, changed = self._leave_one_out(cfg, changed, base_cfg, floor)
        final = self._run(cfg, "final", phase="confirm", passes=self.s.final_passes)

        drift_pt = self._run(base_cfg, "baseline-end", phase="confirm", passes=self.s.passes)
        drift, needs_rerun = None, False
        if not drift_pt.ok:  # a confirmation that never re-established the baseline proves nothing about drift
            needs_rerun = True
            self._note("drift", drift_pt.status, "rejected", "end baseline did not complete: rerun this sweep")
        elif base_score:
            drift = abs(drift_pt.score - base_score) / base_score
            if drift > DRIFT_LIMIT:
                needs_rerun = True
                self._note("drift", f"{drift:.3f}", "rejected", "baseline drifted more than 3%: rerun this sweep")
        self.ledger.event("tuned", model=self.cand.id, lane=self.lane.name, cfg=cfg.as_dict(), changed=changed,
                          final_score=final.score, final_pp=final.pp_tps, final_tg=final.tg_tps, drift=drift,
                          needs_rerun=needs_rerun, final_ok=final.ok)
        return TuneResult("ok" if final.ok else "disqualified", "" if final.ok else f"final run {final.status}",
                          cfg=cfg, final_point=final, decisions=self.decisions, points=self.points,
                          drift=drift, needs_rerun=needs_rerun)

    def _baseline(self, cfg: ServerConfig, probe: bool):
        n = self.s.baseline_launches if self.state.noise_floor is None else 1
        pts = [self._run(cfg, f"baseline-{i + 1}", passes=self.s.passes, probe=probe and i == 0) for i in range(n)]
        good = [p for p in pts if p.ok]
        if self.state.noise_floor is None and len(good) >= 2:
            self.floor = noise_floor([p.score for p in good])
            self._note("noise", f"{self.floor:.4f}", "recorded", f"from {len(good)} baseline launches")
        if not good:
            return pts[0]
        best = sorted(good, key=lambda p: p.score)[len(good) // 2]  # the median launch
        best.probe_outputs = best.probe_outputs or next((p.probe_outputs for p in good if p.probe_outputs), [])
        return best

    def _lever_a(self, cfg: ServerConfig):
        fit = self._fits(cfg)
        ladder = sorted({min(fit.n_cpu_moe + k, 40) for k in (0, 4, 8)}) if fit.n_cpu_moe else []
        best_pt, best_cfg, worse_streak = None, None, 0
        for n in ladder:
            c = dataclasses.replace(cfg, n_cpu_moe=n)
            p = self._run(c, f"placement-moe{n}", passes=self.s.passes, flag_changed={"name": "n_cpu_moe", "value": n})
            if not p.ok:
                self._note("A", str(n), "rejected", f"{p.status}: {p.notes[:80]}")
                continue
            if best_pt is None or better_than(p, best_pt, self.floor or 0.03):
                best_pt, best_cfg, worse_streak = p, c, 0
                self._note("A", str(n), "adopted", f"score {p.score:.3f}")
            else:
                worse_streak += 1
                self._note("A", str(n), "kept_base", f"score {p.score:.3f}")
                if worse_streak >= 2:
                    break
        return best_cfg

    def _lever_b(self, cfg: ServerConfig, base, floor: float):
        self._first_model_levers_tried = {*self._first_model_levers_tried, "B"}
        for name in self.cand["spec_options"]:
            if name == "off":
                continue
            trial = dataclasses.replace(cfg, spec=name)
            fit = self._fits(trial)
            if not fit.fits:
                self._note("B", name, "rejected", f"does not fit: {fit.reason}")
                continue
            p = self._run(trial, f"spec-{name}", passes=self.s.passes, probe=True,
                          flag_changed={"name": "spec", "value": name})
            if not p.ok:
                self._note("B", name, "rejected", f"{p.status}: {p.notes[:80]}")
                continue
            same, ndiff = spec_identical(base.probe_outputs, p.probe_outputs)
            if not same:
                self._note("B", name, "rejected", f"output differs from no-speculation on {ndiff} probes at temperature 0")
                continue
            if better_than(p, base, floor):
                self._note("B", name, "adopted", f"score {p.score:.3f} vs {base.score:.3f}")
                return trial, True
            self._note("B", name, "kept_base", f"score {p.score:.3f} vs {base.score:.3f} (inside floor or worse)")
        return cfg, False

    def _lever_c(self, cfg: ServerConfig, base, floor: float):
        policy = self.cand["kv_policy"]
        if policy == "fixed_q8":
            return cfg, False
        if policy == "forced_q8":
            if not self.state.kv_record_done and cfg.kv == "q8_0":
                rec = self._run(dataclasses.replace(cfg, kv="f16"), "kv-f16-for-the-record", record_only=True,
                                passes=self.s.passes, flag_changed={"name": "kv", "value": "f16"})
                self.state.kv_record_done = True
                self._note("C", "f16", "recorded", f"{rec.status}; score {rec.score} vs q8_0 {base.score:.3f} "
                           "(not eligible: fails the headroom rule)")
            return cfg, False
        # tested: first model of the lane decides for the lane
        if self.state.kv_decision is not None or not self._tuned("C"):
            return cfg, False
        self._first_model_levers_tried = {*self._first_model_levers_tried, "C"}
        other = "q8_0" if cfg.kv == "f16" else "f16"
        trial = dataclasses.replace(cfg, kv=other)
        fit = self._fits(trial)
        if not fit.fits:
            self.state.kv_decision = cfg.kv
            self._note("C", other, "rejected", f"does not fit: {fit.reason}")
            return cfg, False
        if other == "q8_0":
            g = self._guard(trial)
            if not g.ok:
                self.state.kv_decision = "f16"
                self._note("C", "q8_0", "rejected", g.reason)
                return cfg, False
        p = self._run(trial, f"kv-{other}", passes=self.s.passes, flag_changed={"name": "kv", "value": other})
        if better_than(p, base, floor):
            self.state.kv_decision = other
            self._note("C", other, "adopted", f"score {p.score:.3f} vs {base.score:.3f}")
            return trial, True
        self.state.kv_decision = cfg.kv  # flat or worse: keep f16 for the lane
        self._note("C", other, "kept_base", f"score {p.score if p.ok else p.status} vs {base.score:.3f}")
        return cfg, False

    def _lever_d(self, cfg: ServerConfig, base, floor: float):
        self._first_model_levers_tried = {*self._first_model_levers_tried, "D"}
        best_cfg, best_pt = cfg, base
        for b, ub in D_POINTS:
            if (b, ub) == (cfg.b, cfg.ub):
                continue
            trial = dataclasses.replace(cfg, b=b, ub=min(ub, b))
            p = self._run(trial, f"batch-b{b}-ub{ub}", passes=self.s.passes, flag_changed={"name": "b/ub", "value": f"{b}/{ub}"})
            if better_than(p, best_pt, floor):
                best_cfg, best_pt = trial, p
                self._note("D", f"{b}/{ub}", "adopted", f"score {p.score:.3f}")
            else:
                self._note("D", f"{b}/{ub}", "kept_base", f"score {p.score if p.ok else p.status}")
        return best_cfg, best_cfg is not cfg

    def _leave_one_out(self, cfg: ServerConfig, changed: list[str], base_cfg: ServerConfig, floor: float):
        """Toggle each changed lever back; if the result is not worse than the floor, the lever goes."""
        if not changed:
            return cfg, changed
        full = self._run(cfg, "combined", phase="confirm", passes=self.s.passes)
        if not full.ok:
            return base_cfg, []
        keep = list(changed)
        for lever in list(changed):
            back = self._revert(cfg, base_cfg, lever)
            p = self._run(back, f"loo-without-{lever}", phase="confirm", passes=self.s.passes,
                          flag_changed={"name": f"revert-{lever}", "value": ""})
            if p.ok and not worse_than(p, full, floor):
                keep.remove(lever)
                cfg = back
                full = p  # later levers are judged against the config now in force
                self._note("LOO", lever, "rejected", "reverting it was not worse than the noise floor: dropped")
            else:
                self._note("LOO", lever, "adopted", "reverting it was worse: kept")
        return cfg, keep

    @staticmethod
    def _revert(cfg: ServerConfig, base_cfg: ServerConfig, lever: str) -> ServerConfig:
        if lever == "B":
            return dataclasses.replace(cfg, spec=base_cfg.spec)
        if lever == "C":
            return dataclasses.replace(cfg, kv=base_cfg.kv)
        if lever == "D":
            return dataclasses.replace(cfg, b=base_cfg.b, ub=base_cfg.ub)
        return cfg
