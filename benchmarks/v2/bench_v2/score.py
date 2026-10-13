"""Score step: run the model-generated code that the GPU machines deferred, then finish the verdicts.

Runs on Linux, as root dropping to an unprivileged user or inside a sandbox command (for example a throwaway
container). It never loads a model and never touches a lane. Results go to a separate, hash-chained ledger
(`<lane>.scored.jsonl`) so the source ledger stays exactly as the GPU machine wrote it.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import load_candidates, load_lanes
from .errors import BenchError
from .ledger import Ledger, read_all
from .quality import ItemResult, role_verdict, summarize
from .suites import builder, scoring


def _results_from_ledger(records: list[dict], model: str, label: str, run_id: str | None, late: dict[str, tuple[bool, str]]) -> list[ItemResult]:
    out = []
    for r in records:
        if r.get("phase") != "quality" or r.get("mode") != label or (r.get("model") or {}).get("id") != model:
            continue
        if r.get("run_id") != run_id:  # the source ledger is append-only and may hold several runs of the same model
            continue
        q = r["quality"]
        passed, detail = q["pass"], q["detail"]
        if q["item_id"] in late:
            passed, detail = late[q["item_id"]]
        out.append(ItemResult(q["item_id"], q["suite"], passed, detail, q["tokens_out"], q["wall_s"], q["finish"], q.get("valid")))
    return out


def score_dir(args) -> dict:
    lanes = load_lanes(args.lanes)
    roles, cands = load_candidates(args.candidates, lanes)
    by_id = {c.id: c for c in cands}
    scoring.configure_runner(drop_user=args.run_as, sandbox_cmd=list(args.sandbox_cmd or []) or None)
    ok, why = scoring.can_execute()
    if not ok:
        raise BenchError(f"the score step cannot run model-generated code here: {why}. "
                         "Run it on Linux as root (drops to nobody) or pass --sandbox-cmd with a container prefix.")
    src = Ledger(args.source_ledger)
    src.verify()
    records = list(src.records())
    out_path = args.out_ledger or str(Path(args.ledger_dir) / f"{args.lane}.scored.jsonl")
    out = Ledger(out_path)
    snap = json.loads(Path(args.tool_schemas).read_text()) if getattr(args, "tool_schemas", None) else None
    if snap is None:
        default = Path(__file__).resolve().parent.parent / "data" / "tool_schemas.frozen.json"
        snap = json.loads(default.read_text()) if default.exists() else None
    frozen_dir = Path(args.frozen_dir) if args.frozen_dir else None
    items_cache: dict[str, dict] = {}

    def items_for(role: str) -> dict:
        if role not in items_cache:
            items_cache[role] = {i["id"]: i for i in builder.load_frozen(role, frozen_dir, snap)}
        return items_cache[role]

    scored_labels: dict[tuple[str, str, str | None], dict[str, tuple[bool, str]]] = {}
    n = 0
    for path in sorted(Path(args.raw_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            model, label = row["model"], row["label"]
            role = by_id[model].role if model in by_id else "slow_reasoner"  # the cloud anchor reuses the reasoner mix
            item = items_for(role).get(row["item_id"])
            if item is None or item["scorer"].get("type") != "python":
                continue
            try:
                passed, detail = scoring.score_python(row["text"], item["scorer"])
            except RuntimeError as exc:
                raise BenchError(str(exc)) from exc
            scored_labels.setdefault((model, label, row.get("run_id")), {})[item["id"]] = (passed, detail)
            out.append("quality", run_id=row.get("run_id"), model={"id": model}, mode=f"{label}:scored",
                       quality={"suite": item["category"], "item_id": item["id"], "pass": passed, "detail": detail[:160],
                                "tokens_out": 0, "wall_s": 0.0, "finish": "scored-late", "valid": None}, thinking=None)
            n += 1

    verdicts = {}
    anchor_summary = None
    for (model, label, run_id), late in scored_labels.items():
        if label == "anchor":
            summary = summarize(_results_from_ledger(records, model, "anchor", run_id, late))
            out.event("anchor_summary", model=model, summary=summary, deferred=False, scored_late=True)
            anchor_summary = summary
    if anchor_summary is None:  # an already-scored anchor from an earlier score run or from a lane that could execute
        for r in reversed(list(read_all(args.ledger_dir))):
            if r.get("phase") == "event" and r.get("kind") == "anchor_summary" and not r.get("deferred"):
                anchor_summary = r["summary"]
                break
    for (model, label, run_id), late in scored_labels.items():
        if not label.startswith("production-") or model not in by_id:
            continue
        c = by_id[model]
        summary = summarize(_results_from_ledger(records, model, label, run_id, late))
        v = role_verdict(c.role, roles[c.role]["criteria"], summary, anchor_summary)
        out.event("role_verdict", model=model, role=c.role, verdict=v, overall=summary.get("overall"), scored_late=True)
        verdicts[model] = v
    return {"items_scored": n, "verdicts": verdicts, "anchor": bool(anchor_summary), "out_ledger": out_path}
