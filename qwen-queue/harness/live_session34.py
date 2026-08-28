"""Session 3 harness smokes + Session 4 admission probes. Flag must already be on."""
from __future__ import annotations

import importlib.util
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ADAPTER = Path(__file__).resolve().parents[1] / "local-worker-mcp.py"
WS = Path(r"D:\Workspace\tmp\guardian-s34-2026-08-28")
BASE = "http://127.0.0.1:8080"

spec = importlib.util.spec_from_file_location("local_worker_mcp", ADAPTER)
mcp = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mcp)


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw[:400]}
        return exc.code, parsed


def poll(job_id: str, timeout_s: int = 180) -> dict:
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        code, last = http("GET", f"/__guardian/jobs/{job_id}")
        if last.get("status") in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(5)
    raise TimeoutError(f"job {job_id} still {last.get('status')}")


def mcp_json(result: dict) -> dict:
    text = result["content"][0]["text"]
    return json.loads(text)


def main() -> int:
    WS.mkdir(parents=True, exist_ok=True)
    health_code, health = http("GET", "/__guardian/health")
    workers_code, workers = http("GET", "/__guardian/workers")
    print("health", health_code, health.get("status"), "llama_up", health.get("llama_up"),
          "aiwa", ((health.get("seats") or {}).get("aiwa") or {}).get("occupant"))
    print("workers_enabled", workers.get("enabled"))
    if workers.get("enabled") is not True:
        print("FAIL workers not enabled")
        return 1

    for source in ("grok", "hermes"):
        caps = mcp_json(mcp.call_tool("local_worker_capabilities", {}, source))
        print(f"caps {source}", "enabled=", caps.get("enabled"), "error" if caps.get("error") else "ok")
        if caps.get("enabled") is not True:
            print("FAIL capabilities enabled false for", source)
            return 1
        marker = f"S34_{source.upper()}_OK"
        name = f"{source.upper()}_MCP.txt"
        submitted = mcp.call_tool("local_worker_submit", {
            "work_class": "tool_execution",
            "preference": "require_local",
            "quality_floor": "standard",
            "task": f"Create {name} in the current workspace containing exactly {marker}. Do not edit other files.",
            "workspace": str(WS),
            "parent_run_id": f"s34-{source}",
            "timeout_seconds": 300,
        }, source)
        if submitted.get("isError"):
            print("FAIL submit", source, submitted)
            return 1
        payload = mcp_json(submitted)
        print("submit", source, payload.get("job_id"), payload.get("status"), payload.get("source"))
        if payload.get("source") != source:
            print("FAIL attribution", source, payload.get("source"))
            return 1
        done = poll(payload["job_id"])
        print("done", source, done.get("status"), done.get("error"))
        if done.get("status") != "succeeded":
            print("FAIL job", source)
            return 1
        text = (WS / name).read_text(encoding="utf-8", errors="replace").strip()
        if marker not in text:
            print("FAIL fixture", name, repr(text[:200]))
            return 1

    # Session 4 probes: no consult swap, frontier stays cloud.
    occupant_before = ((http("GET", "/__guardian/health")[1].get("seats") or {}).get("aiwa") or {}).get("occupant")
    code, planning = http("POST", "/__guardian/workers", {
        "work_class": "planning",
        "task": "architecture review",
        "workspace": str(WS),
        "source": "grok",
        "idempotency_key": uuid.uuid4().hex,
    })
    print("planning_no_gate", code, planning.get("error", {}).get("code"))
    if code != 409 or (planning.get("error") or {}).get("code") != "consult_gate_required":
        print("FAIL planning probe")
        return 1

    code, gated = http("POST", "/__guardian/workers", {
        "work_class": "planning",
        "gate": "operator",
        "task": "architecture review",
        "workspace": str(WS),
        "source": "grok",
        "idempotency_key": uuid.uuid4().hex,
    })
    print("planning_gated", code, gated.get("error", {}).get("code"))
    if code != 503 or (gated.get("error") or {}).get("code") != "consult_unavailable":
        print("FAIL gated planning probe")
        return 1

    code, frontier = http("POST", "/__guardian/workers", {
        "work_class": "tool_execution",
        "quality_floor": "frontier",
        "task": "port an unknown iOS API",
        "workspace": str(WS),
        "source": "grok",
        "idempotency_key": uuid.uuid4().hex,
    })
    print("frontier", code, frontier.get("error", {}).get("code"))
    if code != 409 or (frontier.get("error") or {}).get("code") != "requires_cloud":
        print("FAIL frontier probe")
        return 1

    occupant_after = ((http("GET", "/__guardian/health")[1].get("seats") or {}).get("aiwa") or {}).get("occupant")
    print("aiwa_occupant", occupant_before, "->", occupant_after)
    if occupant_after != occupant_before:
        print("FAIL unexpected AIWA swap")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
