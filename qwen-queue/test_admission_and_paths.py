"""Step 11: Hermes is admission control only, and arrivals are counted by path."""

from __future__ import annotations

import json
import unittest

from dispatch_paths import DispatchPathCounter
from guardian_queue import ADMISSION_KEYS, HermesDecisionError, admission_only, parse_hermes_decision
from guardian_http_harness import GuardianHarnessCase
from test_task_api import TaskHttpBase, payload

REQ = {"messages": [{"role": "user", "content": "hi"}]}
WB = "workbench-local"
CLERK = "nemotron-r9700"
# What callers put in request.model, mapped to the registry model that must serve it.
ALIAS_TO_MODEL = {"local-llm": WB, "nemotron-3.5-lightning-30b-a3b": CLERK}


class StaticDecider:
    def __init__(self, decision: dict) -> None:
        self.decision, self.calls = decision, 0

    async def decide(self, context, queue):
        self.calls += 1
        return dict(self.decision)


class BrokenDecider:
    calls = 0

    async def decide(self, context, queue):
        BrokenDecider.calls += 1
        raise HermesDecisionError("hermes is down")


class ParseTests(unittest.TestCase):
    def test_only_admit_and_reject_are_accepted_from_hermes(self) -> None:
        self.assertEqual(parse_hermes_decision('{"route":"queue_local","reason":"ok","priority":5}')["route"], "queue_local")
        self.assertEqual(parse_hermes_decision('{"route":"bypass","reason":"no","priority":5}')["route"], "bypass")
        for route in ("fallback_cloud", "queue_clerk", "deepseek", ""):
            with self.subTest(route), self.assertRaises(HermesDecisionError):
                parse_hermes_decision(json.dumps({"route": route, "reason": "x", "priority": 5}))

    def test_extra_keys_never_survive_parsing_or_filtering(self) -> None:
        parsed = parse_hermes_decision('{"route":"queue_local","reason":"ok","priority":5,"model":"deepseek-flash","seat":"consult"}')
        self.assertEqual(set(parsed), set(ADMISSION_KEYS))
        self.assertEqual(set(admission_only({"route": "queue_local", "reason": "r", "priority": 1, "model": "x", "provider": "y"})), set(ADMISSION_KEYS))


class DeciderCannotChooseTheModel(GuardianHarnessCase):
    ENV = {"HERMES_DECIDER_ENABLED": "true", "GUARDIAN_PER_MODEL_QUEUES": "true"}

    async def submit(self, decision: dict, *, model: str, key: str):
        self.module.guardian.hermes_decider = StaticDecider(decision)
        body = {"idempotency_key": key, "source": "t", "decision_context": {"summary": "s"}, "request": {"model": model, **REQ}}
        return await self.client.post("/__guardian/jobs", json=body)

    async def test_no_decider_output_changes_the_queue_or_model_of_a_job(self) -> None:
        self.module.guardian._llama_up = True
        outputs = [
            {"route": "queue_local", "reason": "ok", "priority": 10},
            {"route": "queue_local", "reason": "ok", "priority": 99, "model": "deepseek-flash", "seat": "consult", "provider": "deepseek"},
            {"route": "queue_qwen", "reason": "legacy", "priority": 50},
        ]
        for alias, expected in ALIAS_TO_MODEL.items():
            for n, decision in enumerate(outputs):
                with self.subTest(model=alias, decision=decision):
                    resp = await self.submit(decision, model=alias, key=f"{alias}-{n}")
                    self.assertEqual(resp.status, 202, await resp.text())
                    job = self.module.guardian.job_store.get((await resp.json())["job_id"])
                    self.assertEqual(job.as_api()["model_id"], expected)
                    self.assertEqual(job.request["model"], alias)
                    for leaked in ("model", "seat", "provider"):
                        self.assertNotIn(leaked, job.decision)

    async def test_a_cloud_verdict_is_rejected_not_passed_on(self) -> None:
        resp = await self.submit({"route": "fallback_cloud", "reason": "go cloud", "priority": 1}, model="local-llm", key="cloud")
        body = await resp.json()
        self.assertEqual((resp.status, body["error"]["code"]), (503, "hermes_decision_unavailable"))
        self.assertEqual(self.module.guardian.job_store.summary()["queued"], 0)

    async def test_a_reject_verdict_queues_nothing(self) -> None:
        resp = await self.submit({"route": "bypass", "reason": "not a coding task", "priority": 1}, model="local-llm", key="no")
        self.assertEqual((await resp.json())["status"], "not_queued")
        self.assertEqual(self.module.guardian.job_store.summary()["queued"], 0)


