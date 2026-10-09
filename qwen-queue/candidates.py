"""Candidate filter: which models a job may be offered. Security-critical.

Pure function over registry facts and readiness. Data must never be offered to
a model whose egress is wider than the job's clearance, whatever its readiness,
cost or quality. Anything unclear fails closed to the narrowest class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from model_registry import EGRESS_ORDER, UNAVAILABLE, ModelSpec, Readiness, egress_rank

NARROWEST = EGRESS_ORDER[0]
# Chief's route policy calls "stays on this PC" `none`; Guardian calls it `pc`.
CLEARANCE_ALIASES = {"none": "pc"}
NO_CANDIDATES = "no_candidates"
OK = "ok"


@dataclass(frozen=True)
class TaskLimits:
    input_tokens: int = 0
    max_output_tokens: int = 1024

    @property
    def context_needed(self) -> int:
        return self.input_tokens + self.max_output_tokens


@dataclass(frozen=True)
class Candidate:
    model_id: str
    egress: str
    readiness: Readiness
    wake_seconds: float
    cost_estimate_usd: float
    quality_tier: int
    needs_approval: bool
    context_tokens: int

    def as_api(self) -> dict[str, Any]:
        return {
            "id": self.model_id, "egress": self.egress, "readiness": self.readiness.as_api(),
            "wake_seconds": self.wake_seconds, "cost_estimate_usd": self.cost_estimate_usd,
            "quality_tier": self.quality_tier, "needs_approval": self.needs_approval,
            "context_tokens": self.context_tokens,
        }


@dataclass(frozen=True)
class CandidateResult:
    status: str
    clearance: str
    cost_ceiling_usd: float
    candidates: tuple[Candidate, ...] = ()
    excluded: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    clearance_note: str | None = None

    def ids(self) -> list[str]:
        return [c.model_id for c in self.candidates]

    def as_api(self) -> dict[str, Any]:
        return {
            "status": self.status, "clearance": self.clearance,
            "cost_ceiling_usd": self.cost_ceiling_usd,
            "candidates": [c.as_api() for c in self.candidates],
            "excluded": [{"id": m, "reason": r} for m, r in self.excluded],
            "clearance_note": self.clearance_note,
        }


def normalize_clearance(clearance: Any) -> tuple[str, str | None]:
    """Returns (effective clearance, note). Missing or unrecognized falls to the narrowest class."""
    if clearance is None:
        return NARROWEST, "clearance absent; defaulted to narrowest"
    if isinstance(clearance, str):
        word = clearance.strip().lower()
        word = CLEARANCE_ALIASES.get(word, word)
        if word in EGRESS_ORDER:
            return word, None
    return NARROWEST, f"clearance {clearance!r} not recognized; defaulted to narrowest"


def normalize_ceiling(ceiling: Any) -> float:
    """No ceiling means zero: a paid model is only offered when someone states what it may cost."""
    if isinstance(ceiling, bool) or not isinstance(ceiling, (int, float)):
        return 0.0
    value = float(ceiling)
    return value if value > 0 else 0.0  # also maps NaN to 0


def estimate_cost_usd(spec: ModelSpec, limits: TaskLimits) -> float:
    """Worst case: every allowed output token is generated."""
    return round(
        limits.input_tokens / 1000 * spec.cost_per_1k_in
        + limits.max_output_tokens / 1000 * spec.cost_per_1k_out,
        8,
    )


def build_candidates(
    registry_state: Sequence[tuple[ModelSpec, Readiness]],
    clearance: Any,
    cost_ceiling: Any,
    task_limits: TaskLimits | None = None,
) -> CandidateResult:
    limits = task_limits or TaskLimits()
    effective, note = normalize_clearance(clearance)
    ceiling = normalize_ceiling(cost_ceiling)
    allowed_rank = egress_rank(effective)
    kept: list[Candidate] = []
    excluded: list[tuple[str, str]] = []
    for spec, readiness in registry_state:
        # Egress first and unconditionally: nothing below can re-admit a too-wide model.
        if egress_rank(spec.egress) > allowed_rank:
            excluded.append((spec.id, "egress_exceeds_clearance"))
            continue
        if readiness.state == UNAVAILABLE:
            excluded.append((spec.id, f"unavailable:{readiness.reason or 'unknown'}"))
            continue
        if limits.context_needed > spec.context_tokens:
            excluded.append((spec.id, "context_too_small"))
            continue
        cost = estimate_cost_usd(spec, limits)
        if cost > ceiling:
            excluded.append((spec.id, "cost_exceeds_ceiling"))
            continue
        kept.append(
            Candidate(
                model_id=spec.id, egress=spec.egress, readiness=readiness,
                wake_seconds=readiness.seconds or 0.0, cost_estimate_usd=cost,
                quality_tier=spec.quality_tier, needs_approval=spec.needs_approval,
                context_tokens=spec.context_tokens,
            )
        )
    return CandidateResult(
        status=OK if kept else NO_CANDIDATES,
        clearance=effective,
        cost_ceiling_usd=ceiling,
        candidates=tuple(kept),
        excluded=tuple(excluded),
        clearance_note=note,
    )
