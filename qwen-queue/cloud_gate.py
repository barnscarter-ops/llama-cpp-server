"""Front-door gate for cloud models named directly on /v1 or /__guardian/jobs.

The task API checks clearance and cost before a model is ever offered. A caller that names
a cloud model itself skips that, so it must (1) be authorized like any queue client and
(2) state a clearance wide enough for the model's egress. Pure functions, no I/O.
"""

from __future__ import annotations

from typing import Any

from candidates import normalize_clearance
from model_registry import ModelSpec, egress_rank

CLEARANCE_HEADER = "X-Guardian-Clearance"


def refusal(spec: ModelSpec, authorized: bool, clearance: Any) -> tuple[str, str, int] | None:
    """Returns (code, message, status) when a direct cloud call must be refused, else None."""
    if not authorized:
        return ("cloud_forbidden", "Cloud models require loopback or a configured bearer token.", 403)
    if clearance is None or not str(clearance).strip():
        return ("clearance_required", f"Naming '{spec.id}' directly needs {CLEARANCE_HEADER}; or use the task API.", 403)
    effective, _ = normalize_clearance(clearance)
    if egress_rank(effective) < egress_rank(spec.egress):
        return ("clearance_too_narrow", f"Clearance '{effective}' does not allow data to reach '{spec.id}' ({spec.egress}).", 403)
    return None
