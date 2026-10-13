"""Model registry: one source of truth for every model Guardian could use.

Pure data + readiness classification. Nothing here wakes a model: Workbench
readiness reads in-process state only, R9700 readiness goes through the
existing 2 s occupant cache, and cloud readiness never calls a provider.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "models.json"

# Narrowest to widest. A job's clearance names the widest class its data may reach.
EGRESS_ORDER = ("pc", "lan", "internet")
LOCALITIES = frozenset({"local", "cloud"})
HOSTS = frozenset({"workbench", "r9700", "provider"})
READINESS_SOURCES = frozenset({"workbench_state", "aiwa_occupant", "cloud_state"})
SEATS = frozenset({"glm", "clerk", "consult"})

READY = "ready"
WAKEABLE = "wakeable"
NEEDS_APPROVAL = "needs_approval"
UNAVAILABLE = "unavailable"


class RegistryError(ValueError):
    """models.json is malformed or contradicts itself."""


def egress_rank(egress: str) -> int:
    try:
        return EGRESS_ORDER.index(egress)
    except ValueError:
        raise RegistryError(f"unknown egress class {egress!r}") from None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    locality: str
    host: str
    egress: str
    cost_per_1k_in: float
    cost_per_1k_out: float
    context_tokens: int
    quality_tier: int
    max_concurrent: int
    queue_key: str
    readiness_source: str
    needs_approval: bool
    wake_seconds: float
    seat: str | None = None
    key_env: str | None = None

    def as_api(self) -> dict[str, Any]:
        return {
            "id": self.id, "seat": self.seat, "locality": self.locality, "host": self.host,
            "egress": self.egress, "cost_per_1k_in": self.cost_per_1k_in,
            "cost_per_1k_out": self.cost_per_1k_out, "context_tokens": self.context_tokens,
            "quality_tier": self.quality_tier, "max_concurrent": self.max_concurrent,
            "queue_key": self.queue_key, "needs_approval": self.needs_approval,
            "wake_seconds": self.wake_seconds,
        }


def _require(entry: dict, key: str, kind: type | tuple, where: str) -> Any:
    value = entry.get(key)
    # bool is an int subclass; a flag where a number belongs is a typo, not a value.
    if isinstance(value, bool) and kind is not bool or not isinstance(value, kind):
        raise RegistryError(f"{where}: {key} must be {kind}, got {value!r}")
    return value


def _parse_entry(entry: Any, index: int) -> ModelSpec:
    if not isinstance(entry, dict):
        raise RegistryError(f"models[{index}] must be an object")
    where = f"models[{index}]"
    model_id = _require(entry, "id", str, where).strip()
    if not model_id:
        raise RegistryError(f"{where}: id must be non-empty")
    where = f"model {model_id!r}"
    locality = _require(entry, "locality", str, where)
    if locality not in LOCALITIES:
        raise RegistryError(f"{where}: bad locality {locality!r}")
    host = _require(entry, "host", str, where)
    if host not in HOSTS:
        raise RegistryError(f"{where}: bad host {host!r}")
    egress = _require(entry, "egress", str, where)
    egress_rank(egress)
    source = _require(entry, "readiness_source", str, where)
    if source not in READINESS_SOURCES:
        raise RegistryError(f"{where}: bad readiness_source {source!r}")
    tier = _require(entry, "quality_tier", int, where)
    if not 1 <= tier <= 3:
        raise RegistryError(f"{where}: quality_tier must be 1-3")
    max_concurrent = _require(entry, "max_concurrent", int, where)
    if max_concurrent < 1:
        raise RegistryError(f"{where}: max_concurrent must be >= 1")
    context = _require(entry, "context_tokens", int, where)
    if context < 1:
        raise RegistryError(f"{where}: context_tokens must be >= 1")
    cost_in = _require(entry, "cost_per_1k_in", (int, float), where)
    cost_out = _require(entry, "cost_per_1k_out", (int, float), where)
    if cost_in < 0 or cost_out < 0:
        raise RegistryError(f"{where}: costs must be >= 0")
    wake = _require(entry, "wake_seconds", (int, float), where)
    if wake < 0:
        raise RegistryError(f"{where}: wake_seconds must be >= 0")
    seat = entry.get("seat")
    if seat is not None and seat not in SEATS:
        raise RegistryError(f"{where}: bad seat {seat!r}")
    needs_approval = _require(entry, "needs_approval", bool, where)
    key_env = entry.get("key_env")
    if key_env is not None and not isinstance(key_env, str):
        raise RegistryError(f"{where}: key_env must be a string")
    if locality == "local":
        # Two tasks on one local model crash it; the registry refuses to describe otherwise.
        if max_concurrent != 1:
            raise RegistryError(f"{where}: local models must have max_concurrent 1")
        if cost_in or cost_out:
            raise RegistryError(f"{where}: local models cost 0")
        if seat is None:
            raise RegistryError(f"{where}: local models need a seat")
    else:
        if host != "provider" or egress != "internet" or source != "cloud_state":
            raise RegistryError(f"{where}: cloud models must be host provider, egress internet, cloud_state")
    queue_key = entry.get("queue_key") or model_id
    if not isinstance(queue_key, str):
        raise RegistryError(f"{where}: queue_key must be a string")
    return ModelSpec(
        id=model_id, locality=locality, host=host, egress=egress,
        cost_per_1k_in=float(cost_in), cost_per_1k_out=float(cost_out),
        context_tokens=context, quality_tier=tier, max_concurrent=max_concurrent,
        queue_key=queue_key, readiness_source=source, needs_approval=needs_approval,
        wake_seconds=float(wake), seat=seat, key_env=key_env,
    )


class ModelRegistry:
    def __init__(self, specs: list[ModelSpec]) -> None:
        seen: set[str] = set()
        seats: set[str] = set()
        for spec in specs:
            if spec.id in seen:
                raise RegistryError(f"duplicate model id {spec.id!r}")
            seen.add(spec.id)
            if spec.seat:
                if spec.seat in seats:
                    raise RegistryError(f"seat {spec.seat!r} claimed by two models")
                seats.add(spec.seat)
        self._specs = {s.id: s for s in specs}

    def __iter__(self):
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def get(self, model_id: str) -> ModelSpec | None:
        return self._specs.get(model_id)

    def ids(self) -> list[str]:
        return list(self._specs)

    def for_seat(self, seat: str) -> ModelSpec | None:
        """Aliases keep resolving through fleet_router.seat_for_model; this maps seat to model."""
        return next((s for s in self._specs.values() if s.seat == seat), None)


def parse_registry(document: Any) -> ModelRegistry:
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise RegistryError("registry must be an object with a models array")
    return ModelRegistry([_parse_entry(e, i) for i, e in enumerate(document["models"])])


def load_registry(path: str | os.PathLike | None = None) -> ModelRegistry:
    target = Path(path or os.environ.get("GUARDIAN_MODELS_FILE") or DEFAULT_REGISTRY_PATH)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read registry {target}: {exc}") from exc
    return parse_registry(document)


# ─────────────────────────────────────────────────────────────────────────────
#  READINESS
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Readiness:
    state: str
    seconds: float | None = None
    reason: str | None = None

    def as_api(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.seconds is not None:
            out["seconds"] = self.seconds
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class WorkbenchState:
    """In-process Workbench facts. Gathering these must never touch port 8081."""
    llama_up: bool
    ram_ok: bool = True
    cooldown_remaining_s: float = 0.0
    last_start_failure: str | None = None


@dataclass(frozen=True)
class CloudState:
    enabled: bool
    key_present: bool
    cap_left: bool = True
    slot_free: bool = True


WorkbenchProvider = Callable[[], WorkbenchState]
AiwaProvider = Callable[[], Awaitable[tuple[str, str | None, bool]]]
CloudProvider = Callable[[ModelSpec], CloudState]


def cloud_flag_enabled() -> bool:
    return os.environ.get("GUARDIAN_CLOUD", "false").strip().lower() in {"true", "1", "yes"}


def env_cloud_state(spec: ModelSpec) -> CloudState:
    """Default cloud facts: flag + key presence only. Spend and slots arrive with the cloud backend."""
    return CloudState(
        enabled=cloud_flag_enabled(),
        key_present=bool(spec.key_env and os.environ.get(spec.key_env, "").strip()),
    )


class ReadinessProbe:
    def __init__(
        self,
        workbench: WorkbenchProvider,
        aiwa: AiwaProvider,
        cloud: CloudProvider = env_cloud_state,
    ) -> None:
        self._workbench = workbench
        self._aiwa = aiwa
        self._cloud = cloud

    async def readiness(self, spec: ModelSpec) -> Readiness:
        if spec.readiness_source == "workbench_state":
            return self._workbench_readiness(spec)
        if spec.readiness_source == "aiwa_occupant":
            return await self._aiwa_readiness(spec)
        return self._cloud_readiness(spec)

    def _workbench_readiness(self, spec: ModelSpec) -> Readiness:
        state = self._workbench()
        if state.llama_up:
            return Readiness(READY)
        if state.last_start_failure == "gpu_placement":
            return Readiness(UNAVAILABLE, reason="gpu_placement")
        if not state.ram_ok:
            return Readiness(UNAVAILABLE, reason="ram_low")
        return Readiness(WAKEABLE, seconds=spec.wake_seconds + max(0.0, state.cooldown_remaining_s))

    async def _aiwa_readiness(self, spec: ModelSpec) -> Readiness:
        occupant, _model_id, reachable = await self._aiwa()
        if not reachable:
            return Readiness(UNAVAILABLE, reason="aiwa_unreachable")
        if occupant not in {"clerk", "consult"}:
            return Readiness(UNAVAILABLE, reason="aiwa_occupant_unknown")
        if occupant == spec.seat:
            return Readiness(READY)
        # Qwen never loads without Carter; Nemotron is the resting model and reloads on its own.
        if spec.needs_approval:
            return Readiness(NEEDS_APPROVAL, seconds=spec.wake_seconds)
        return Readiness(WAKEABLE, seconds=spec.wake_seconds)

    def _cloud_readiness(self, spec: ModelSpec) -> Readiness:
        state = self._cloud(spec)
        if not state.enabled:
            return Readiness(UNAVAILABLE, reason="cloud_disabled")
        if not state.key_present:
            return Readiness(UNAVAILABLE, reason="no_key")
        if not state.cap_left:
            return Readiness(UNAVAILABLE, reason="spend_cap")
        if not state.slot_free:
            return Readiness(UNAVAILABLE, reason="no_slot")
        return Readiness(READY)

    async def snapshot(self, registry: ModelRegistry) -> list[dict[str, Any]]:
        out = []
        for spec in registry:
            entry = spec.as_api()
            entry["readiness"] = (await self.readiness(spec)).as_api()
            out.append(entry)
        return out
