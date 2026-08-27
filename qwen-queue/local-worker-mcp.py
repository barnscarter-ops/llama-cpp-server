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
import re
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


SOURCE_CHOICES = ("grok", "hermes", "pi", "deepseek-harness", "codex", "claude")
SUPPORTED_PROTOCOL_VERSION = "2025-06-18"
JOB_ID = re.compile(r"^qj_[A-Za-z0-9]{8,64}$")

TOOLS = [
    {
        "name": "local_worker_capabilities",
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


def rpc_error(request_id: Any, code: int, message: str) -> None:
    response_payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(response_payload) + "\n")
    sys.stdout.flush()


def _bounded_arguments(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    allowed = {
        "local_worker_capabilities": set(),
        "local_worker_submit": {
            "work_class", "preference", "quality_floor", "gate", "task", "workspace",
            "parent_run_id", "priority", "timeout_seconds",
        },
        "local_worker_status": {"job_id"},
        "local_worker_cancel": {"job_id"},
    }.get(name)
    if allowed is None:
        raise KeyError(name)
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unsupported tool argument(s): {', '.join(sorted(unknown))}.")
    if name in {"local_worker_status", "local_worker_cancel"}:
        job_id = arguments.get("job_id")
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError("job_id must be a guardian job identifier.")
    if name == "local_worker_submit":
        if not isinstance(arguments.get("work_class"), str) or arguments.get("work_class") not in {
            "mechanical_execution", "tool_execution", "planning", "deep_analysis",
        }:
            raise ValueError("work_class must be an approved guardian work class.")
        for key in ("task", "workspace"):
            if not isinstance(arguments.get(key), str) or not arguments[key].strip():
                raise ValueError(f"{key} is required and must be a non-empty string.")
    return dict(arguments)


def call_tool(name: str, arguments: Any, source: str) -> dict[str, Any]:
    try:
        if source not in SOURCE_CHOICES:
            raise ValueError("source is fixed by the adapter launch configuration.")
        args = _bounded_arguments(name, arguments)
        if name == "local_worker_capabilities":
            capabilities = guardian_request("GET", "/__guardian/workers")
            return tool_result({"source": source, "enabled": capabilities.get("enabled", False), "work_classes": capabilities.get("profiles", [])})
        if name == "local_worker_submit":
            payload = dict(args)
            payload["source"] = source
            payload["idempotency_key"] = payload.get("idempotency_key") or uuid.uuid4().hex
            return tool_result(guardian_request("POST", "/__guardian/workers", payload))
        if name == "local_worker_status":
            return tool_result(guardian_request("GET", f"/__guardian/jobs/{args.get('job_id', '')}"))
        if name == "local_worker_cancel":
            return tool_result(guardian_request("POST", f"/__guardian/jobs/{args.get('job_id', '')}/cancel", {}))
        raise KeyError(name)
    except KeyError:
        return tool_result({"error": f"Unknown tool: {name}"}, is_error=True)
    except (RuntimeError, ValueError) as exc:
        return tool_result({"error": str(exc)}, is_error=True)


def response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=SOURCE_CHOICES)
    options = parser.parse_args()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            rpc_error(None, -32700, "Parse error")
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            rpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
            continue
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            continue
        if method == "initialize":
            params = message.get("params", {})
            if not isinstance(params, dict):
                rpc_error(request_id, -32602, "initialize params must be an object")
                continue
            response(
                request_id,
                {
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "guardian-local-worker", "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            response(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                rpc_error(request_id, -32602, "tools/call requires a tool name")
                continue
            response(request_id, call_tool(params["name"], params.get("arguments", {}), options.source))
        elif method == "ping":
            response(request_id, {})
        else:
            rpc_error(request_id, -32601, f"Method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
