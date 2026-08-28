"""Restart-free MCP stdio + parent-contract checks for grok and hermes sources."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "local-worker-mcp.py"
spec = importlib.util.spec_from_file_location("local_worker_mcp", ADAPTER)
mcp = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mcp)
FORBIDDEN = ("profile", "model", "endpoint", "url", "executable", "command", "args", "runner")


def _run_stdio(source: str, messages: list[dict]) -> list[dict]:
    buf_in = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    buf_out = io.StringIO()
    argv = [str(ADAPTER), "--source", source]
    old_argv, old_in, old_out = sys.argv, sys.stdin, sys.stdout
    sys.argv, sys.stdin, sys.stdout = argv, buf_in, buf_out
    try:
        mcp.main()
    finally:
        sys.argv, sys.stdin, sys.stdout = old_argv, old_in, old_out
    return [json.loads(line) for line in buf_out.getvalue().splitlines()]


def main() -> int:
    serialized = json.dumps(mcp.TOOLS).lower()
    for word in FORBIDDEN:
        if word in serialized:
            print("FAIL tools catalog contains", word)
            return 1
    for source in ("grok", "hermes"):
        replies = _run_stdio(source, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        names = {t["name"] for t in replies[1]["result"]["tools"]}
        expected = {t["name"] for t in mcp.TOOLS}
        if names != expected:
            print("FAIL tools/list", source, names)
            return 1
        raw = mcp.call_tool("local_worker_submit", {
            "work_class": "tool_execution",
            "task": "x",
            "workspace": r"D:\Workspace\tmp\guardian-smoke-2026-08-28",
            "model": "local-llm",
        }, source)
        if raw.get("isError") is not True:
            print("FAIL raw model argument was accepted for", source)
            return 1
        print("ok", source, "initialize+tools/list+reject-raw-model")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
