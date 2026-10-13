"""Load and validate lanes.json / candidates.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .fitcheck import FitInput, Lane

HERE = Path(__file__).resolve().parent.parent  # benchmarks/v2
KV_POLICIES = {"forced_q8", "tested", "fixed_q8"}
ROLES = ("worker_bee", "fast_executor", "slow_reasoner")


class ConfigError(ValueError):
    pass


@dataclass
class LaneConfig:
    name: str
    raw: dict

    @property
    def fit_lane(self) -> Lane:
        r = self.raw
        return Lane(r["vram_gib"], r["headroom_mib"], r.get("overhead_gib", 0.0), r.get("default_compute_gib", 1.0))

    @property
    def port(self) -> int:
        return int(self.raw["bench_port"])

    @property
    def host(self) -> str:
        return self.raw.get("bench_host", "127.0.0.1")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def path(self, key: str, override: str | None = None, env: str | None = None) -> str | None:
        return override or (os.environ.get(env) if env else None) or self.raw.get(key)


@dataclass
class Candidate:
    raw: dict

    def __getitem__(self, k: str):
        return self.raw[k]

    def get(self, k: str, default=None):
        return self.raw.get(k, default)

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def role(self) -> str:
        return self.raw["role"]

    @property
    def lane(self) -> str:
        return self.raw["lane"]

    def fit_input(self, kv_type: str, ctx: int = 131072, with_draft: bool = True,
                  with_mmproj: bool = True) -> FitInput:
        r = self.raw
        off = r.get("moe_offload") or {}
        return FitInput(
            weights_gib=r["weights_gib"], kv_kib_per_token_f16=r["kv_kib_per_token_f16"], kv_type=kv_type,
            ctx=ctx, state_gib=r.get("state_gib", 0.0), compute_gib=r.get("compute_gib"),
            draft_gib=(r.get("draft") or {}).get("gib", 0.0) if with_draft else 0.0,
            mmproj_gib=(r.get("mmproj") or {}).get("gib", 0.0) if with_mmproj else 0.0,
            moe=bool(r.get("moe")), layers=off.get("layers", 0),
            expert_gib_per_layer=off.get("expert_gib_per_layer", 0.0), nonexpert_gib=off.get("nonexpert_gib", 0.0),
        )


def load_lanes(path: str | os.PathLike | None = None) -> dict[str, LaneConfig]:
    doc = json.loads(Path(path or HERE / "lanes.json").read_text(encoding="utf-8"))
    out = {}
    for name, raw in doc["lanes"].items():
        for key in ("vram_gib", "headroom_mib", "bench_port", "gpu_kind", "safety"):
            if key not in raw:
                raise ConfigError(f"lane {name}: missing {key}")
        out[name] = LaneConfig(name, raw)
    return out


def load_candidates(path: str | os.PathLike | None = None, lanes: dict | None = None) -> tuple[dict, list[Candidate]]:
    doc = json.loads(Path(path or HERE / "candidates.json").read_text(encoding="utf-8"))
    roles = doc["roles"]
    seen: set[str] = set()
    cands: list[Candidate] = []
    for raw in doc["models"]:
        cid = raw.get("id")
        if not cid or cid in seen:
            raise ConfigError(f"duplicate or missing candidate id {cid!r}")
        seen.add(cid)
        if raw.get("role") not in roles:
            raise ConfigError(f"{cid}: unknown role {raw.get('role')!r}")
        if lanes is not None and raw.get("lane") not in lanes:
            raise ConfigError(f"{cid}: unknown lane {raw.get('lane')!r}")
        if raw.get("kv_policy") not in KV_POLICIES:
            raise ConfigError(f"{cid}: bad kv_policy {raw.get('kv_policy')!r}")
        for key in ("weights_gib", "kv_kib_per_token_f16", "reasoning_modes", "production_reasoning", "spec_options"):
            if key not in raw:
                raise ConfigError(f"{cid}: missing {key}")
        if raw["production_reasoning"] not in raw["reasoning_modes"]:
            raise ConfigError(f"{cid}: production_reasoning not in reasoning_modes")
        if "off" not in raw["spec_options"]:
            raise ConfigError(f"{cid}: spec_options needs an 'off' entry")
        cands.append(Candidate(raw))
    return roles, cands


def lane_candidates(cands: list[Candidate], lane: str, include_optional: bool = False,
                    include_exception: bool = False) -> list[Candidate]:
    out = []
    for c in cands:
        if c.lane != lane:
            continue
        if c.get("optional") and not (include_optional and c.get("enabled", True) is not False) and not c.get("enabled"):
            continue
        if c.get("exception_only") and not include_exception:
            continue
        out.append(c)
    return out
