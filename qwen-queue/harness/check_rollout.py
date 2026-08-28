"""Read-only Session 6 checks: fragment paths, live guardian policy, no 8081.

Does not change firewall, PM2, Pi, or LOCAL_WORKER_ENABLED. Does not POST
to :8081 or .240. Does not dump secrets.
"""
from __future__ import annotations

import json
import socket
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "qwen-queue" / "local-worker-mcp.py"
PY = Path(r"C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe")
DOCS = (
    REPO / "qwen-queue" / "harness" / "ROLLOUT.md",
    REPO / "LOCAL-WORKER-MANAGER.md",
    REPO / "qwen-queue" / "README.md",
    REPO / "qwen-queue" / "harness" / "README.md",
    REPO / "PLAN.md",
)
GUARDIAN = "http://127.0.0.1:8080"


def get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{GUARDIAN}{path}", timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def port_listening(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def mcp_names_codex() -> list[str]:
    data = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8"))
    mcp = data.get("mcp_servers") or {}
    return sorted(mcp.keys()) if isinstance(mcp, dict) else []


def mcp_names_claude() -> list[str]:
    data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    mcp = data.get("mcpServers") or {}
    return sorted(mcp.keys()) if isinstance(mcp, dict) else []


def main() -> int:
    if not PY.is_file():
        print("FAIL python 3.12 path missing", PY)
        return 1
    if not ADAPTER.is_file():
        print("FAIL adapter missing", ADAPTER)
        return 1
    print("ok paths python+adapter")

    for doc in DOCS:
        if not doc.is_file():
            print("FAIL missing doc", doc)
            return 1
        text = doc.read_text(encoding="utf-8")
        if str(ADAPTER) not in text and "local-worker-mcp.py" not in text:
            print("FAIL", doc.name, "does not mention adapter")
            return 1
    rollout = (REPO / "qwen-queue" / "harness" / "ROLLOUT.md").read_text(encoding="utf-8")
    for needle in (
        "--source', 'codex'",
        '"--source"',
        '"claude"',
        "Remain **cloud**",
        "Do not deploy",
        "127.0.0.1:8081",
        "192.168.1.240:8080",
    ):
        if needle not in rollout:
            print("FAIL ROLLOUT.md missing", needle)
            return 1
    print("ok docs exist and name adapter")

    try:
        health = get_json("/__guardian/health")
        workers = get_json("/__guardian/workers")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print("FAIL guardian unreachable", exc)
        return 1
    if health.get("status") != "ok":
        print("FAIL guardian status", health.get("status"))
        return 1
    if health.get("llama_up") is not False:
        print("FAIL expected llama_up=false (no :8081 client)")
        return 1
    seats = health.get("seats") or {}
    aiwa = seats.get("aiwa") or {}
    if aiwa.get("occupant") != "clerk":
        print("FAIL expected AIWA occupant clerk", aiwa.get("occupant"))
        return 1
    if aiwa.get("endpoint") != "http://192.168.1.240:8080":
        print("FAIL unexpected AIWA endpoint")
        return 1
    workbench = seats.get("workbench") or {}
    if workbench.get("endpoint") != "http://127.0.0.1:8081":
        print("FAIL unexpected workbench upstream endpoint")
        return 1
    if workers.get("enabled") is not False:
        print("FAIL expected workers enabled=false")
        return 1
    print("ok guardian policy workers=off llama_up=false aiwa=clerk")

    if port_listening("127.0.0.1", 8081):
        print("FAIL :8081 is accepting connections; docs say it is down")
        return 1
    if not port_listening("127.0.0.1", 8080):
        print("FAIL :8080 is not listening")
        return 1
    print("ok ports 8080 up, 8081 down")

    codex_mcp = mcp_names_codex()
    claude_mcp = mcp_names_claude()
    if "guardian_local_worker" in codex_mcp:
        print("FAIL Codex live-wired; Session 6 fragments are docs-only")
        return 1
    if "guardian_local_worker" in claude_mcp:
        print("FAIL Claude live-wired; Session 6 fragments are docs-only")
        return 1
    print("ok Codex MCP", ",".join(codex_mcp) or "(none)", "no guardian_local_worker")
    print("ok Claude MCP", ",".join(claude_mcp) or "(none)", "no guardian_local_worker")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
