"""Offline protocol contract tests for the guardian MCP adapter."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("local-worker-mcp.py")
spec = importlib.util.spec_from_file_location("local_worker_mcp", MODULE_PATH)
mcp = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mcp)


def test_tools_are_bounded_and_have_no_raw_route_inputs():
    names = {tool["name"] for tool in mcp.TOOLS}
    assert names == {"local_worker_capabilities", "local_worker_submit", "local_worker_status", "local_worker_cancel"}
    serialized = json.dumps(mcp.TOOLS).lower()
    for forbidden in ("profile", "model", "endpoint", "url", "executable", "command", "args", "runner"):
        assert forbidden not in serialized
    submit = next(tool for tool in mcp.TOOLS if tool["name"] == "local_worker_submit")
    assert submit["inputSchema"]["required"] == ["work_class", "task", "workspace"]
    assert submit["inputSchema"]["additionalProperties"] is False


def test_initialize_tools_list_and_errors_are_jsonrpc(monkeypatch):
    monkeypatch.setattr(mcp, "guardian_request", lambda *args, **kwargs: {"enabled": False, "profiles": []})
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), "--source", "grok"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "nope"}),
        "not-json",
    ]) + "\n"))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    mcp.main()
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "guardian-local-worker"
    assert {tool["name"] for tool in replies[1]["result"]["tools"]} == {tool["name"] for tool in mcp.TOOLS}
    assert replies[2] == {"jsonrpc": "2.0", "id": 3, "result": {}}
    assert replies[3]["error"]["code"] == -32601


def test_source_is_fixed_and_submit_payload_is_bounded(monkeypatch):
    captured = {}

    def fake_request(method, path, body=None):
        captured.update(method=method, path=path, body=body)
        return {"job_id": "qj_test1234", "status": "queued"}

    monkeypatch.setattr(mcp, "guardian_request", fake_request)
    result = mcp.call_tool("local_worker_submit", {
        "work_class": "tool_execution", "task": "bounded task", "workspace": r"D:\Workspace\fixture",
    }, "hermes")
    assert result["isError"] is False
    assert captured["body"]["source"] == "hermes"
    assert set(captured["body"]) == {"work_class", "task", "workspace", "source", "idempotency_key"}


@pytest.mark.parametrize("name", ["local_worker_status", "local_worker_cancel"])
def test_job_operations_validate_ids_and_call_guardian(monkeypatch, name):
    captured = {}
    monkeypatch.setattr(mcp, "guardian_request", lambda method, path, body=None: captured.update(method=method, path=path) or {})
    result = mcp.call_tool(name, {"job_id": "qj_abcdef12"}, "grok")
    assert result["isError"] is False
    assert captured["path"].startswith("/__guardian/jobs/qj_abcdef12")
    assert mcp.call_tool(name, {"job_id": "../../etc"}, "grok")["isError"] is True


def test_unknown_and_arbitrary_arguments_are_rejected():
    assert mcp.call_tool("raw_model", {}, "grok")["isError"] is True
    assert mcp.call_tool("local_worker_submit", {
        "work_class": "tool_execution", "task": "x", "workspace": r"D:\Workspace", "model": "raw",
    }, "grok")["isError"] is True
    assert mcp.call_tool("local_worker_capabilities", {"args": []}, "grok")["isError"] is True
