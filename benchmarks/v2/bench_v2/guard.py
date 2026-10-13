"""Numeric quality guard used during tuning (never ranks models).

KV quantization, 3-bit quants and speculative drafting change numerics, so a
value is checked with llama-perplexity (perplexity + KL divergence against the
f16-KV base run) and, for speculative decoding, a byte-identity probe at
temperature 0. Flags that fail are disqualified before Phase 5 ever sees them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .backend import Backend
from .config import Candidate, LaneConfig
from .server import ServerConfig

PPL_PAT = re.compile(r"Final estimate:\s*PPL\s*=\s*([\d.]+)")
KLD_PAT = re.compile(r"Mean\s+KLD:\s*([\d.eE+-]+)")
PPL_REL_CEILING = 0.02
KL_FLOOR = 0.002  # never demand tighter than this even if the reference gap is ~0


@dataclass
class GuardResult:
    ok: bool
    reason: str
    ppl: float | None = None
    kl: float | None = None
    ceiling_kl: float | None = None
    checked: bool = True


def parse_perplexity(stdout: str) -> tuple[float | None, float | None]:
    p, k = PPL_PAT.search(stdout), KLD_PAT.search(stdout)
    return (float(p[1]) if p else None), (float(k[1]) if k else None)


def spec_identical(a: list[str], b: list[str], max_mismatch_frac: float = 0.0) -> tuple[bool, int]:
    """Outputs of the same temperature-0 probes with and without speculation."""
    if len(a) != len(b) or not a:
        return False, len(a)
    diff = sum(1 for x, y in zip(a, b) if hashlib.sha256(x.encode()).digest() != hashlib.sha256(y.encode()).digest())
    return diff / len(a) <= max_mismatch_frac, diff


class NumericGuard:
    def __init__(self, backend: Backend, lane: LaneConfig, cand: Candidate, perplexity_bin: str | None,
                 model_path: str, slice_path: str, work_dir: str, ctx: int = 4096, enabled: bool = True,
                 allow_unguarded: bool = False, server_running=lambda: False) -> None:
        self.b, self.lane, self.cand = backend, lane, cand
        self.bin, self.model, self.slice, self.work = perplexity_bin, model_path, slice_path, work_dir
        self.ctx = ctx
        self.enabled = enabled and bool(perplexity_bin)
        self.allow_unguarded = allow_unguarded
        self._server_running = server_running
        self.base_ppl: float | None = None
        self.reference_kl: float | None = None   # f16 vs q8_0 gap, set from the lane's incumbent
        self._base_file = f"{work_dir}/{cand.id}.base.kld"

    def _argv(self, cfg: ServerConfig, kv: str, mode: str) -> list[str]:
        ngl = "999" if self.lane.raw["gpu_kind"] != "nvidia" else "99"
        argv = [self.bin, "-m", self.model, "-f", self.slice, "-c", str(self.ctx), "-ngl", ngl,
                "--flash-attn", "on" if cfg.fa else "off", "-ctk", kv, "-ctv", kv]
        if cfg.n_cpu_moe:
            argv += ["--n-cpu-moe", str(cfg.n_cpu_moe)]
        if mode == "base":
            argv += ["--kl-divergence-base", self._base_file]
        else:
            argv += ["--kl-divergence", "--kl-divergence-base", self._base_file]
        return argv

    def _run(self, argv: list[str]) -> tuple[float | None, float | None, str]:
        if self._server_running():
            raise RuntimeError("guard needs the GPU: stop the bench server first (one model per machine)")
        r = self.b.run(argv, timeout=3600)
        ppl, kl = parse_perplexity(r.stdout)
        return ppl, kl, (r.stderr or "")[-200:] if r.rc != 0 else ""

    def ensure_base(self, cfg: ServerConfig) -> GuardResult:
        if self.base_ppl is not None:
            return GuardResult(True, "base ready", self.base_ppl, 0.0)
        ppl, _kl, err = self._run(self._argv(cfg, "f16", "base"))
        if ppl is None:
            return GuardResult(False, f"base perplexity run failed {err}", checked=False)
        self.base_ppl = ppl
        return GuardResult(True, "base ready", ppl, 0.0)

    def measure_reference_gap(self, cfg: ServerConfig) -> GuardResult:
        """f16-vs-q8_0 KL on the lane's incumbent; later ceilings are 2x this."""
        if not self.enabled:
            return GuardResult(False, "guard unavailable (no llama-perplexity)", checked=False)
        b = self.ensure_base(cfg)
        if not b.ok:
            return b
        ppl, kl, err = self._run(self._argv(cfg, "q8_0", "test"))
        if kl is None:
            return GuardResult(False, f"reference run failed {err}", checked=False)
        self.reference_kl = kl
        return GuardResult(True, "reference gap set", ppl, kl)

    def check(self, cfg: ServerConfig) -> GuardResult:
        if cfg.kv == "f16" and not cfg.n_cpu_moe:
            return GuardResult(True, "f16 KV is the base itself", self.base_ppl, 0.0, checked=False)
        if not self.enabled:
            if self.allow_unguarded:
                return GuardResult(True, "guard unavailable; allowed unguarded", checked=False)
            return GuardResult(False, "guard unavailable (no llama-perplexity); numerics-changing value disqualified",
                               checked=False)
        b = self.ensure_base(cfg)
        if not b.ok:
            return b
        ppl, kl, err = self._run(self._argv(cfg, cfg.kv, "test"))
        if ppl is None or kl is None:
            return GuardResult(False, f"perplexity run failed {err}", ppl, kl, checked=False)
        ceiling = max(2.0 * (self.reference_kl or 0.0), KL_FLOOR)
        if kl > ceiling:
            return GuardResult(False, f"KL {kl:.5f} above ceiling {ceiling:.5f}", ppl, kl, ceiling)
        if self.base_ppl and (ppl - self.base_ppl) / self.base_ppl > PPL_REL_CEILING:
            return GuardResult(False, f"perplexity {ppl:.3f} is >2% over base {self.base_ppl:.3f}", ppl, kl, ceiling)
        return GuardResult(True, "within ceilings", ppl, kl, ceiling)
