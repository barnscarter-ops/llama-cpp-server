"""VRAM fit check: the hard rules of protocol 17.A.

A config is disqualified unless weights, the full KV cache at >= 131072 tokens,
compute buffers, optional draft/vision files and the headroom all fit in VRAM.
Only an MoE model may put experts on CPU, and only when the all-GPU config does
not fit and the window allows the exception. Dense models never offload.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MIN_CTX = 131072
GIB = 1024.0

# bytes per element relative to f16 (2 B); q8_0 is 34 bytes per 32 elements.
KV_BYTES_PER_ELEMENT = {"f16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}


@dataclass(frozen=True)
class Lane:
    vram_gib: float
    headroom_mib: float
    overhead_gib: float = 0.0
    default_compute_gib: float = 1.0


@dataclass(frozen=True)
class FitInput:
    weights_gib: float
    kv_kib_per_token_f16: float
    kv_type: str = "f16"
    ctx: int = MIN_CTX
    state_gib: float = 0.0      # fixed recurrent state (Gated DeltaNet / Mamba layers)
    compute_gib: float | None = None
    draft_gib: float = 0.0
    mmproj_gib: float = 0.0
    moe: bool = False
    # MoE offload description (only used when moe and the all-GPU config does not fit)
    layers: int = 0
    expert_gib_per_layer: float = 0.0
    nonexpert_gib: float = 0.0


@dataclass
class FitResult:
    fits: bool
    reason: str
    total_gib: float
    headroom_gib: float
    kv_gib: float
    n_cpu_moe: int = 0
    offloaded: bool = False
    parts: dict = field(default_factory=dict)


def kv_gib(fi: FitInput) -> float:
    factor = KV_BYTES_PER_ELEMENT[fi.kv_type] / KV_BYTES_PER_ELEMENT["f16"]
    return fi.kv_kib_per_token_f16 * fi.ctx * factor / (1024.0 * 1024.0)


def _total(fi: FitInput, lane: Lane, weights_on_gpu: float) -> tuple[float, dict]:
    compute = lane.default_compute_gib if fi.compute_gib is None else fi.compute_gib
    parts = {
        "weights": weights_on_gpu, "kv": kv_gib(fi), "state": fi.state_gib, "compute": compute,
        "draft": fi.draft_gib, "mmproj": fi.mmproj_gib, "overhead": lane.overhead_gib,
    }
    return sum(parts.values()), parts


def check_fit(fi: FitInput, lane: Lane, allow_moe_offload: bool = False) -> FitResult:
    if fi.ctx < MIN_CTX:
        return FitResult(False, f"ctx {fi.ctx} below the hard minimum {MIN_CTX}", 0.0, 0.0, 0.0)
    total, parts = _total(fi, lane, fi.weights_gib)
    headroom = lane.vram_gib - total
    need = lane.headroom_mib / 1024.0
    if headroom >= need:
        return FitResult(True, "fits 100% on GPU", total, headroom, parts["kv"], parts=parts)
    if not fi.moe:
        return FitResult(False, f"dense model: {total:.2f} GiB leaves {headroom:.2f} GiB, "
                         f"needs {need:.2f}; offload is not allowed", total, headroom, parts["kv"], parts=parts)
    if not allow_moe_offload:
        return FitResult(False, f"MoE does not fit all-GPU ({headroom:.2f} GiB headroom, needs {need:.2f}) "
                         "and the window does not allow the expert-offload exception",
                         total, headroom, parts["kv"], parts=parts)
    if not (fi.layers and fi.expert_gib_per_layer and fi.nonexpert_gib):
        return FitResult(False, "MoE offload needs layers, expert_gib_per_layer and nonexpert_gib",
                         total, headroom, parts["kv"], parts=parts)
    for cpu_layers in range(1, fi.layers + 1):
        on_gpu = fi.nonexpert_gib + fi.expert_gib_per_layer * (fi.layers - cpu_layers)
        t2, p2 = _total(fi, lane, on_gpu)
        h2 = lane.vram_gib - t2
        if h2 >= need:
            return FitResult(True, f"fits with {cpu_layers} expert layers on CPU (MoE exception)",
                             t2, h2, p2["kv"], n_cpu_moe=cpu_layers, offloaded=True, parts=p2)
    return FitResult(False, "does not fit even with all experts on CPU", total, headroom, parts["kv"], parts=parts)


# llama.cpp prints e.g. "CUDA0 model buffer size = 11734.00 MiB", "CPU_Mapped model buffer size = 166.00 MiB"
_BUF = re.compile(r"(?P<dev>[A-Za-z_]+\d*(?:_[A-Za-z]+)?)\s+(?P<kind>model|KV|compute|RS|output)\s+buffer size\s*=\s*(?P<mib>[\d.]+)\s*MiB")


def parse_buffers(log_lines: list[str]) -> dict:
    """Sum buffer sizes per device class from a llama.cpp load log."""
    out = {"gpu_mib": 0.0, "cpu_mib": 0.0, "cpu_kv_mib": 0.0, "cpu_model_mib": 0.0, "lines": 0}
    for line in log_lines:
        m = _BUF.search(line)
        if not m:
            continue
        out["lines"] += 1
        mib = float(m["mib"])
        dev = m["dev"].upper()
        if dev.startswith("CPU"):
            out["cpu_mib"] += mib
            if m["kind"] == "KV":
                out["cpu_kv_mib"] += mib
            if m["kind"] == "model":
                out["cpu_model_mib"] += mib
        else:
            out["gpu_mib"] += mib
    return out


def check_loaded(buffers: dict, used_mib: float, total_mib: float, shared_mib: float, lane: Lane,
                 moe_offload: bool, cpu_tolerance_mib: float = 512.0, shared_tolerance_mib: float = 256.0) -> tuple[bool, str]:
    """Post-load verification against what the process really allocated."""
    if buffers["cpu_kv_mib"] > 0:
        return False, f"KV cache on CPU ({buffers['cpu_kv_mib']:.0f} MiB)"
    if not moe_offload and buffers["cpu_model_mib"] > cpu_tolerance_mib:
        return False, f"{buffers['cpu_model_mib']:.0f} MiB of weights on CPU (tolerance {cpu_tolerance_mib:.0f})"
    if shared_mib > shared_tolerance_mib:
        return False, f"{shared_mib:.0f} MiB of shared GPU memory in use (spill)"
    free_mib = total_mib - used_mib
    if free_mib < lane.headroom_mib:
        return False, f"only {free_mib:.0f} MiB VRAM free, headroom rule needs {lane.headroom_mib:.0f}"
    return True, "ok"
