"""Cloud reference arm: the frozen reasoner mix on an OpenAI-compatible endpoint (DeepSeek Flash anchor).

Runs only on Carter's go with a spend cap. Reads the key from an environment
variable by name and never prints it. Stops the moment the estimated spend passes the cap.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket

from .backend import Backend
from .errors import AbortRun
from .ledger import Ledger
from . import quality


BLOCKED_PORTS = (8080, 8081)
PRIVATE_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".home.arpa", ".corp", ".intranet", ".ts.net")


def _resolve(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def validate_endpoint(endpoint: str, approved_host: str, resolver=None) -> tuple[str, int]:
    """The cloud arm may only talk to the one public https host the window names. Returns (host, port)."""
    from urllib.parse import urlsplit
    resolver = resolver or _resolve
    u = urlsplit(endpoint)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        raise AbortRun("the cloud arm only talks to an https endpoint", "bad_endpoint")
    if not approved_host or host != approved_host.strip().lower():
        raise AbortRun(f"endpoint host {host!r} is not the host the window names (cloud_endpoint_host)", "bad_endpoint")
    if "." not in host or host.endswith(PRIVATE_SUFFIXES):
        raise AbortRun(f"{host!r} looks like an internal name", "bad_endpoint")
    port = u.port or 443
    if port in BLOCKED_PORTS:
        raise AbortRun(f"port {port} is Guardian's", "bad_endpoint")
    try:
        addrs = resolver(host)
    except OSError as exc:
        raise AbortRun(f"cannot resolve {host}: {exc}", "bad_endpoint") from exc
    if not addrs:
        raise AbortRun(f"{host} resolved to nothing", "bad_endpoint")
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # is_global is false for private, loopback, link-local, CGNAT/Tailscale 100.64/10, ULA, multicast, reserved
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise AbortRun(f"{host} resolves to {ip}, which is not a public address", "bad_endpoint")
    return host, port


def is_maverick_item(item: dict) -> bool:
    return item.get("category") == "maverick" or str(item.get("id", "")).startswith("maverick")


def run_anchor(backend: Backend, ledger: Ledger, items: list[dict], endpoint: str, model: str, key_env: str,
               price_in_per_1k: float, price_out_per_1k: float, cap_usd: float, run_id: str, environ=None,
               allow_maverick: bool = False, defer_code: bool = True, raw_dir: str | None = None) -> dict:
    environ = os.environ if environ is None else environ
    if not allow_maverick:  # Maverick-derived tasks are client material: never leave the building without a window approval
        kept = [i for i in items if not is_maverick_item(i)]
        if len(kept) != len(items):
            ledger.event("cloud_items_withheld", reason="maverick items need cloud_allow_maverick_items", n=len(items) - len(kept))
        items = kept
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

    sink: list = []
    results = quality.run_items(Authed(), endpoint, model, items, None, None, think=False, cost_guard=cost_guard,
                                defer_code=defer_code, raw_sink=sink)
    if defer_code and raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        path = os.path.join(raw_dir, f"{model}.anchor.jsonl".replace("/", "_"))
        with open(path, "w", encoding="utf-8") as fh:
            for row in sink:
                fh.write(json.dumps({**row, "model": model, "label": "anchor", "run_id": run_id}, sort_keys=True) + "\n")
        ledger.event("raw_outputs", model=model, label="anchor", path=path, n=len(sink))
    for r in results:
        ledger.append("quality", run_id=run_id, model={"id": model}, mode="anchor",
                      quality={"suite": r.category, "item_id": r.item_id, "pass": r.passed, "detail": r.detail[:160],
                               "tokens_out": r.tokens_out, "wall_s": round(r.wall_s, 3), "finish": r.finish, "valid": r.valid},
                      thinking=False)
    summary = quality.summarize(results)
    deferred = any(c.get("unscored") for c in summary["categories"].values())
    ledger.event("anchor_summary", model=model, summary=summary, spent_usd=round(spent["usd"], 5), cap_usd=cap_usd, deferred=deferred)
    return {"summary": summary, "spent_usd": spent["usd"]}
