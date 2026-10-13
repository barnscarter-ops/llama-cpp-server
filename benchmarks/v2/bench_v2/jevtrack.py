"""Jev routing track (protocol section 10): cases, hard-rule labels, split, grading.

This module never calls Jev, DeepSeek or any server. It writes `cases.jsonl` for
the (separately approved) sender and grades arm outputs that come back as JSONL.
Hard rules (clearance, context, tools, availability) produce the acceptable set
automatically; the preferred route needs measured quality, so it stays unlabeled
until the quality stage has run.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

from .stats import percentile, wilson

EGRESS = ("pc", "lan", "internet")
TASK_TYPES = ("clerk", "code_edit", "multi_step", "tool_use", "long_context")
TYPE_CATEGORIES = {"clerk": ("clerk", "ifeval"), "code_edit": ("code_fix", "code_easy"), "multi_step": ("analysis", "reasoning"),
                   "tool_use": ("tool",), "long_context": ("longctx",)}
SIZES = {"short": 1500, "medium": 12000, "near_limit": 120000}
OUT_TOKENS = 1000
STATES = ("all_ready", "one_busy", "one_down", "qwen_needs_approval", "cloud_disabled", "local_down")
COST_CEILINGS = (0.0, 0.01, 1.0)
READINESS_RANK = {"ready": 0, "wakeable": 1, "busy": 2, "needs_approval": 3}
FAMILIES_PER_TYPE = 5
CASES_PER_FAMILY = 8
HELD_OUT_TARGET = 80

TEMPLATES = {
    "clerk": ["rename identifiers in a snippet", "reformat a table", "convert log lines to JSON", "extract fields to a schema", "normalize dates"],
    "code_edit": ["fix a failing unit test", "add a parameter to a function", "write a small helper", "port a loop to a comprehension", "add input validation"],
    "multi_step": ["trace an error across three log files", "plan a safe migration order", "compare two designs", "find why a job stalls", "estimate a change's blast radius"],
    "tool_use": ["read then patch a file", "search then summarize", "submit a worker job", "check job status", "write a notes file"],
    "long_context": ["answer from a long spec", "find a value in a long log", "summarize a long thread", "locate a clause", "cross-reference two long docs"],
}


@dataclass(frozen=True)
class RouteModel:
    id: str
    egress: str
    context_tokens: int
    cost_per_1k_in: float
    cost_per_1k_out: float
    tools: bool
    host: str
    needs_approval: bool = False

    def cost(self, n_in: int, n_out: int = OUT_TOKENS) -> float:
        return n_in / 1000 * self.cost_per_1k_in + n_out / 1000 * self.cost_per_1k_out


def models_from_registry(path: str | Path, tools_off: tuple[str, ...] = ()) -> list[RouteModel]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RouteModel(m["id"], m["egress"], m["context_tokens"], m["cost_per_1k_in"], m["cost_per_1k_out"],
                       m["id"] not in tools_off, m["host"], bool(m.get("needs_approval"))) for m in doc["models"]]


def _availability(state: str, models: list[RouteModel], r: random.Random) -> dict[str, str]:
    avail = {m.id: "ready" for m in models}
    local = [m for m in models if m.host != "provider"]
    if state == "one_busy" and local:
        avail[r.choice(local).id] = "busy"
    elif state == "one_down" and local:
        avail[r.choice(local).id] = "down"
    elif state == "qwen_needs_approval":
        for m in models:
            if m.needs_approval:
                avail[m.id] = "needs_approval"
    elif state == "local_down":
        for m in local:
            avail[m.id] = "down"
    elif state == "cloud_disabled":
        for m in models:
            if m.host == "provider":
                avail[m.id] = "down"
    return avail


def generate_cases(models: list[RouteModel], seed: int = 20261013) -> list[dict]:
    cases = []
    for ti, ttype in enumerate(TASK_TYPES):
        for fi in range(FAMILIES_PER_TYPE):
            family = f"{ttype}-{fi}"
            for k in range(CASES_PER_FAMILY):
                r = random.Random(int(hashlib.sha256(f"jev|{seed}|{family}|{k}".encode()).hexdigest()[:12], 16))
                # stratify: walk the dimension values so every value shows up in every type
                idx = ti * 1000 + fi * CASES_PER_FAMILY + k
                clearance = EGRESS[idx % 3]
                size = ("short", "medium", "near_limit")[(idx // 3) % 3] if ttype != "clerk" or k % 4 else "short"
                if ttype == "long_context":
                    size = ("medium", "near_limit", "near_limit")[k % 3]
                state = STATES[(idx // 2 + fi) % len(STATES)]
                ceiling = COST_CEILINGS[(idx // 5) % 3]
                n_in = SIZES[size] + r.randint(0, 400)
                avail = _availability(state, models, r)
                needs_tools = ttype == "tool_use"
                cases.append({
                    "case_id": f"{family}-{k}", "family": family, "task_type": ttype, "template": TEMPLATES[ttype][fi],
                    "clearance": clearance, "size": size, "input_tokens": n_in, "output_tokens": OUT_TOKENS,
                    "cost_ceiling_usd": ceiling, "needs_tools": needs_tools, "state": state, "availability": avail,
                    "summary": f"{TEMPLATES[ttype][fi]} ({size} input, data may reach: {clearance})",
                })
    return cases


def acceptable_set(case: dict, models: list[RouteModel]) -> list[str]:
    """Hard rules only: clearance, context, tool support, availability, cost ceiling."""
    out = []
    for m in models:
        if EGRESS.index(m.egress) > EGRESS.index(case["clearance"]):
            continue
        if m.context_tokens < case["input_tokens"] + case["output_tokens"]:
            continue
        if case["needs_tools"] and not m.tools:
            continue
        if case["availability"].get(m.id) in ("down", "needs_approval"):
            continue
        if m.cost(case["input_tokens"], case["output_tokens"]) > case["cost_ceiling_usd"] and m.cost_per_1k_in + m.cost_per_1k_out > 0:
            continue
        out.append(m.id)
    return out


def preferred_route(case: dict, models: list[RouteModel], pass_rates: dict[str, dict[str, float]] | None,
                    threshold: float = 0.8) -> str | None:
    """Cheapest acceptable model whose measured pass rate on the task type meets the threshold.
    None when there is no acceptable model (abstain) or no measurements yet (unlabeled)."""
    acc = acceptable_set(case, models)
    if not acc:
        return None
    if not pass_rates:
        return None
    by_id = {m.id: m for m in models}
    ok = [i for i in acc if pass_rates.get(i, {}).get(case["task_type"], 0.0) >= threshold]
    if not ok:  # nothing meets the bar: the best measured acceptable one
        ok = [max(acc, key=lambda i: pass_rates.get(i, {}).get(case["task_type"], 0.0))]
    ok.sort(key=lambda i: (by_id[i].cost(case["input_tokens"], case["output_tokens"]),
                           READINESS_RANK.get(case["availability"].get(i, "ready"), 3), i))
    return ok[0]


def split_by_family(cases: list[dict], held_out_target: int = HELD_OUT_TARGET) -> tuple[list[dict], list[dict]]:
    """Whole families go to one side, so a template cannot leak from dev into held-out."""
    fams = sorted({c["family"] for c in cases}, key=lambda f: hashlib.sha256(f"split|{f}".encode()).hexdigest())
    held, n = set(), 0
    sizes = {f: sum(1 for c in cases if c["family"] == f) for f in fams}
    for f in fams:
        if n >= held_out_target:
            break
        held.add(f)
        n += sizes[f]
    return [c for c in cases if c["family"] not in held], [c for c in cases if c["family"] in held]


def label_cases(cases: list[dict], models: list[RouteModel], pass_rates=None) -> list[dict]:
    out = []
    for c in cases:
        acc = acceptable_set(c, models)
        out.append({**c, "acceptable": acc, "abstain_expected": not acc,
                    "preferred": preferred_route(c, models, pass_rates)})
    return out


def pass_rates_from_records(records, cand_to_registry: dict[str, str]) -> dict[str, dict[str, float]]:
    """Latest production-mode quality result per model, grouped into the track's task types."""
    latest: dict[str, dict] = {}
    records = list(records)
    newest_run: dict[str, object] = {}
    for r in records:  # only the newest production run of each model counts; reruns and pre-fix runs must not mix in
        if r.get("phase") == "quality" and str(r.get("mode", "")).startswith("production"):
            newest_run[r["model"]["id"]] = r.get("run_id")
    for r in records:
        if r.get("phase") == "quality" and str(r.get("mode", "")).startswith("production"):
            if r.get("run_id") != newest_run.get(r["model"]["id"]):
                continue
            q = r["quality"]
            if q["pass"] is None:
                continue
            latest.setdefault(r["model"]["id"], {}).setdefault(q["suite"], [0, 0])
            latest[r["model"]["id"]][q["suite"]][0] += 1 if q["pass"] else 0
            latest[r["model"]["id"]][q["suite"]][1] += 1
    out: dict[str, dict[str, float]] = {}
    for cid, suites in latest.items():
        reg = cand_to_registry.get(cid)
        if not reg:
            continue
        for ttype, cats in TYPE_CATEGORIES.items():
            p = sum(suites[c][0] for c in cats if c in suites)
            n = sum(suites[c][1] for c in cats if c in suites)
            if n:
                out.setdefault(reg, {})[ttype] = p / n
    return out


