"""Quality stage (S5): run a frozen role mix on one server, score deterministically, record each item.

Production reasoning mode on all items; the other mode and the card-vs-greedy
sampling check on 60-item subsets. Rubric items are never auto-scored. Role
pass criteria are applied to the production-mode results.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field

from . import toolschemas
from .backend import Backend
from .ledger import Ledger
from .stats import wilson
from .suites import generators, scoring

SUBSET_N = 60
THINK_MULTIPLIER = 6
MAX_THINK_TOKENS = 16384
REQUEST_TIMEOUT_S = 1800.0


def subset(items: list[dict], n: int = SUBSET_N) -> list[dict]:
    """Deterministic subset in hash order, so the same items are used for every model."""
    return sorted(items, key=lambda i: hashlib.sha256(i["id"].encode()).hexdigest())[:n]


def materialize(item: dict) -> tuple[list[dict], dict]:
    if item.get("longctx"):
        return generators.materialize_longctx(item["longctx"])
    return item["messages"], item["scorer"]


def score_item(item: dict, scorer: dict, text: str, tool_calls: list) -> tuple[bool | None, str, dict]:
    kind = scorer["type"]
    if kind == "rubric":
        return None, "rubric item: unscored without a judge", {}
    if kind == "tool":
        ok, detail, extra = toolschemas.score_tool(tool_calls, scoring.clean(text), scorer, item.get("tools") or [])
        return ok, detail, extra
    fn = scoring.SCORERS[kind]
    ok, detail = fn(text, scorer)
    return ok, detail, {}


@dataclass
class ItemResult:
    item_id: str
    category: str
    passed: bool | None
    detail: str
    tokens_out: int
    wall_s: float
    finish: str
    valid: bool | None = None
    ok_request: bool = True


def run_items(backend: Backend, url: str, alias: str, items: list[dict], sampler: dict | None, mode_extra: dict | None,
              think: bool, on_item=None, cost_guard=None, judge=None) -> list[ItemResult]:
    out = []
    for it in items:
        if on_item:
            on_item()
        messages, scorer = materialize(it)
        base = int(it.get("max_tokens", 800))
        max_tokens = min(MAX_THINK_TOKENS, base * THINK_MULTIPLIER) if think else base
        body = {"model": alias, "messages": messages, "max_tokens": max_tokens, "seed": 1}
        if sampler:
            body.update(sampler)
        if mode_extra:
            body.update(mode_extra)
        if it.get("tools"):
            body["tools"] = it["tools"]
        t0 = time.monotonic()
        r = backend.chat(url, body, timeout=REQUEST_TIMEOUT_S)
        wall = r.total_s or (time.monotonic() - t0)
        if cost_guard:
            cost_guard(r)
        if not r.ok:
            out.append(ItemResult(it["id"], it["category"], False, f"request failed: {r.error}", 0, wall, "error", None, False))
            continue
        passed, detail, extra = score_item({**it, "tools": it.get("tools")}, scorer, r.text, r.tool_calls)
        if passed is None and judge is not None:
            passed, detail = judge(it, r.text)
        out.append(ItemResult(it["id"], it["category"], passed, detail, r.completion_tokens, wall, r.finish_reason, extra.get("valid")))
    return out


def summarize(results: list[ItemResult]) -> dict:
    by = defaultdict(list)
    for r in results:
        by[r.category].append(r)
    cats = {}
    for c, rs in by.items():
        scored = [r for r in rs if r.passed is not None]
        p = sum(1 for r in scored if r.passed)
        lo, hi = wilson(p, len(scored))
        cats[c] = {"n": len(rs), "scored": len(scored), "passed": p, "rate": (p / len(scored)) if scored else None,
                   "ci": [round(lo, 3), round(hi, 3)], "mean_tokens_out": sum(r.tokens_out for r in rs) / len(rs),
                   "wall_s": sum(r.wall_s for r in rs)}
    tools = by.get("tool", [])
    valid = [r for r in tools if r.valid is not None]
    scored = [r for r in results if r.passed is not None]
    return {"categories": cats, "n": len(results), "scored": len(scored),
            "overall": (sum(1 for r in scored if r.passed) / len(scored)) if scored else None,
            "strict_tool_validity": (sum(1 for r in valid if r.valid) / len(valid)) if valid else None,
            "wall_s": sum(r.wall_s for r in results), "tokens_out": sum(r.tokens_out for r in results)}


def rate(summary: dict, cats: tuple[str, ...]) -> float | None:
    p = n = 0
    for c in cats:
        d = summary["categories"].get(c)
        if d and d["scored"]:
            p += d["passed"]
            n += d["scored"]
    return (p / n) if n else None


def role_verdict(role: str, criteria: dict, summary: dict, anchor: dict | None = None) -> dict:
    """Apply the per-role pass criteria (fixed before any result is seen)."""
    if role in ("worker_bee", "fast_executor"):
        tv = summary.get("strict_tool_validity")
        inst = rate(summary, ("ifeval", "clerk"))
        ok = (tv is not None and tv >= criteria["strict_tool_validity_min"]
              and inst is not None and inst >= criteria["instruction_min"])
        return {"pass": ok, "tier": 1 if ok else None, "strict_tool_validity": tv, "instruction": inst,
                "detail": "tier 1 (clerk grade)" if ok else "below the 95% bars; not tier 1"}
    mine = rate(summary, ("analysis", "reasoning", "code_fix", "code_hard"))
    if anchor is None:
        return {"pass": None, "tier": None, "reasoning_rate": mine, "detail": "anchor (DeepSeek Flash) results not available"}
    ref = rate(anchor, ("analysis", "reasoning", "code_fix", "code_hard"))
    gap = (ref - mine) * 100 if (ref is not None and mine is not None) else None
    ok = gap is not None and gap <= criteria["within_points_of_anchor"]
    return {"pass": ok, "tier": 2 if ok else None, "reasoning_rate": mine, "anchor_rate": ref, "gap_points": gap,
            "detail": "tier 2 (within the anchor margin)" if ok else "outside the anchor margin"}


class QualityStage:
    """Runs inside a `PointRunner.run(..., during=...)` so the server lifecycle and safety stay in one place."""

    def __init__(self, backend: Backend, ledger: Ledger, cand, role: str, items: list[dict], run_id: str,
                 safety_check=None, judge=None, strict_greedy_spotcheck: bool = True, extra_runner=None) -> None:
        self.b, self.ledger, self.cand, self.role, self.items, self.run_id = backend, ledger, cand, role, items, run_id
        self.safety_check, self.judge = safety_check, judge
        self.do_greedy = strict_greedy_spotcheck
        self.extra_runner = extra_runner  # e.g. Hermes bench adapter: (url) -> list[ItemResult]
        self.results: dict[str, list[ItemResult]] = {}

    def _record(self, label: str, results: list[ItemResult], think: bool) -> None:
        for r in results:
            self.ledger.append("quality", run_id=self.run_id, model={"id": self.cand.id}, mode=label,
                               quality={"suite": r.category, "item_id": r.item_id, "pass": r.passed, "detail": r.detail[:160],
                                        "tokens_out": r.tokens_out, "wall_s": round(r.wall_s, 3), "finish": r.finish,
                                        "valid": r.valid}, thinking=think)

    def production_pass(self, mode_name: str):
        """`during` callback for the production-mode launch: all items, then the greedy spot-check."""
        think = mode_name == "on"

        def during(url: str, mode: dict) -> dict:
            sampler = dict(self.cand.get("sampler_card") or {})
            res = run_items(self.b, url, self.cand.id, self.items, sampler, mode, think, self.safety_check, judge=self.judge)
            if self.extra_runner:
                res = res + list(self.extra_runner(url))
            self.results[f"production-{mode_name}"] = res
            self._record(f"production-{mode_name}", res, think)
            out = {"production": summarize(res)}
            if self.do_greedy:
                sub = subset(self.items)
                greedy = {"temperature": 0, "top_k": 1}
                gres = run_items(self.b, url, self.cand.id, sub, greedy, mode, think, self.safety_check, judge=self.judge)
                self.results["greedy-subset"] = gres
                self._record("greedy-subset", gres, think)
                out["greedy_subset"] = summarize(gres)
                same = [r for r in res if r.item_id in {i["id"] for i in sub}]
                out["card_vs_greedy_subset"] = summarize(same)
            return out

        return during

    def other_mode_pass(self, mode_name: str):
        """`during` callback for the relaunch in the other reasoning mode (60-item subset)."""
        think = mode_name == "on"

        def during(url: str, mode: dict) -> dict:
            sub = subset(self.items)
            sampler = dict(self.cand.get("sampler_card") or {})
            res = run_items(self.b, url, self.cand.id, sub, sampler, mode, think, self.safety_check, judge=self.judge)
            self.results[f"other-{mode_name}"] = res
            self._record(f"other-{mode_name}", res, think)
            return {"subset": summarize(res), "mode": mode_name}

        return during


def quality_per_second(summary: dict) -> float | None:
    if not summary or not summary.get("scored") or not summary.get("wall_s"):
        return None
    passed = summary["overall"] * summary["scored"]
    return passed / summary["wall_s"]


def toggle_effect(on_summary: dict, off_summary: dict) -> dict:
    """The report shows quality and quality per second for both reasoning modes."""
    return {"on_overall": on_summary.get("overall"), "off_overall": off_summary.get("overall"),
            "on_qps": quality_per_second(on_summary), "off_qps": quality_per_second(off_summary),
            "on_mean_tokens": (on_summary["tokens_out"] / on_summary["n"]) if on_summary.get("n") else None,
            "off_mean_tokens": (off_summary["tokens_out"] / off_summary["n"]) if off_summary.get("n") else None}
