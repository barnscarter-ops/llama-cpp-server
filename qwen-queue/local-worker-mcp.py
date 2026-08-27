"""Small stdio MCP adapter for guardian-managed local workers.

Run one instance per parent harness with ``--source grok`` (or ``hermes``,
``codex``, etc.).  The adapter intentionally exposes bounded work classes,
not OpenAI URLs, model aliases, or raw Pi command arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any


def guardian_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = os.environ.get("GUARDIAN_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message") or json.dumps(detail)
        except Exception:
            message = exc.reason
        raise RuntimeError(f"Guardian returned HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Guardian is unreachable: {exc.reason}") from exc


TOOLS = [
    {
        "name": "local_worker_profiles",
        "description": "List guardian-approved local worker capabilities and whether dispatch is enabled.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "local_worker_submit",
        "description": "Request a guardian-managed local worker. The worker may edit files only inside workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_class": {"type": "string", "enum": ["mechanical_execution", "tool_execution", "planning", "deep_analysis"]},
                "preference": {"type": "string", "enum": ["prefer_local", "require_local"]},
                "quality_floor": {"type": "string", "enum": ["standard", "frontier"]},
                "gate": {"type": "string", "enum": ["operator", "frontier"]},
                "task": {"type": "string", "description": "Bounded worker task."},
                "workspace": {"type": "string", "description": "Existing workspace directory under D:\\Workspace."},
                "parent_run_id": {"type": "string", "description": "Optional parent harness run/session ID."},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 3600},
            },
            "required": ["work_class", "task", "workspace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "local_worker_status",
        "description": "Get the durable status and final result for a guardian worker job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "local_worker_cancel",
        "description": "Cancel a queued guardian worker job before it starts.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
]


def tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, indent=2)}], "isError": is_error}


def call_tool(name: str, arguments: Any, source: str) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "local_worker_profiles":
            return tool_result(guardian_request("GET", "/__guardian/workers"))
        if name == "local_worker_submit":
            payload = dict(args)
            payload["source"] = source
            payload["idempotency_key"] = payload.get("idempotency_key") or uuid.uuid4().hex
            return tool_result(guardian_request("POST", "/__guardian/workers", payload))
        if name == "local_worker_status":
            return tool_result(guardian_request("GET", f"/__guardian/jobs/{args.get('job_id', '')}"))
        if name == "local_worker_cancel":
            return tool_result(guardian_request("POST", f"/__guardian/jobs/{args.get('job_id', '')}/cancel", {}))
        return tool_result({"error": f"Unknown tool: {name}"}, is_error=True)
    except RuntimeError as exc:
        return tool_result({"error": str(exc)}, is_error=True)


def response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=("grok", "hermes", "pi", "deepseek-harness", "codex", "claude"))
    options = parser.parse_args()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            response(
                request_id,
                {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "guardian-local-worker", "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            response(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params", {})
            response(request_id, call_tool(params.get("name", ""), params.get("arguments"), options.source))
        elif method == "ping":
            response(request_id, {})
        else:
            response(request_id, {"error": {"code": -32601, "message": f"Method not found: {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
