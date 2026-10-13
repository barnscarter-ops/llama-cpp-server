"""Cloud reference arm: the frozen reasoner mix on an OpenAI-compatible endpoint (DeepSeek Flash anchor).

Runs only on Carter's go with a spend cap. Reads the key from an environment
variable by name and never prints it. Stops the moment the estimated spend passes the cap.
"""

from __future__ import annotations

import os

from .backend import Backend
from .errors import AbortRun
from .ledger import Ledger
from . import quality


def run_anchor(backend: Backend, ledger: Ledger, items: list[dict], endpoint: str, model: str, key_env: str,
               price_in_per_1k: float, price_out_per_1k: float, cap_usd: float, run_id: str, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    key = environ.get(key_env)
    if not key:
        raise AbortRun(f"environment variable {key_env} is not set", "no_key")
    if cap_usd <= 0:
        raise AbortRun("cloud spend cap must be greater than zero (window cloud_spend_cap_usd)", "no_cap")
    spent = {"usd": 0.0}
    headers = {"Authorization": f"Bearer {key}"}

    def cost_guard(r) -> None:
        spent["usd"] += r.prompt_tokens / 1000 * price_in_per_1k + r.completion_tokens / 1000 * price_out_per_1k
        if spent["usd"] > cap_usd:
            raise AbortRun(f"estimated spend ${spent['usd']:.4f} passed the cap ${cap_usd:.2f}", "spend_cap")

    class Authed:  # adds the auth header to every chat call without touching the shared backend
        def chat(self, url, body, timeout=600.0, headers_=None):
            return backend.chat(url, body, timeout, headers)

    results = quality.run_items(Authed(), endpoint, model, items, None, None, think=False, cost_guard=cost_guard)
    for r in results:
        ledger.append("quality", run_id=run_id, model={"id": model}, mode="anchor",
                      quality={"suite": r.category, "item_id": r.item_id, "pass": r.passed, "detail": r.detail[:160],
                               "tokens_out": r.tokens_out, "wall_s": round(r.wall_s, 3), "finish": r.finish, "valid": r.valid},
                      thinking=False)
    summary = quality.summarize(results)
    ledger.event("anchor_summary", model=model, summary=summary, spent_usd=round(spent["usd"], 5), cap_usd=cap_usd)
    return {"summary": summary, "spent_usd": spent["usd"]}
