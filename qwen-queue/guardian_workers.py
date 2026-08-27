"""Guardian-owned admission policy for disabled-by-default local workers."""

from __future__ import annotations
import asyncio, json, os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_TASK_CHARS = 24_000
MAX_PARENT_ID_CHARS = 160
MAX_RESULT_BYTES = 1_000_000
WORK_CLASSES = ("mechanical_execution", "tool_execution", "planning", "deep_analysis")
PREFERENCES = ("prefer_local", "require_local")
QUALITY_FLOORS = ("standard", "frontier")
GATES = ("operator", "frontier")

@dataclass(frozen=True)
class WorkerPolicy:
    work_class: str
    route: str
    runner: str
    pi_model: str
    description: str
    skill_path: str | None = None
    consult: bool = False

def workers_enabled() -> bool:
    return os.environ.get("LOCAL_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes"}

def worker_root() -> Path:
    return Path(os.environ.get("LOCAL_WORKER_ROOT", r"D:\Workspace")).expanduser().resolve()

def worker_policies() -> dict[str, WorkerPolicy]:
    skill = os.environ.get("LOCAL_WORKER_EXECUTOR_SKILL", r"D:\Workspace\Active\pi-agents\qwen-executor\skills\executor")
    return {
        "mechanical_execution": WorkerPolicy("mechanical_execution", "aiwa-clerk", "pi", "llamacpp-690/nemotron-3.5-lightning-30b-a3b", "Bounded mechanical execution on the approved AIWA clerk seat."),
        "tool_execution": WorkerPolicy("tool_execution", "workbench-executor", "pi", "llamacpp/local-llm", "Bounded write-capable tool execution on the approved Workbench seat.", skill),
        "planning": WorkerPolicy("planning", "aiwa-consult", "aiwa", "", "Gate-aware planning through the existing AIWA consult capability.", consult=True),
        "deep_analysis": WorkerPolicy("deep_analysis", "aiwa-consult", "aiwa", "", "Gate-aware deep analysis through the existing AIWA consult capability.", consult=True),
    }

def profiles_as_api() -> list[dict[str, str]]:
    return [{"work_class": p.work_class, "description": p.description} for p in worker_policies().values()]

def _required_text(payload: dict[str, Any], key: str, limit: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key} is required and must be at most {limit} characters.")
    return value.strip()

def _workspace_from(payload: dict[str, Any]) -> str:
    value = _required_text(payload, "workspace", 1_000)
    path, root = Path(value).expanduser().resolve(), worker_root()
    try: path.relative_to(root)
    except ValueError as exc: raise ValueError(f"workspace must be inside the configured worker root ({root}).") from exc
    if not path.is_dir(): raise ValueError("workspace must be an existing directory.")
    return str(path)

def validate_worker_submission(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict): raise ValueError("Worker request body must be a JSON object.")
    forbidden = {"profile", "model", "endpoint", "runner", "executable"} & payload.keys()
    if forbidden: raise ValueError("profile, model, endpoint, runner, and executable are guardian-owned and not accepted.")
    work_class = _required_text(payload, "work_class", 80)
    policy = worker_policies().get(work_class)
    if policy is None: raise ValueError(f"Unknown work_class '{work_class}'.")
    preference = payload.get("preference", "prefer_local")
    if preference not in PREFERENCES: raise ValueError("preference must be prefer_local or require_local.")
    quality = payload.get("quality_floor", "standard")
    if quality not in QUALITY_FLOORS: raise ValueError("quality_floor must be standard or frontier.")
    gate = payload.get("gate")
    if gate is not None and gate not in GATES: raise ValueError("gate must be operator or frontier when supplied.")
    if quality == "frontier": return {"admission": "requires_cloud", "work_class": work_class, "quality_floor": quality}
    if policy.consult and gate not in GATES: return {"admission": "gate_required", "work_class": work_class, "gate": gate}
    if policy.consult: return {"admission": "consult_unavailable", "work_class": work_class, "gate": gate}
    key = _required_text(payload, "idempotency_key", 160)
    task = _required_text(payload, "task", MAX_TASK_CHARS)
    parent = payload.get("parent_run_id", "")
    if parent is None: parent = ""
    if not isinstance(parent, str) or len(parent) > MAX_PARENT_ID_CHARS: raise ValueError(f"parent_run_id must be a string of at most {MAX_PARENT_ID_CHARS} characters.")
    source = _required_text(payload, "source", 80)
    priority = payload.get("priority", 50)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100: raise ValueError("priority must be an integer from 0 through 100.")
    timeout = payload.get("timeout_seconds", 1_800)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 30 <= timeout <= 3_600: raise ValueError("timeout_seconds must be an integer from 30 through 3600.")
    return {"admission": "local", "idempotency_key": key, "source": source, "priority": priority, "worker": {"work_class": work_class, "preference": preference, "quality_floor": quality, "gate": gate, "workspace": _workspace_from(payload), "task": task, "parent_run_id": parent.strip(), "timeout_seconds": timeout}}

def is_worker_job(request: dict[str, Any]) -> bool: return isinstance(request.get("_guardian_worker"), dict)

def worker_command(spec: dict[str, Any]) -> tuple[list[str], Path]:
    policy = worker_policies().get(spec.get("work_class"))
    if policy is None or policy.runner != "pi": raise ValueError("This work class is not an executable local worker.")
    workspace = Path(spec["workspace"]).resolve()
    try: workspace.relative_to(worker_root())
    except ValueError as exc: raise ValueError("Worker workspace is outside the configured worker root.") from exc
    if not workspace.is_dir(): raise ValueError("Worker workspace no longer exists.")
    prompt = "You are a guardian-managed local worker. Work only inside the current workspace. Do not spawn subagents, switch models, contact cloud providers, or work outside this workspace. Report completed changes, verification performed, and blockers.\n\nTask:\n" + spec["task"]
    command = [os.environ.get("LOCAL_WORKER_PI_EXE", "pi"), "--model", policy.pi_model, "--mode", "json", "--print", "--no-session", "--no-extensions", "--approve"]
    if policy.skill_path: command.extend(["--skill", policy.skill_path])
    command.append(prompt)
    return command, workspace

def _parse_pi_output(stdout: bytes) -> Any:
    text = stdout.decode(errors="replace").strip()
    if not text: return ""
    try: return json.loads(text)
    except json.JSONDecodeError: return text

async def run_worker(spec: dict[str, Any]) -> dict[str, Any]:
    command, workspace = worker_command(spec)
    timeout = int(spec["timeout_seconds"])
    try: proc = await asyncio.create_subprocess_exec(*command, cwd=str(workspace), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as exc: raise RuntimeError("Pi executable was not found for the configured local worker.") from exc
    try: stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill(); await proc.communicate(); raise RuntimeError(f"Worker timed out after {timeout} seconds.") from exc
    if len(stdout) + len(stderr) > MAX_RESULT_BYTES: raise RuntimeError(f"Worker output exceeded the {MAX_RESULT_BYTES}-byte durable result limit.")
    if proc.returncode != 0:
        detail = (stderr or stdout).decode(errors="replace").strip(); raise RuntimeError(f"Worker exited {proc.returncode}: {detail[:2000]}")
    return {"kind": "local_worker", "work_class": spec["work_class"], "workspace": str(workspace), "parent_run_id": spec.get("parent_run_id") or None, "runner": "pi", "output": _parse_pi_output(stdout), "stderr": stderr.decode(errors="replace").strip() or None}
