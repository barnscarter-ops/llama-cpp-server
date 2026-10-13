"""Phase 4 on winners: context ladder (8k, 32k, max safe), cold loads, R9700 swap timing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import speed
from .backend import Backend
from .server import ServerConfig
from .stats import median, percentile

LADDER_DEPTHS = (8000, 32000)  # plus "max safe": ctx minus the output room


def depth_prompt(tokens: int, seed: int = 3) -> str:
    return speed._fill("prose", tokens, seed) + "\n\n" + speed.INSTRUCTIONS["prose"]


def ladder_checks(backend: Backend, alias: str, ctx: int, safety_check, mode_extra: dict | None = None):
    """Returns a `during` callback for PointRunner.run: one request per depth."""

    def during(url: str, mode: dict) -> dict:
        out = {}
        for depth in (*LADDER_DEPTHS, ctx - 3000):
            safety_check()
            body = speed.request_body(alias, [{"role": "user", "content": depth_prompt(depth)}], 128,
                                      extra={**(mode_extra or {}), **mode})
            r = backend.chat(url, body, timeout=1800.0)
            key = str(depth)
            if not r.ok:
                out[key] = {"ok": False, "error": r.error}
                continue
            jr = speed.to_job_result(key, depth, r)
            out[key] = {"ok": r.finish_reason != "error", "n_in": r.prompt_tokens, "pp_tps": jr.pp_tps,
                        "tg_tps": jr.tg_tps, "ttft_s": r.ttft_s}
        return out

    return during


def max_safe_ctx(checks: dict, ctx: int) -> int:
    """Largest tested depth that returned; the config ctx counts only if the deepest request passed."""
    deepest = str(ctx - 3000)
    return ctx if checks.get(deepest, {}).get("ok") else 0


def decode_loss(checks: dict, ctx: int) -> float | None:
    """Fractional decode loss at the deepest point vs the 8k point (limit 25%)."""
    a, b = checks.get("8000", {}), checks.get(str(ctx - 3000), {})
    if a.get("ok") and b.get("ok") and a.get("tg_tps"):
        return 1.0 - b["tg_tps"] / a["tg_tps"]
    return None


@dataclass
class ColdLoadResult:
    cold_s: list = field(default_factory=list)
    first_token_s: list = field(default_factory=list)
    failures: int = 0
    cache_drop_failures: int = 0

    def wake_seconds(self) -> float | None:
        """p90 cold load plus the median first-token time, rounded up. Goes into models.json."""
        if not self.cold_s:
            return None
        return float(math.ceil(percentile(self.cold_s, 0.9) + (median(self.first_token_s) if self.first_token_s else 0.0)))


def cold_loads(runner, cfg: ServerConfig, n: int, drop_cache_cmd: list[str] | None, backend: Backend) -> ColdLoadResult:
    """n separate launches; the page cache is dropped first where the lane allows it."""
    res = ColdLoadResult()
    for i in range(n):
        if drop_cache_cmd:
            dropped = backend.run(drop_cache_cmd, timeout=60)
            if dropped.rc != 0:  # a warm page cache would be published as a cold load
                res.failures += 1
                res.cache_drop_failures += 1
                continue

        def first_token(url: str, mode: dict, _alias=runner.cand.id) -> dict:
            body = speed.request_body(_alias, [{"role": "user", "content": "Reply with the single word: ready"}], 8, extra=mode)
            r = backend.chat(url, body, timeout=120.0)
            return {"first_token_s": r.ttft_s if r.ok else None}

        p = runner.run(cfg, f"cold-load-{i + 1}", phase="ladder", passes=0, skip_speed=True, during=first_token)
        if p.status != "ok" or p.cold_s is None:
            res.failures += 1
            continue
        res.cold_s.append(p.cold_s)
        ft = p.checks.get("first_token_s")
        if ft is not None:
            res.first_token_s.append(ft)
    return res


def time_swap(backend: Backend, swap_cmd: list[str], ready_probe, timeout_s: float = 600.0) -> float | None:
    """Seconds from running a swap script until `ready_probe()` is true; None on timeout or script failure."""
    t0 = backend.now()
    r = backend.run(swap_cmd, timeout=timeout_s)
    if r.rc != 0:
        return None
    while backend.now() - t0 < timeout_s:
        if ready_probe():
            return backend.now() - t0
        backend.sleep(2.0)
    return None
