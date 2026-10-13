"""S1 smoke gate: loads, tool calls round-trip, 4k and 32k prompts return, decode at 8k depth is usable."""

from __future__ import annotations

import json

from . import speed, toolschemas
from .backend import Backend

MIN_DECODE_TPS_AT_8K = 10.0


def tool_roundtrips(backend: Backend, url: str, alias: str, tool_items: list[dict], mode: dict) -> tuple[int, int]:
    """Send up to 3 single-call tool prompts; count correct, strictly valid first calls."""
    singles = [i for i in tool_items if i.get("kind") == "single"][:3]
    good = 0
    for it in singles:
        body = speed.request_body(alias, it["messages"], 400, extra={**mode, "tools": it["tools"]})
        r = backend.chat(url, body, timeout=300.0)
        if r.ok:
            ok, _d, extra = toolschemas.score_tool(r.tool_calls, r.text, it["scorer"], it["tools"])
            good += 1 if ok and extra.get("valid") else 0
    return good, len(singles)


def make_checks(backend: Backend, alias: str, tool_items: list[dict], estimate_gib: float | None, telemetry=None):
    def during(url: str, mode: dict) -> dict:
        out = {}
        good, total = tool_roundtrips(backend, url, alias, tool_items, mode)
        out["tool_roundtrips"] = {"ok": good, "of": total}
        for tokens in (4000, 32000):
            body = speed.request_body(alias, [{"role": "user", "content": speed._fill("prose", tokens, 5) + "\n\n" + speed.INSTRUCTIONS["prose"]}],
                                      64, extra=mode)
            r = backend.chat(url, body, timeout=900.0)
            out[f"prompt_{tokens}"] = bool(r.ok and r.completion_tokens > 0)
        body = speed.request_body(alias, [{"role": "user", "content": speed._fill("prose", 8000, 6) + "\n\n" + speed.INSTRUCTIONS["prose"]}],
                                  200, extra=mode)
        r = backend.chat(url, body, timeout=900.0)
        jr = speed.to_job_result("decode-8k", 8000, r)
        out["decode_tps_8k"] = jr.tg_tps if r.ok else None
        if telemetry is not None and telemetry.last is not None and estimate_gib:
            used_gib = telemetry.last.used_mib / 1024.0
            out["vram_used_gib"] = round(used_gib, 2)
            out["vram_estimate_gib"] = round(estimate_gib, 2)
            out["vram_estimate_error_gib"] = round(used_gib - estimate_gib, 2)
        return out

    return during


def passes(checks: dict) -> tuple[bool, str]:
    tr = checks.get("tool_roundtrips", {"ok": 0, "of": 0})
    if tr["of"] and tr["ok"] < tr["of"]:
        return False, f"tool round-trips {tr['ok']}/{tr['of']}"
    if not checks.get("prompt_4000") or not checks.get("prompt_32000"):
        return False, "4k or 32k prompt did not return"
    d = checks.get("decode_tps_8k")
    if d is None or d < MIN_DECODE_TPS_AT_8K:
        return False, f"decode at 8k depth {d} t/s below {MIN_DECODE_TPS_AT_8K}"
    return True, "ok"
