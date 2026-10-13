"""Ledger -> Guardian `models.json` entries and RESULTS.md. Nothing else feeds the registry.

`context_tokens`, `wake_seconds`, `quality_tier` and measured throughput come only
from ledger records. Roles with no passing candidate keep their current entry.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import sys
from pathlib import Path

from .config import Candidate
from .fitcheck import MIN_CTX

SEAT_SOURCE = {"workbench": "workbench_state", "r9700": "aiwa_occupant"}


def _model_id(r: dict):
    m = r.get("model")
    return m.get("id") if isinstance(m, dict) else m


def _latest(records, **match):
    hit = None
    for r in records:
        if all(r.get(k) == v for k, v in match.items()):
            hit = r
    return hit


def model_facts(records: list[dict], cand: Candidate) -> dict:
    """Everything the ledger knows about one candidate."""
    cid = cand.id
    tuned = _latest(records, phase="event", kind="tuned", model=cid)
    ladder = _latest(records, phase="event", kind="ladder_done", model=cid)
    verdict = _latest(records, phase="event", kind="role_verdict", model=cid)
    smoke = _latest([r for r in records if _model_id(r) == cid], phase="smoke")
    dropped = _latest(records, phase="event", kind="smoke_verdict", model=cid)
    quality = None
    for r in records:
        if r.get("phase") == "quality" and _model_id(r) == cid and r.get("checks"):
            quality = r
    return {"tuned": tuned, "ladder": ladder, "verdict": verdict, "smoke": smoke, "smoke_verdict": dropped,
            "quality": (quality or {}).get("checks", {}).get("production") if quality else None}


def build_entry(cand: Candidate, facts: dict) -> dict | None:
    """One models.json entry for a candidate that passed its role criteria, else None."""
    v = facts["verdict"]
    if not v or not v.get("verdict", {}).get("pass"):
        return None
    reg = cand["registry"]
    ladder = facts["ladder"] or {}
    ctx = max(int(ladder.get("max_safe_ctx") or MIN_CTX), MIN_CTX)
    wake = ladder.get("wake_seconds")
    tuned = facts["tuned"] or {}
    entry = {
        "id": reg["id"], "seat": reg["seat"], "locality": "local", "host": reg["host"], "egress": reg["egress"],
        "cost_per_1k_in": 0, "cost_per_1k_out": 0, "context_tokens": ctx, "quality_tier": v["verdict"]["tier"],
        "max_concurrent": 1, "readiness_source": SEAT_SOURCE[cand.lane], "needs_approval": bool(reg["needs_approval"]),
        "wake_seconds": wake if wake is not None else None,
        "bench": {"candidate": cand.id, "file": cand.get("file"), "launch_cfg": tuned.get("cfg"),
                  "tg_tps": tuned.get("final_tg"), "pp_tps": tuned.get("final_pp"), "run": "see ledger"},
    }
    return entry


def rank_role(records: list[dict], role: str, cands: list[Candidate]) -> list[tuple[Candidate, dict]]:
    ranked = []
    for c in cands:
        if c.role != role:
            continue
        f = model_facts(records, c)
        if build_entry(c, f) is None:
            continue
        overall = (f["quality"] or {}).get("overall") or 0.0
        score = (f["tuned"] or {}).get("final_score") or math.inf
        ranked.append((c, f, overall, score))
    ranked.sort(key=lambda t: (-t[2], t[3]))  # best quality first, then lowest latency score
    return [(c, f) for c, f, _o, _s in ranked]


def _load_registry_module(qwen_queue_dir: str | Path):
    path = Path(qwen_queue_dir) / "model_registry.py"
    spec = importlib.util.spec_from_file_location("bench_model_registry", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_model_registry"] = mod
    spec.loader.exec_module(mod)
    return mod


def generate_models_json(records: list[dict], cands: list[Candidate], base_models: dict, qwen_queue_dir: str | Path) -> tuple[dict, list[str]]:
    """New registry document and notes. The result is validated by Guardian's own parser."""
    doc = copy.deepcopy(base_models)
    notes = []
    by_seat = {m.get("seat"): i for i, m in enumerate(doc["models"]) if m.get("seat")}
    for role in ("worker_bee", "fast_executor", "slow_reasoner"):
        ranked = rank_role(records, role, cands)
        if not ranked:
            notes.append(f"{role}: no candidate passed its criteria; the current entry is unchanged")
            continue
        cand, facts = ranked[0]
        entry = build_entry(cand, facts)
        if entry["wake_seconds"] is None:
            old = doc["models"][by_seat[entry["seat"]]] if entry["seat"] in by_seat else {}
            entry["wake_seconds"] = old.get("wake_seconds", 60)
            notes.append(f"{role}: no cold-load measurement yet, kept wake_seconds {entry['wake_seconds']}")
        if entry["seat"] in by_seat:
            doc["models"][by_seat[entry["seat"]]] = entry
        else:
            doc["models"].append(entry)
        notes.append(f"{role}: {cand.id} -> {entry['id']} (tier {entry['quality_tier']}, ctx {entry['context_tokens']}, wake {entry['wake_seconds']} s)")
    mod = _load_registry_module(qwen_queue_dir)
    mod.parse_registry(doc)  # raises RegistryError if the generated file would not load in Guardian
    return doc, notes


def _fmt(x, nd=1):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def render_results(records: list[dict], cands: list[Candidate], frozen: dict | None, notes: list[str]) -> str:
    lines = ["# Benchmark results", "", "Generated from the ledger; do not edit by hand.", ""]
    for role in ("worker_bee", "fast_executor", "slow_reasoner"):
        lines += [f"## {role}", "", "| Candidate | Smoke | Final tg t/s | pp t/s | Peak MiB | Max safe ctx | Wake s | Quality | Tool validity | Verdict |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for c in [x for x in cands if x.role == role]:
            f = model_facts(records, c)
            q = f["quality"] or {}
            t = f["tuned"] or {}
            lad = f["ladder"] or {}
            v = (f["verdict"] or {}).get("verdict", {})
            sv = f["smoke_verdict"] or {}
            smoke = "dropped: " + sv.get("reason", "") if sv.get("dropped") else ("ok" if f["smoke"] else "not run")
            lines.append(f"| {c.id} | {smoke} | {_fmt(t.get('final_tg'))} | {_fmt(t.get('final_pp'), 0)} | {_fmt(lad.get('peak_mib'), 0)} | "
                         f"{_fmt(lad.get('max_safe_ctx'))} | {_fmt(lad.get('wake_seconds'))} | {_fmt(q.get('overall'), 3)} | "
                         f"{_fmt(q.get('strict_tool_validity'), 3)} | {v.get('detail', '-')} |")
        lines.append("")
    lines += ["## Decisions", ""]
    for r in records:
        if r.get("phase") == "event" and r.get("kind") == "decision":
            lines.append(f"- {r['model']}: lever {r['lever']} = {r['value']} -> {r['outcome']} ({r['detail']})")
    lines += ["", "## Suite shortfalls (not padded)", ""]
    for role, info in ((frozen or {}).get("roles") or {}).items():
        for cat, s in info.get("shortfall", {}).items():
            lines.append(f"- {role}/{cat}: wanted {s['wanted']}, have {s['have']} ({s['reason']})")
    lines += ["", "## Registry notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
