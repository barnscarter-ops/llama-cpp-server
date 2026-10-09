"""Step 6: task-in endpoint, RulesSelector, re-check after pick, evidence object."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import unittest
from unittest import mock

import model_registry as mr
import task_dispatch as td
from candidates import Candidate
from guardian_http_harness import GuardianHarnessCase
from test_qwen_approval import QwenCase

CLERK = "nemotron-3.5-lightning-30b-a3b"
CONSULT = "qwen3.8-27b"


def payload(task_id: str = "t1", **over) -> dict:
    body = {
        "task_id": task_id, "idempotency_key": f"key-{task_id}", "summary": "do a thing",
        "prompt": "hello", "clearance": "pc", "cost_ceiling_usd": 0,
    }
    body.update(over)
    return body


class TaskHttpBase(GuardianHarnessCase):
    ENV = {"GUARDIAN_TASK_API": "true"}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.module.guardian._llama_up = True  # Workbench ready

    async def submit(self, **over):
        return await self.client.post("/__guardian/tasks", json=payload(**over))

    async def start_worker(self) -> None:
        task = asyncio.create_task(self.module.queue_worker(None))

        async def stop() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)

    async def wait_status(self, task_id: str, *want: str, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = await (await self.client.get(f"/__guardian/tasks/{task_id}")).json()
            if body["status"] in want:
                return body
            await asyncio.sleep(0.02)
        self.fail(f"task {task_id} never reached {want}; last {body}")


class TaskApiTests(TaskHttpBase):
    async def test_task_without_a_model_is_dispatched_to_the_ready_workbench(self) -> None:
        resp = await self.submit()
        body = await resp.json()
        self.assertEqual(202, resp.status, body)
        self.assertEqual("queued", body["status"])
        job = self.module.guardian.job_store.get(body["job_id"])
        self.assertEqual("workbench-local", job.model_id)
        self.assertEqual("local-llm", job.request["model"])
        self.assertEqual("workbench-local", body["evidence"]["chosen_model"])
        self.assertEqual("rules", body["evidence"]["selector"])

    async def test_a_task_that_names_a_model_is_rejected(self) -> None:
        for extra in ({"model": "clerk"}, {"seat": "glm"}, {"provider": "x"}):
            resp = await self.submit(**extra)
            self.assertEqual(400, resp.status)

    async def test_pc_task_never_lands_on_lan_or_cloud_even_when_workbench_is_down(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        resp = await self.submit(clearance="pc", cost_ceiling_usd=100)
        body = await resp.json()
        self.assertEqual(422, resp.status)
        self.assertEqual(("selection_failed", "no_candidates"), (body["status"], body["reason"]))
        reasons = {e["id"]: e["reason"] for e in body["evidence"]["excluded"]}
        self.assertEqual("egress_exceeds_clearance", reasons["nemotron-r9700"])
        self.assertEqual("egress_exceeds_clearance", reasons["deepseek-flash"])
        self.assertEqual([], self.aiwa.chat_hits)
        self.assertEqual(0, self.module.guardian.job_store.summary()["queued"])

    async def test_lan_task_prefers_ready_r9700_over_waking_the_workbench(self) -> None:
        self.module.guardian._llama_up = False
        self.aiwa.occupant_id = CLERK
        body = await (await self.submit(clearance="lan")).json()
        self.assertEqual("nemotron-r9700", body["evidence"]["chosen_model"])
        self.assertEqual(CLERK, self.module.guardian.job_store.get(body["job_id"]).request["model"])

    async def test_missing_clearance_is_treated_as_pc(self) -> None:
        body = await (await self.client.post(
            "/__guardian/tasks",
            json={k: v for k, v in payload().items() if k not in ("clearance", "cost_ceiling_usd")},
        )).json()
        self.assertEqual("pc", body["evidence"]["clearance"])
        self.assertTrue(body["evidence"]["clearance_note"])

    async def test_evidence_is_complete_and_served_model_arrives_with_the_result(self) -> None:
        await self.start_worker()
        first = await (await self.submit()).json()
        for key in ("chosen_model", "selector", "candidates_offered", "selector_answer", "readiness_at_pick",
                    "readiness_at_dispatch", "queue_wait_ms", "fallback_used", "served_model"):
            self.assertIn(key, first["evidence"])
        done = await self.wait_status("t1", "succeeded")
        ev = done["evidence"]
        self.assertEqual("local-llm", ev["served_model"])
        self.assertIsInstance(ev["queue_wait_ms"], int)
        self.assertFalse(ev["fallback_used"])
        self.assertEqual({"state": "ready"}, ev["readiness_at_dispatch"])
        self.assertEqual("workbench-local", ev["selector_answer"]["model_id"])
        self.assertEqual("glm-ok", done["result"]["response"]["choices"][0]["message"]["content"])

    async def test_idempotency_key_returns_the_same_task_and_one_job(self) -> None:
        a = await (await self.submit()).json()
        resp = await self.submit()
        b = await resp.json()
        self.assertEqual(200, resp.status)
        self.assertTrue(b["idempotent"])
        self.assertEqual(a["job_id"], b["job_id"])
        self.assertEqual(1, self.module.guardian.job_store.summary()["queued"])

    async def test_task_id_reuse_with_another_key_is_409(self) -> None:
        await self.submit()
        resp = await self.client.post("/__guardian/tasks", json=payload(idempotency_key="other"))
        self.assertEqual(409, resp.status)

    async def test_selection_failure_is_stored_and_readable(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        await self.submit()
        body = await (await self.client.get("/__guardian/tasks/t1")).json()
        self.assertEqual("selection_failed", body["status"])
        self.assertEqual("no_candidates", body["error"])

    async def test_quality_floor_can_empty_the_field(self) -> None:
        body = await (await self.submit(quality_floor=3)).json()
        self.assertEqual("selection_failed", body["status"])
        self.assertEqual("no_candidate_meets_quality_floor", body["reason"])

    async def test_cloud_is_never_dispatched_while_the_cloud_flag_is_off(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        self.aiwa.models_status = 503
        with mock.patch.dict("os.environ", {"GUARDIAN_CLOUD": "false", "GUARDIAN_DEEPSEEK_API_KEY": "k"}):
            body = await (await self.submit(clearance="internet", cost_ceiling_usd=5)).json()
        self.assertEqual("selection_failed", body["status"])
        reasons = {e["id"]: e["reason"] for e in body["evidence"]["excluded"]}
        self.assertEqual("unavailable:cloud_disabled", reasons["deepseek-flash"])

    async def test_validation_errors(self) -> None:
        cases = [
            {"task_id": ""}, {"summary": ""}, {"idempotency_key": None}, {"prompt": None},
            {"messages": []}, {"messages": ["x"]}, {"quality_floor": 4}, {"quality_floor": True},
            {"priority": 101}, {"priority": True}, {"stream": True}, {"tools": [{}]}, {"max_tokens": 0},
        ]
        for over in cases:
            with self.subTest(over=over):
                resp = await self.submit(**over)
                self.assertEqual(400, resp.status, over)
        bad = await self.client.post("/__guardian/tasks", data="{nope")
        self.assertEqual(400, bad.status)

    async def test_unknown_task_is_404(self) -> None:
        self.assertEqual(404, (await self.client.get("/__guardian/tasks/nope")).status)


class FlagTests(GuardianHarnessCase):
    async def test_disabled_by_default(self) -> None:
        resp = await self.client.post("/__guardian/tasks", json=payload())
        self.assertEqual(404, resp.status)
        self.assertEqual("task_api_disabled", (await resp.json())["error"]["code"])
        self.assertEqual(404, (await self.client.get("/__guardian/tasks/t1")).status)


class FleetOffTests(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true", "FLEET_ROUTER": "false"}

    async def test_with_the_fleet_router_off_only_the_workbench_is_dispatchable(self) -> None:
        self.aiwa.occupant_id = CLERK
        body = await (await self.submit(clearance="lan")).json()
        self.assertEqual("workbench-local", body["evidence"]["chosen_model"])
        reasons = {e["id"]: e["reason"] for e in body["evidence"]["excluded"]}
        self.assertEqual("unavailable:fleet_router_off", reasons["nemotron-r9700"])


class QwenQuarantineTests(TaskHttpBase):
    async def test_qwen_is_not_offered_when_the_approval_flag_is_off(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        self.aiwa.occupant_id = CLERK
        body = await (await self.submit(clearance="lan", quality_floor=2)).json()
        self.assertEqual("selection_failed", body["status"])
        reasons = {e["id"]: e["reason"] for e in body["evidence"]["excluded"]}
        self.assertEqual("unavailable:qwen_approval_disabled", reasons["qwen27b-r9700"])


class QwenTaskTests(QwenCase):
    ENV = {**QwenCase.ENV, "GUARDIAN_TASK_API": "true"}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}  # Workbench cannot wake

    async def start_worker(self) -> None:
        task = asyncio.create_task(self.module.queue_worker(None))

        async def stop() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)

    async def test_qwen_is_last_resort_waits_for_approval_then_runs_and_reloads(self) -> None:
        await self.start_worker()
        resp = await self.client.post(
            "/__guardian/tasks", json=payload(clearance="lan", quality_floor=2, cost_ceiling_usd=0)
        )
        body = await resp.json()
        self.assertEqual(202, resp.status, body)
        self.assertEqual(("awaiting_approval", "qwen27b-r9700"), (body["status"], body["evidence"]["chosen_model"]))
        self.assertEqual("needs_approval_nothing_else_qualifies", body["evidence"]["selector_answer"]["reason"])
        self.assertIsNone(body["job_id"])
        await asyncio.sleep(0.05)
        self.assertEqual([], self.ssh_calls, "nothing loads before Carter decides")
        decided = await self.client.post(
            f"/__guardian/approvals/{body['approval_id']}/decide", json={"decision": "approve", "decided_by": "carter"}
        )
        self.assertEqual(200, decided.status)
        done = await self.client_wait("t1", "succeeded")
        await self.settled()
        self.assertEqual(["consult", "clerk"], self.ssh_calls)
        self.assertEqual("qwen3.8-27b", done["evidence"]["served_model"])
        self.assertEqual("used", self.approvals.get(body["approval_id"])["status"])

    async def client_wait(self, task_id: str, want: str) -> dict:
        for _ in range(500):
            body = await (await self.client.get(f"/__guardian/tasks/{task_id}")).json()
            if body["status"] == want:
                return body
            await asyncio.sleep(0.02)
        self.fail(f"never reached {want}: {body}")

    async def test_denied_approval_fails_the_task_and_nothing_loads(self) -> None:
        body = await (await self.client.post(
            "/__guardian/tasks", json=payload(clearance="lan", quality_floor=2)
        )).json()
        await self.client.post(
            f"/__guardian/approvals/{body['approval_id']}/decide", json={"decision": "deny", "decided_by": "carter"}
        )
        status = await (await self.client.get("/__guardian/tasks/t1")).json()
        self.assertEqual(("failed", "approval_denied"), (status["status"], status["error"]))
        self.assertEqual([], self.ssh_calls)

    async def test_expired_approval_closes_the_task(self) -> None:
        body = await (await self.client.post(
            "/__guardian/tasks", json=payload(clearance="lan", quality_floor=2)
        )).json()
        self.approvals._conn.execute("UPDATE qwen_approvals SET expires_at = 1 WHERE id = ?", (body["approval_id"],))
        status = await (await self.client.get("/__guardian/tasks/t1")).json()
        self.assertEqual(("failed", "approval_expired"), (status["status"], status["error"]))


# ── dispatcher / selector units (no HTTP) ───────────────────────────────────


def cand(model_id, state=mr.READY, *, cost=0.0, tier=2, wake=0.0, needs_approval=False) -> Candidate:
    return Candidate(
        model_id=model_id, egress="pc", readiness=mr.Readiness(state, seconds=wake or None), wake_seconds=wake,
        cost_estimate_usd=cost, quality_tier=tier, needs_approval=needs_approval, context_tokens=10**6,
    )


def task(**over) -> td.TaskSpec:
    return td.parse_task(payload(**over))


class RulesSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def pick(self, candidates, **over):
        return (await td.RulesSelector().choose(task(**over), candidates)).model_id

    async def test_ready_beats_wakeable(self) -> None:
        self.assertEqual("b", await self.pick([cand("a", mr.WAKEABLE, wake=45), cand("b")]))

    async def test_ready_local_beats_ready_cloud_and_cloud_beats_wakeable(self) -> None:
        self.assertEqual("local", await self.pick([cand("cloud", cost=0.01, tier=3), cand("local")]))
        self.assertEqual("cloud", await self.pick([cand("wake", mr.WAKEABLE, wake=5), cand("cloud", cost=0.01)]))

    async def test_cheapest_then_higher_quality(self) -> None:
        self.assertEqual("hq", await self.pick([cand("lq", tier=1), cand("hq", tier=3)]))
        self.assertEqual("cheap", await self.pick([cand("dear", cost=0.5), cand("cheap", cost=0.1)]))

    async def test_fastest_wake_among_wakeable(self) -> None:
        self.assertEqual("fast", await self.pick([cand("slow", mr.WAKEABLE, wake=60), cand("fast", mr.WAKEABLE, wake=10)]))

    async def test_needs_approval_only_when_nothing_else_qualifies(self) -> None:
        q = cand("qwen", mr.NEEDS_APPROVAL, tier=3, wake=60, needs_approval=True)
        self.assertEqual("wake", await self.pick([q, cand("wake", mr.WAKEABLE, wake=45, tier=1)]))
        self.assertEqual("qwen", await self.pick([q]))

    async def test_quality_floor_filters_before_ranking(self) -> None:
        self.assertEqual("good", await self.pick([cand("weak", tier=1), cand("good", tier=2, cost=0.2)], quality_floor=2))
        self.assertIsNone(await self.pick([cand("weak", tier=1)], quality_floor=2))


class ScriptedProbe:
    """Readiness that changes between the pick and the re-check."""

    def __init__(self, registry, script):
        self.registry, self.script, self.calls = registry, script, []

    async def states(self, registry):
        return [(s, mr.Readiness(mr.READY)) for s in registry]

    async def readiness(self, spec):
        self.calls.append(spec.id)
        state = self.script.get(spec.id, [mr.Readiness(mr.READY)])
        return state.pop(0) if len(state) > 1 else state[0]


class DispatcherUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.registry = mr.load_registry()
        self.jobs: list[dict] = []

    def dispatcher(self, probe, selector=None) -> td.TaskDispatcher:
        from qwen_approvals import ApprovalStore

        def enqueue(*, request, decision, priority, idempotency_key, model_id):
            job = mock.Mock(job_id=f"qj_{len(self.jobs)}")
            self.jobs.append({"request": request, "model_id": model_id, "decision": decision})
            return job, True

        env = td.DispatchEnv(
            registry=lambda: self.registry, probe=lambda: probe, store=td.TaskStore(self.conn),
            approvals=ApprovalStore(self.conn), enqueue=enqueue,
            flags=lambda: {"qwen_approval": True, "fleet_router": True, "cloud_dispatch": True},
            request_for=lambda spec, t: {"model": spec.id, "messages": t.messages},
            notify=lambda model_id: None,
        )
        return td.TaskDispatcher(env, selector or td.RulesSelector())

    async def test_pick_that_goes_down_triggers_exactly_one_reselection(self) -> None:
        down = mr.Readiness(mr.UNAVAILABLE, reason="ram_low")
        probe = ScriptedProbe(self.registry, {"workbench-local": [down]})
        outcome = await self.dispatcher(probe).submit(task(clearance="lan"))
        self.assertEqual(202, outcome.http_status, outcome.body)
        ev = outcome.body["evidence"]
        self.assertTrue(ev["reselected"])
        self.assertEqual("nemotron-r9700", ev["chosen_model"])
        self.assertEqual(1, len(self.jobs))

    async def test_second_failure_is_selection_failed(self) -> None:
        down = mr.Readiness(mr.UNAVAILABLE, reason="gone")
        probe = ScriptedProbe(self.registry, {"workbench-local": [down], "nemotron-r9700": [down], "qwen27b-r9700": [down]})
        outcome = await self.dispatcher(probe).submit(task(clearance="lan"))
        self.assertEqual(422, outcome.http_status)
        self.assertEqual("chosen_model_unavailable:gone", outcome.body["reason"])
        self.assertEqual([], self.jobs)

    async def test_selector_pick_outside_the_offered_list_is_rejected(self) -> None:
        class Rogue:
            name = "rogue"

            async def choose(self, t, candidates):
                return td.SelectorAnswer("deepseek-flash", "trust me")

        outcome = await self.dispatcher(ScriptedProbe(self.registry, {}), Rogue()).submit(task(clearance="pc"))
        self.assertEqual(("selection_failed", "pick_not_offered"), (outcome.body["status"], outcome.body["reason"]))
        self.assertEqual([], self.jobs)

    async def test_selector_only_sees_filtered_candidates(self) -> None:
        seen = []

        class Spy:
            name = "spy"

            async def choose(self, t, candidates):
                seen.extend(c.model_id for c in candidates)
                return td.SelectorAnswer(None, "abstain")

        await self.dispatcher(ScriptedProbe(self.registry, {}), Spy()).submit(task(clearance="pc"))
        self.assertEqual(["workbench-local"], seen)


if __name__ == "__main__":
    unittest.main()
