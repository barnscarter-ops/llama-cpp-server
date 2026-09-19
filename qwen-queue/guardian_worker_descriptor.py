"""Public, non-secret worker descriptor for Guardian-owned disabled-by-default workers.

build_worker_descriptor() projects a WorkerPolicy into the public admission
surface only. It never emits route/executable/endpoint/skill-path material, and
the policyRevision digest binds ONLY this public projection -- never private
runner or skill contents. Origin is a claim; no authenticated authority is
asserted. Existing sibling module: guardian_workers.py (the existing
Guardian admission policy owning worker_policies()).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

# Sibling Guardian admission policy module (owns worker_policies()).
from guardian_workers import worker_policies

_PUBLIC_VERSION = "GuardianWorkerPolicy.v1"
_DESCRIPTOR_VERSION = "GuardianWorkerDescriptor.v1"
_WORK_CLASSES = ("mechanical_execution", "tool_execution")
_DATA_EGRESS = {"mechanical_execution": "lan", "tool_execution": "none"}
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_JS_SAFE_MAX = 2**53 - 1


def _safe_token(value: Any) -> bool:
    """ASCII model identifier; no colon-bearing URL or drive-path forms."""
    return isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) is not None


def _epoch_seconds_to_ms(value: Any) -> int | None:
    """Finite positive epoch seconds -> JS-safe integer ms, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0 or value > _JS_SAFE_MAX / 1000 or not math.isfinite(value):
        return None
    ms = value * 1000
    if not math.isfinite(ms) or ms > _JS_SAFE_MAX:
        return None
    return int(ms)


def _readiness(aiwa_cache: dict[str, Any] | None, workbench_up: bool | None) -> dict[str, Any]:
    """Readiness projection from the AIWA occupant cache or Workbench bool."""
    if aiwa_cache is None:
        # Workbench cache carries no measured timestamp or exact served model.
        return {
            "source": "workbench-health-cache",
            "observedAt": None,
            "reachable": workbench_up if isinstance(workbench_up, bool) else None,
            "modelId": None,
        }
    # Copy ONLY fetchedAt/reachable/modelId. Stale/future timestamps are kept
    # verbatim (Core rejects them); generation time never refreshes observation.
    reachable = aiwa_cache.get("reachable")
    model_id = aiwa_cache.get("modelId")
    return {
        "source": "aiwa-occupant-cache",
        "observedAt": _epoch_seconds_to_ms(aiwa_cache.get("fetchedAt")),
        "reachable": reachable if isinstance(reachable, bool) else None,
        "modelId": model_id if _safe_token(model_id) else None,
    }


def build_worker_descriptor(
    work_class: str,
    *,
    observed_at: int,
    enabled: bool,
    workbench_up: bool | None = None,
    aiwa_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if work_class not in _WORK_CLASSES:
        raise ValueError(f"unsupported work_class: {work_class!r}")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a bool")
    if isinstance(observed_at, bool) or not isinstance(observed_at, int):
        raise ValueError("observed_at must be a JS-safe integer timestamp")
    if not (0 <= observed_at <= _JS_SAFE_MAX):
        raise ValueError("observed_at exceeds JS-safe integer range")
    if aiwa_cache is not None and not isinstance(aiwa_cache, dict):
        raise ValueError("aiwa_cache must be a dict or None")
    if work_class == "mechanical_execution" and aiwa_cache is None:
        raise ValueError("AIWA cache snapshot input is unavailable")

    policy = worker_policies().get(work_class)
    if policy is None:
        raise ValueError(f"no worker policy for {work_class!r}")
    expected_route = "aiwa-clerk" if work_class == "mechanical_execution" else "workbench-executor"
    if policy.work_class != work_class or policy.route != expected_route or policy.runner != "pi" or policy.consult:
        raise ValueError(f"unsupported runner/consult flag for {work_class!r}")

    pi_model = policy.pi_model
    if not isinstance(pi_model, str):
        raise ValueError("unsafe configured model")
    provider, sep, model = pi_model.partition("/")
    if not sep or not _safe_token(provider) or not _safe_token(model):
        raise ValueError(f"unsafe pi_model for {work_class!r}")

    public_policy: dict[str, Any] = {
        "version": _PUBLIC_VERSION,
        "workClass": work_class,
        "provider": provider,
        "model": model,
        "runner": "pi",
        "runnerStatus": "paused",  # fixed documented Pi pause, independent of enablement
        "dataEgress": _DATA_EGRESS[work_class],
        "capabilities": None,
        "scope": "non-secret-projection",
    }
    canonical = json.dumps(
        public_policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    policy_revision = hashlib.sha256(canonical).hexdigest()

    return {
        "version": _DESCRIPTOR_VERSION,
        "origin": "guardian-worker-policy",  # claim only, not authenticated
        "observedAt": observed_at,
        "policyRevision": policy_revision,
        "policy": public_policy,
        "workersEnabled": enabled,
        "readiness": _readiness((aiwa_cache or {}) if work_class == "mechanical_execution" else None, workbench_up),
        "authEntitlement": "unavailable",
        "transportReady": None,
        "capacityReady": None,  # capacity is never claimed from a healthy cache
        "capacityReserved": False,
        "executionAuthorized": False,
    }


__all__ = ["build_worker_descriptor"]
