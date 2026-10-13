#!/usr/bin/env python3
"""Benchmark harness CLI. See README.md.

Nothing here touches a machine unless `run`, `swap` or `cloud` is given a window
file that Carter created. `plan`, `freeze`, `preflight`, `jev`, `registry`,
`status`, `window-template` and `dry-run` never stop a service or load a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bench_v2 import jevtrack, registry  # noqa: E402
from bench_v2.backend import SystemBackend  # noqa: E402
from bench_v2.config import load_candidates, load_lanes  # noqa: E402
from bench_v2.errors import BenchError  # noqa: E402
from bench_v2.fakes import FakeBackend, oracle_hooks, world_for_lane  # noqa: E402
from bench_v2.lane import Lane, LaneSettings  # noqa: E402
from bench_v2.ledger import Ledger, read_all  # noqa: E402
from bench_v2.suites import builder  # noqa: E402
from bench_v2.window import WINDOW_TEMPLATE  # noqa: E402

STAGES = ("smoke", "tune", "confirm", "ladder", "quality")


def _external(args) -> dict:
    return {k: v for k, v in (("maverick", getattr(args, "maverick", None)), ("humaneval", getattr(args, "humaneval", None))) if v}


def _snapshot(args):
    p = Path(getattr(args, "tool_schemas", None) or HERE / "data" / "tool_schemas.frozen.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _settings(args, dry: bool = False) -> LaneSettings:
    return LaneSettings(
        lane=args.lane, window_path=getattr(args, "window", None), ledger_dir=args.ledger_dir, runs_dir=args.runs_dir,
        server_bin=getattr(args, "server_bin", None), perplexity_bin=getattr(args, "perplexity_bin", None),
        models_dir=getattr(args, "models_dir", None), lanes_path=args.lanes, candidates_path=args.candidates,
        frozen_dir=args.frozen_dir, external=_external(args), only=[x for x in (getattr(args, "only", "") or "").split(",") if x],
        dry_run=dry, skip_hash=not getattr(args, "hash_models", False), allow_unguarded=getattr(args, "allow_unguarded", False))


def _real_backend(args) -> SystemBackend:
    lane = load_lanes(args.lanes)[args.lane]
    pa = lane.raw["power_alarm"]
    return SystemBackend(lane.raw["gpu_kind"], pa.get("command"), pa.get("alarm_regex"), lane.raw.get("shared_mem_cmd"))


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def cmd_plan(args) -> int:
    lane = Lane(_settings(args), FakeBackend())
    for r in lane.plan():
        mark = "FITS " if r["fits"] else "no   "
        print(f"{mark} {r['candidate']:<34} kv={r['kv']:<5} draft={int(r['draft'])} offload={int(r['offload_allowed'])} "
              f"total={r['total_gib']:>6.2f} GiB headroom={r['headroom_gib']:>5.2f}  {r['reason'] if not r['fits'] else ''}")
    return 0


def cmd_freeze(args) -> int:
    snap = _snapshot(args)
    out = Path(args.frozen_dir) if args.frozen_dir else None
    m = builder.freeze(snap, _external(args), out)
    _print({r: {"n": v["n"], "hash": v["hash"][:12], "shortfall": v["shortfall"]} for r, v in m["roles"].items()})
    return 0


def cmd_preflight(args) -> int:
    lane = Lane(_settings(args), _real_backend(args))
    res = lane.preflight()
    _print(res)
    return 0 if res["ok"] else 2


def cmd_run(args) -> int:
    stages = [s for s in args.stages.split(",") if s]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise BenchError(f"unknown stage(s) {bad}; choose from {STAGES}")
    if not args.window:
        raise BenchError("--window is required: the harness never starts a real run without a window file Carter created")
    lane = Lane(_settings(args), _real_backend(args))
    rep = lane.run_stages(stages)
    _print(rep)
    return 3 if "aborted" in rep else 0


def cmd_dry_run(args) -> int:
    """Whole lane against the fake machine: proves wiring, touches nothing."""
    import tempfile
    base = tempfile.mkdtemp(prefix="bench-dry-")
    lanes = load_lanes(args.lanes)
    _, cands = load_candidates(args.candidates, lanes)
    world = world_for_lane(lanes[args.lane], cands)
    snap = _snapshot(args)
    pool = list(builder.load_frozen("worker_bee", Path(args.frozen_dir) if args.frozen_dir else None, snap, _external(args)))
    if snap:
        from bench_v2.toolschemas import gen_tool_items
        pool += gen_tool_items(40, snap)
    world.responder, world.tool_responder = oracle_hooks(pool)
    win = Path(base) / "window.json"
    win_doc = dict(WINDOW_TEMPLATE, lane=args.lane, start="2000-01-01T00:00:00Z", end="2099-01-01T00:00:00Z")
    win.write_text(json.dumps(win_doc), encoding="utf-8")
    args.window, args.ledger_dir, args.runs_dir = str(win), str(Path(base) / "ledger"), str(Path(base) / "runs")
    s = _settings(args, dry=True)
    s.models_dir = s.models_dir or "/fake/models"
    s.server_bin = s.server_bin or "/fake/llama-server"
    s.perplexity_bin = s.perplexity_bin or "/fake/llama-perplexity"
    backend = FakeBackend(world)
    lane = Lane(s, backend)
    rep = lane.run_stages([x for x in args.stages.split(",") if x])
    _print({"stages": {k: (list(v) if isinstance(v, dict) else v) for k, v in rep.items()}, "scratch": base,
            "services_after": world.services, "requests": len(world.requests)})
    return 0


def cmd_swap(args) -> int:
    lane = Lane(_settings(args), _real_backend(args))
    res = lane.swap_test()
    _print(res)
    return 3 if res.get("failed") else 0


def cmd_restore(args) -> int:
    lane = Lane(_settings(args), _real_backend(args))
    _print(lane.restore_now())
    return 0


def cmd_status(args) -> int:
    lane = Lane(_settings(args), FakeBackend())
    recs = list(lane.ledger.records())
    kinds: dict = {}
    for r in recs:
        k = r.get("kind") or r.get("phase")
        kinds[k] = kinds.get(k, 0) + 1
    try:
        n = lane.ledger.verify()
        chain = f"ok ({n} records)"
    except Exception as exc:  # noqa: BLE001
        chain = f"BROKEN: {exc}"
    _print({"lane": args.lane, "ledger": str(lane.ledger.path), "chain": chain, "restore_required": lane.markers.restore_pending(),
            "stop_requested": lane.markers.stop_requested(), "counts": kinds})
    return 0


def cmd_window_template(args) -> int:
    doc = dict(WINDOW_TEMPLATE, lane=args.lane)
    print(json.dumps(doc, indent=2))
    return 0


def cmd_jev(args) -> int:
    models = jevtrack.models_from_registry(args.registry, tuple(x for x in (args.tools_off or "").split(",") if x))
    cases = jevtrack.generate_cases(models)
    records = read_all(args.ledger_dir)
    _, cands = load_candidates(args.candidates)
    rates = jevtrack.pass_rates_from_records(records, {c.id: c["registry"]["id"] for c in cands if c.get("registry")}) if records else None
    labeled = jevtrack.label_cases(cases, models, rates)
    dev, held = jevtrack.split_by_family(labeled)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("cases_dev.jsonl", dev), ("cases_held_out.jsonl", held)):
        (out / name).write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    report = {"cases": len(cases), "dev": len(dev), "held_out": len(held)}
    if args.grade_builtin:
        arms = {}
        for arm in ("rules", "majority", "oracle"):
            arms[arm] = jevtrack.grade(jevtrack.run_builtin(arm, held, models, dev), held)
        report["built_in_arms"] = arms
    if args.grade:
        rows = [json.loads(l) for l in Path(args.grade).read_text(encoding="utf-8").splitlines() if l.strip()]
        report["graded"] = jevtrack.grade(rows, held)
    _print(report)
    return 0


def cmd_registry(args) -> int:
    records = read_all(args.ledger_dir)
    _, cands = load_candidates(args.candidates)
    base = json.loads(Path(args.base_models).read_text(encoding="utf-8"))
    doc, notes = registry.generate_models_json(records, cands, base, args.qwen_queue)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "models.generated.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    frozen = None
    fp = Path(args.frozen_dir or builder.FROZEN_DIR) / "FROZEN.json"
    if fp.exists():
        frozen = json.loads(fp.read_text(encoding="utf-8"))
    (out / "RESULTS.md").write_text(registry.render_results(records, cands, frozen, notes), encoding="utf-8")
    _print({"models_json": str(out / "models.generated.json"), "results": str(out / "RESULTS.md"), "notes": notes})
    return 0


def cmd_cloud(args) -> int:
    from bench_v2 import cloud
    from bench_v2.window import load_window
    from urllib.parse import urlsplit
    from bench_v2.backend import GuardedBackend
    lane = Lane(_settings(args), _real_backend(args))
    w = lane.load_window()
    u = urlsplit(args.endpoint)
    if u.scheme != "https" or not u.hostname or u.hostname in ("localhost",) or u.hostname.startswith(("127.", "192.168.", "10.")):
        raise BenchError("the cloud arm only talks to a public https endpoint")
    cloud_backend = GuardedBackend(lane.raw_backend, {u.port or 443})  # own guard: the lane's local bench-port guard would block it
    cap = float(w.approvals.get("cloud_spend_cap_usd", 0) or 0)
    items = builder.load_frozen("slow_reasoner", Path(args.frozen_dir) if args.frozen_dir else None, lane.snap, _external(args))
    res = cloud.run_anchor(cloud_backend, lane.ledger, items, args.endpoint, args.model, args.key_env,
                           args.price_in, args.price_out, cap, lane.run_id)
    _print(res)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lanes"), p.add_argument("--candidates")
    p.add_argument("--ledger-dir", default=str(HERE / "ledger"))
    p.add_argument("--runs-dir", default=str(HERE / "runs"))
    p.add_argument("--frozen-dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    def lane_args(sp, machine=True):
        sp.add_argument("--lane", required=True, choices=["workbench", "r9700"])
        sp.add_argument("--window")
        sp.add_argument("--only", help="comma list of candidate ids")
        sp.add_argument("--maverick"), sp.add_argument("--humaneval")
        if machine:
            sp.add_argument("--server-bin"), sp.add_argument("--perplexity-bin"), sp.add_argument("--models-dir")
            sp.add_argument("--allow-unguarded", action="store_true", help="skip the numeric guard (values that change numerics are then unmeasured)")
            sp.add_argument("--hash-models", action="store_true")

    for name, fn, machine in (("plan", cmd_plan, False), ("preflight", cmd_preflight, True), ("status", cmd_status, False),
                              ("swap", cmd_swap, True), ("restore", cmd_restore, True), ("freeze", cmd_freeze, False)):
        sp = sub.add_parser(name)
        lane_args(sp, machine)
        sp.add_argument("--tool-schemas")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("run")
    lane_args(sp)
    sp.add_argument("--stages", default=",".join(STAGES))
    sp.set_defaults(fn=cmd_run)
    sp = sub.add_parser("dry-run", help="whole lane on the fake machine")
    lane_args(sp)
    sp.add_argument("--stages", default="smoke")
    sp.set_defaults(fn=cmd_dry_run)
    sp = sub.add_parser("window-template")
    sp.add_argument("--lane", default="workbench")
    sp.set_defaults(fn=cmd_window_template)
    sp = sub.add_parser("jev")
    sp.add_argument("--registry", required=True, help="Guardian models.json")
    sp.add_argument("--tools-off")
    sp.add_argument("--out", required=True)
    sp.add_argument("--grade", help="JSONL of arm answers: {case_id, route, latency_ms, cost_usd}")
    sp.add_argument("--grade-builtin", action="store_true")
    sp.set_defaults(fn=cmd_jev)
    sp = sub.add_parser("registry")
    sp.add_argument("--base-models", required=True, help="current Guardian models.json")
    sp.add_argument("--qwen-queue", required=True, help="dir holding model_registry.py")
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_registry)
    sp = sub.add_parser("cloud", help="DeepSeek reference arm; needs a window with cloud_spend_cap_usd > 0")
    lane_args(sp)
    sp.add_argument("--endpoint", required=True), sp.add_argument("--model", required=True)
    sp.add_argument("--key-env", required=True, help="NAME of the environment variable holding the key")
    sp.add_argument("--price-in", type=float, required=True), sp.add_argument("--price-out", type=float, required=True)
    sp.set_defaults(fn=cmd_cloud)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BenchError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