# -- arms --
def rules_arm(case: dict, models: list[RouteModel]) -> str | None:
    """Guardian-style selector: cheapest acceptable, ready first; abstain if none."""
    by_id = {m.id: m for m in models}
    acc = acceptable_set(case, models)
    if not acc:
        return None
    return sorted(acc, key=lambda i: (by_id[i].cost(case["input_tokens"], case["output_tokens"]),
                                      READINESS_RANK.get(case["availability"].get(i, "ready"), 3), i))[0]


def majority_arm(dev_labeled: list[dict]):
    from collections import Counter
    counts = Counter(c["preferred"] for c in dev_labeled if c.get("preferred"))
    const = counts.most_common(1)[0][0] if counts else None
    return lambda case, models: const


def oracle_arm(case: dict, models: list[RouteModel]) -> str | None:
    return case.get("preferred")


def grade(arm_rows: list[dict], labeled: list[dict]) -> dict:
    """arm_rows: {case_id, route (id or null = abstain), latency_ms?, cost_usd?}. Violations must be zero to qualify."""
    by_case = {c["case_id"]: c for c in labeled}
    seen: dict[str, int] = {}
    for row in arm_rows:
        seen[row["case_id"]] = seen.get(row["case_id"], 0) + 1
    missing = sorted(set(by_case) - set(seen))
    duplicates = sorted(k for k, v in seen.items() if v > 1)
    unknown = sorted(set(seen) - set(by_case))
    n = viol = acc_ok = pref_ok = pref_n = 0
    abst_tp = abst_fp = abst_fn = 0
    lats, cost = [], 0.0
    for row in arm_rows:
        c = by_case.get(row["case_id"])
        if c is None:
            continue
        n += 1
        route = row.get("route")
        acceptable = set(c["acceptable"])
        if route is None:
            if c["abstain_expected"]:
                abst_tp += 1
            else:
                abst_fp += 1
        else:
            if c["abstain_expected"]:
                abst_fn += 1
            if route in acceptable:
                acc_ok += 1
            else:
                viol += 1
        if c.get("preferred") is not None:
            pref_n += 1
            pref_ok += 1 if route == c["preferred"] else 0
        if row.get("latency_ms") is not None:
            lats.append(row["latency_ms"])
        cost += row.get("cost_usd") or 0.0
    routable = sum(1 for r in arm_rows if r["case_id"] in by_case and not by_case[r["case_id"]]["abstain_expected"])
    lo, hi = wilson(acc_ok, max(1, routable))
    return {
        "n": n, "violations": viol, "violation_rate": viol / n if n else None, "qualifies": viol == 0 and n > 0 and not (missing or duplicates or unknown),
        "missing_cases": missing, "duplicate_cases": duplicates, "unknown_cases": unknown,
        "acceptable_accuracy": acc_ok / routable if routable else None, "acceptable_ci": [round(lo, 3), round(hi, 3)],
        "preferred_accuracy": pref_ok / pref_n if pref_n else None, "preferred_labeled": pref_n,
        "abstain_precision": abst_tp / (abst_tp + abst_fp) if (abst_tp + abst_fp) else None,
        "abstain_recall": abst_tp / (abst_tp + abst_fn) if (abst_tp + abst_fn) else None,
        "latency_p50_ms": percentile(lats, 0.5) if lats else None, "latency_p95_ms": percentile(lats, 0.95) if lats else None,
        "cost_usd": cost,
    }


def run_builtin(arm: str, cases: list[dict], models: list[RouteModel], dev: list[dict]) -> list[dict]:
    fn = {"rules": rules_arm, "oracle": oracle_arm, "majority": majority_arm(dev)}[arm]
    return [{"case_id": c["case_id"], "route": fn(c, models), "latency_ms": 0, "cost_usd": 0.0} for c in cases]