class DeciderCannotChooseWithFleetOff(GuardianHarnessCase):
    ENV = {"HERMES_DECIDER_ENABLED": "true", "FLEET_ROUTER": "false"}

    async def test_fleet_off_path_also_refuses_a_cloud_verdict(self) -> None:
        self.module.guardian.hermes_decider = StaticDecider({"route": "fallback_cloud", "reason": "x", "priority": 1})
        body = {"idempotency_key": "k", "source": "t", "decision_context": {"summary": "s"}, "request": dict(REQ)}
        resp = await self.client.post("/__guardian/jobs", json=body)
        self.assertEqual((resp.status, (await resp.json())["error"]["code"]), (503, "hermes_decision_unavailable"))


class TaskApiNeverAsksHermes(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true", "HERMES_DECIDER_ENABLED": "true"}

    async def test_task_dispatch_does_not_consult_the_decider(self) -> None:
        BrokenDecider.calls = 0
        self.module.guardian.hermes_decider = BrokenDecider()
        await self.start_worker()
        resp = await self.submit(clearance="pc")
        self.assertEqual(resp.status, 202, await resp.text())
        body = await self.wait_status("t1", "succeeded")
        self.assertEqual(body["evidence"]["chosen_model"], WB)
        self.assertEqual(BrokenDecider.calls, 0)


class CounterTests(unittest.TestCase):
    def test_counts_and_log_line(self) -> None:
        counter = DispatchPathCounter()
        counter.note("direct_named", model="x")
        line = counter.note("direct_named", model="deepseek-flash", remote="127.0.0.1", agent="pi/1.0\nforged")
        self.assertIn("dispatch_path direct_named model=deepseek-flash remote=127.0.0.1", line)
        self.assertIn("direct_named=2 direct_default=0 queue_job=0 task_api=0", line)
        self.assertNotIn("\n", line)
        self.assertEqual(counter.totals["direct_named"], 2)


class PathLoggingTests(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true"}

    async def test_each_arrival_is_counted_by_path_without_changing_behavior(self) -> None:
        self.module.guardian._llama_up = True
        totals = self.module.guardian.dispatch_paths.totals
        await self.client.post("/v1/chat/completions", json={"model": "local-llm", **REQ}, headers={"User-Agent": "pi-test"})
        await self.client.post("/v1/chat/completions", json=dict(REQ))
        await self.client.post("/__guardian/jobs", json={
            "idempotency_key": "q1", "source": "grok", "decision_context": {"summary": "s"}, "request": {"model": "local-llm", **REQ}})
        await self.client.post("/__guardian/tasks", json=payload("t9"))
        self.assertEqual(totals, {"direct_named": 1, "direct_default": 1, "queue_job": 1, "task_api": 1})

    async def test_the_log_line_names_the_caller(self) -> None:
        self.module.guardian._llama_up = True
        with self.assertLogs("guardian", level="INFO") as logs:
            await self.client.post("/v1/chat/completions", json={"model": "local-llm", **REQ}, headers={"User-Agent": "pi-test"})
        line = next(l for l in logs.output if "dispatch_path" in l)
        self.assertIn("direct_named model=local-llm remote=127.0.0.1 agent=pi-test", line)


if __name__ == "__main__":
    unittest.main()
