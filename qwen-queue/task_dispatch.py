"""Task intake: Chief hands over a task, Guardian picks the model and enqueues it.

Flow: validate -> readiness of every model -> clearance/cost filter -> selector
picks -> readiness of the pick checked again -> enqueue on that model's queue.
A selector only ever sees candidates the filter allowed; a pick outside them is
rejected, whoever made it.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence

from candidates import Candidate, CandidateResult, TaskLimits, build_candidates
from model_registry import (
    NEEDS_APPROVAL, READY, UNAVAILABLE, WAKEABLE, ModelRegistry, ModelSpec, Readiness, ReadinessProbe,
)
from qwen_approvals import ApprovalError, ApprovalStore

COMPLETION_PARAMS = ("max_tokens", "temperature", "top_p", "seed", "stop")
DEFAULT_MAX_TOKENS = 1024
MAX_SUMMARY_CHARS = 12_000


class TaskValidationError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    idempotency_key: str
    summary: str
    messages: list[dict]
    clearance: Any
    cost_ceiling_usd: Any
    quality_floor: int
    priority: int
    params: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.__dict__, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "TaskSpec":
        return cls(**json.loads(text))

    def limits(self) -> TaskLimits:
        # Rough but conservative: a token is rarely under 3 bytes of English/code.
        size = len(json.dumps(self.messages, separators=(",", ":")).encode("utf-8"))
        return TaskLimits(
            input_tokens=math.ceil(size / 3),
            max_output_tokens=int(self.params.get("max_tokens", DEFAULT_MAX_TOKENS)),
        )


def _text(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise TaskValidationError(f"{key} is required and must be at most {limit} characters.")
    return value.strip()


def parse_task(payload: Any) -> TaskSpec:
    if not isinstance(payload, dict):
        raise TaskValidationError("Task body must be a JSON object.")
    if {"model", "models", "seat", "endpoint", "provider"} & payload.keys():
        raise TaskValidationError("A task names no model: Guardian chooses it.")
    task_id = _text(payload, "task_id", 160)
    key = _text(payload, "idempotency_key", 160)
    summary = _text(payload, "summary", MAX_SUMMARY_CHARS)
    messages = payload.get("messages")
    prompt = payload.get("prompt")
    if messages is not None:
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
            raise TaskValidationError("messages must be a non-empty array of message objects.")
    elif isinstance(prompt, str) and prompt.strip():
        messages = [{"role": "user", "content": prompt}]
    else:
        raise TaskValidationError("messages or prompt is required.")
    if payload.get("stream") is True or payload.get("tools") or payload.get("tool_choice"):
        raise TaskValidationError("Tasks are non-streaming and take no model tools.")
    floor = payload.get("quality_floor", 1)
    if isinstance(floor, bool) or not isinstance(floor, int) or not 1 <= floor <= 3:
        raise TaskValidationError("quality_floor must be an integer 1-3.")
    priority = payload.get("priority", 50)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
        raise TaskValidationError("priority must be an integer 0-100.")
    params = {k: payload[k] for k in COMPLETION_PARAMS if k in payload}
    if "max_tokens" in params and (
        isinstance(params["max_tokens"], bool) or not isinstance(params["max_tokens"], int) or params["max_tokens"] < 1
    ):
        raise TaskValidationError("max_tokens must be a positive integer.")
    return TaskSpec(
        task_id=task_id, idempotency_key=key, summary=summary, messages=messages,
        clearance=payload.get("clearance"), cost_ceiling_usd=payload.get("cost_ceiling_usd"),
        quality_floor=floor, priority=priority, params=params,
    )


# ── selectors ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SelectorAnswer:
    model_id: str | None
    reason: str
    raw: Any = None


class Selector(Protocol):
    name: str

    async def choose(self, task: TaskSpec, candidates: Sequence[Candidate]) -> SelectorAnswer: ...


class RulesSelector:
    """Ready local first (cheapest, then best), then ready cloud, then wakeable, approval-gated last."""

    name = "rules"

    async def choose(self, task: TaskSpec, candidates: Sequence[Candidate]) -> SelectorAnswer:
        eligible = [c for c in candidates if c.quality_tier >= task.quality_floor]
        if not eligible:
            return SelectorAnswer(None, "no_candidate_meets_quality_floor")

        def rank(c: Candidate) -> tuple:
            state = c.readiness.state
            stage = 2 if c.needs_approval or state == NEEDS_APPROVAL else (0 if state == READY else 1)
            local_first = 0 if c.cost_estimate_usd == 0 else 1
            return (stage, local_first, c.wake_seconds if stage == 1 else 0, c.cost_estimate_usd, -c.quality_tier)

        best = min(eligible, key=rank)
        stage = rank(best)[0]
        reason = {0: "ready", 1: "wakeable", 2: "needs_approval_nothing_else_qualifies"}[stage]
        return SelectorAnswer(best.model_id, reason)


# ── persistence ─────────────────────────────────────────────────────────────


class TaskStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guardian_tasks (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                job_id TEXT,
                approval_id TEXT,
                spec_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        conn.commit()

    def get(self, task_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM guardian_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def get_by_key(self, key: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM guardian_tasks WHERE idempotency_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def awaiting(self, approval_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM guardian_tasks WHERE status = 'awaiting_approval' AND approval_id = ?", (approval_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def insert(self, spec: TaskSpec, status: str, evidence: dict, *, job_id: str | None = None,
               approval_id: str | None = None, error: str | None = None) -> dict:
        self._conn.execute(
            "INSERT INTO guardian_tasks (task_id, idempotency_key, status, job_id, approval_id, spec_json, "
            "evidence_json, error, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (spec.task_id, spec.idempotency_key, status, job_id, approval_id,
             spec.to_json(), json.dumps(evidence, separators=(",", ":")), error, time.time()),
        )
        self._conn.commit()
        return self.get(spec.task_id)  # type: ignore[return-value]

    def update(self, task_id: str, **fields: Any) -> None:
        allowed = {"status", "job_id", "error", "evidence_json"}
        assert set(fields) <= allowed, fields
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE guardian_tasks SET {sets} WHERE task_id = ?", (*fields.values(), task_id))
        self._conn.commit()


# ── dispatcher ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchEnv:
    """Everything the dispatcher needs from Guardian, injected so it can be tested on its own."""
    registry: Callable[[], ModelRegistry]
    probe: Callable[[], ReadinessProbe]
    store: TaskStore
    approvals: ApprovalStore
    enqueue: Callable[..., Any]  # (request, decision, priority, idempotency_key, model_id) -> QueueJob
    flags: Callable[[], dict[str, bool]]  # qwen_approval, fleet_router, cloud_dispatch
    request_for: Callable[[ModelSpec, TaskSpec], dict]
    notify: Callable[[str | None], None]


def apply_flags(pairs: list[tuple[ModelSpec, Readiness]], flags: dict[str, bool]) -> list[tuple[ModelSpec, Readiness]]:
    """Mark models Guardian cannot actually dispatch to right now, whatever their raw readiness."""
    out = []
    for spec, readiness in pairs:
        reason = None
        if spec.host != "workbench" and spec.locality == "local" and not flags.get("fleet_router"):
            reason = "fleet_router_off"
        elif spec.locality == "cloud" and not flags.get("cloud_dispatch"):
            reason = "cloud_backend_missing"
        elif readiness.state == NEEDS_APPROVAL and not flags.get("qwen_approval"):
            reason = "qwen_approval_disabled"
        out.append((spec, Readiness(UNAVAILABLE, reason=reason) if reason else readiness))
    return out


@dataclass
class Outcome:
    http_status: int
    body: dict = field(default_factory=dict)


class TaskDispatcher:
    def __init__(self, env: DispatchEnv, selector: Selector) -> None:
        self.env, self.selector = env, selector

    async def _candidates(self, task: TaskSpec, exclude: set[str]) -> CandidateResult:
        registry = self.env.registry()
        pairs = apply_flags(await self.env.probe().states(registry), self.env.flags())
        pairs = [(s, r) for s, r in pairs if s.id not in exclude]
        return build_candidates(pairs, task.clearance, task.cost_ceiling_usd, task.limits())

    async def _readiness_now(self, spec: ModelSpec) -> Readiness:
        pair = apply_flags([(spec, await self.env.probe().readiness(spec))], self.env.flags())[0]
        return pair[1]

    async def submit(self, task: TaskSpec) -> Outcome:
        existing = self.env.store.get_by_key(task.idempotency_key)
        if existing:
            return Outcome(200, {"idempotent": True, **self.view(existing)})
        if self.env.store.get(task.task_id):
            return Outcome(409, {"error": {"code": "task_exists", "message": "task_id already used with another idempotency_key."}})

        evidence: dict[str, Any] = {
            "chosen_model": None, "selector": self.selector.name, "candidates_offered": [],
            "selector_answer": None, "readiness_at_pick": None, "readiness_at_dispatch": None,
            "queue_wait_ms": None, "fallback_used": False, "served_model": None, "reselected": False,
        }
        exclude: set[str] = set()
        for attempt in range(2):
            result = await self._candidates(task, exclude)
            evidence["clearance"] = result.clearance
            evidence["clearance_note"] = result.clearance_note
            evidence["excluded"] = [{"id": m, "reason": r} for m, r in result.excluded]
            evidence["candidates_offered"] = [
                {"id": c.model_id, "readiness": c.readiness.as_api(), "cost_estimate_usd": c.cost_estimate_usd}
                for c in result.candidates
            ]
            if not result.candidates:
                return self._fail(task, evidence, "no_candidates")
            answer = await self.selector.choose(task, result.candidates)
            evidence["selector_answer"] = {"model_id": answer.model_id, "reason": answer.reason}
            chosen = next((c for c in result.candidates if c.model_id == answer.model_id), None)
            if chosen is None:
                # Includes a pick the selector invented: only offered ids are dispatchable.
                return self._fail(task, evidence, answer.reason if answer.model_id is None else "pick_not_offered")
            evidence["chosen_model"] = chosen.model_id
            evidence["readiness_at_pick"] = chosen.readiness.as_api()
            spec = self.env.registry().get(chosen.model_id)
            now = await self._readiness_now(spec)  # type: ignore[arg-type]
            evidence["readiness_at_dispatch"] = now.as_api()
            if now.state == UNAVAILABLE:
                if attempt == 0:
                    exclude.add(chosen.model_id)
                    evidence["reselected"] = True
                    continue
                return self._fail(task, evidence, f"chosen_model_unavailable:{now.reason or 'unknown'}")
            return self._dispatch(task, spec, now, evidence)  # type: ignore[arg-type]
        return self._fail(task, evidence, "chosen_model_unavailable")

    def _fail(self, task: TaskSpec, evidence: dict, reason: str) -> Outcome:
        row = self.env.store.insert(task, "selection_failed", evidence, error=reason)
        return Outcome(422, {"status": "selection_failed", "reason": reason, **self.view(row)})

    def _dispatch(self, task: TaskSpec, spec: ModelSpec, readiness: Readiness, evidence: dict) -> Outcome:
        if spec.needs_approval:
            approval = self.env.approvals.create_pending(task.task_id)
            row = self.env.store.insert(task, "awaiting_approval", evidence, approval_id=approval["id"])
            if approval["status"] == "approved":
                return self._enqueue(row, task_spec=task, spec=spec, approval_id=approval["id"])
            return Outcome(202, {"approval_id": approval["id"], **self.view(row)})
        row = self.env.store.insert(task, "queued", evidence)
        return self._enqueue(row, task_spec=task, spec=spec, approval_id=None)

    def _enqueue(self, row: dict, *, task_spec: TaskSpec, spec: ModelSpec, approval_id: str | None) -> Outcome:
        decision = {
            "route": "queue_local", "reason": f"task api: {spec.id}", "priority": task_spec.priority,
            "task_id": task_spec.task_id,
        }
        if approval_id:
            decision["approval_id"] = approval_id
        job, _ = self.env.enqueue(
            request=self.env.request_for(spec, task_spec), decision=decision, priority=task_spec.priority,
            idempotency_key=f"task:{task_spec.task_id}", model_id=spec.id,
        )
        self.env.store.update(task_spec.task_id, status="queued", job_id=job.job_id)
        self.env.notify(spec.id)
        return Outcome(202, self.view(self.env.store.get(task_spec.task_id)))  # type: ignore[arg-type]

    # Called when Carter decides an approval (Chief relays it).
    def on_approval(self, approval: dict) -> None:
        for row in self.env.store.awaiting(approval["id"]):
            if approval["status"] == "approved":
                task = TaskSpec.from_json(row["spec_json"])
                evidence = json.loads(row["evidence_json"])
                spec = self.env.registry().get(evidence["chosen_model"])
                if spec is not None:
                    self._enqueue(row, task_spec=task, spec=spec, approval_id=approval["id"])
                    continue
            self.env.store.update(
                row["task_id"], status="failed", error=f"approval_{approval['status']}"
            )

    def refresh(self, row: dict) -> dict:
        """Lazily close tasks whose approval expired while nobody decided."""
        if row["status"] == "awaiting_approval" and row["approval_id"]:
            approval = self.env.approvals.get(row["approval_id"])
            if approval and approval["status"] in ("expired", "denied", "used"):
                self.env.store.update(row["task_id"], status="failed", error=f"approval_{approval['status']}")
                return self.env.store.get(row["task_id"])  # type: ignore[return-value]
        return row

    def view(self, row: dict, job: Any = None) -> dict:
        row = self.refresh(row)
        evidence = json.loads(row["evidence_json"])
        body: dict[str, Any] = {
            "task_id": row["task_id"], "status": row["status"], "job_id": row["job_id"],
            "approval_id": row["approval_id"], "error": row["error"], "evidence": evidence,
        }
        if job is not None:
            body["status"] = job.status
            body["error"] = job.error
            body["result"] = job.result
            created, started = job.created_at, job.started_at
            evidence["queue_wait_ms"] = round(((started or time.time()) - created) * 1000)
            if job.status == "succeeded":
                evidence["served_model"] = served_model_for(self.env.registry().get(evidence["chosen_model"]))
        return body


def served_model_for(spec: ModelSpec | None) -> str | None:
    from fleet_router import SERVING_IDS

    return SERVING_IDS.get(spec.seat) if spec and spec.seat else (spec.id if spec else None)
