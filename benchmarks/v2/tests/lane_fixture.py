"""Builds a Lane on the fake machine inside a temp dir. Nothing here opens a socket or runs a subprocess."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bench_v2.config import load_candidates, load_lanes  # noqa: E402
from bench_v2.fakes import FakeBackend, oracle_hooks, world_for_lane  # noqa: E402
from bench_v2.lane import Lane, LaneSettings  # noqa: E402
from bench_v2.suites import builder  # noqa: E402
from bench_v2.toolschemas import gen_tool_items  # noqa: E402
from bench_v2.window import WINDOW_TEMPLATE  # noqa: E402

SNAP = json.loads((HERE / "data" / "tool_schemas.frozen.json").read_text(encoding="utf-8"))
_FROZEN = {}


def frozen_dir() -> Path:
    """One frozen suite per test process (building it takes a couple of seconds)."""
    if "dir" not in _FROZEN:
        d = Path(tempfile.mkdtemp(prefix="bench-frozen-"))
        builder.freeze(SNAP, None, d)
        _FROZEN["dir"] = d
    return _FROZEN["dir"]


@dataclass
class Env:
    lane: Lane
    world: object
    backend: FakeBackend
    root: Path
    window_path: Path


def make_env(lane_name: str = "workbench", approvals: dict | None = None, window_over: dict | None = None, backend_cls=FakeBackend,
             oracle: bool = True, only: list | None = None, **world_kw) -> Env:
    root = Path(tempfile.mkdtemp(prefix="bench-test-"))
    lanes = load_lanes()
    _, cands = load_candidates(None, lanes)
    world = world_for_lane(lanes[lane_name], cands, **world_kw)
    if oracle:
        pool = list(builder.load_frozen("worker_bee", frozen_dir(), SNAP)) + gen_tool_items(40, SNAP)
        for role in ("fast_executor", "slow_reasoner"):
            pool += builder.load_frozen(role, frozen_dir(), SNAP)
        world.responder, world.tool_responder = oracle_hooks(pool)
    win = dict(WINDOW_TEMPLATE, lane=lane_name, start="2000-01-01T00:00:00Z", end="2099-01-01T00:00:00Z")
    win["approvals"] = dict(WINDOW_TEMPLATE["approvals"], **(approvals or {}))
    win.update(window_over or {})
    wp = root / "window.json"
    wp.write_text(json.dumps(win), encoding="utf-8")
    s = LaneSettings(lane=lane_name, window_path=str(wp), ledger_dir=str(root / "ledger"), runs_dir=str(root / "runs"),
                     server_bin="/fake/llama-server", perplexity_bin="/fake/llama-perplexity", models_dir="/fake/models",
                     frozen_dir=str(frozen_dir()), only=only or [], dry_run=True)
    backend = backend_cls(world)
    return Env(Lane(s, backend), world, backend, root, wp)


def event_kinds(lane: Lane) -> list[str]:
    return [r.get("kind") or r.get("phase") for r in lane.ledger.records()]
