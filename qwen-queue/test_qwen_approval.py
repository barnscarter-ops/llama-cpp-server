"""Step 5: Nemotron rests, Qwen loads only on an approved task, Nemotron reloads after."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from pathlib import Path
from unittest import mock

import aiwa_swap
import fleet_router
import model_slots
import qwen_session
from fleet_router import SERVING_IDS
from guardian_http_harness import GuardianHarnessCase
from qwen_approvals import ApprovalError, ApprovalStore

CLERK = SERVING_IDS["clerk"]
CONSULT = SERVING_IDS["consult"]


class QwenCase(GuardianHarnessCase):
    DECIDER = {"Authorization": "Bearer decider-secret"}
    ENV = {"GUARDIAN_QWEN_APPROVAL": "true", "FLEET_SWAP_OWNER": "true", "GUARDIAN_QWEN_APPROVAL_TOKEN": "decider-secret"}
    swap_delay = 0.0

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        workboard = Path(self.temp_dir.name) / "WORKBOARD.md"
        workboard.write_text("# board\n", encoding="utf-8")
        self.swap_fail: set[str] = set()
        self.ssh_calls: list[str] = []

        async def fake_ssh(to: str):
            self.ssh_calls.append(to)
            self.aiwa.events.append(f"load:{to}")
            await asyncio.sleep(self.swap_delay)
            if to in self.swap_fail:
                return 1, "ssh: connect to host: Connection refused"
            self.aiwa.occupant_id = SERVING_IDS[to]
            return 0, ""

        os.environ["WORKBOARD_PATH"] = str(workboard)
        self.addCleanup(os.environ.pop, "WORKBOARD_PATH", None)
        patches = [
            mock.patch.object(aiwa_swap, "run_swap_ssh", fake_ssh),
            mock.patch.object(aiwa_swap, "SWAP_POLL_S", 0.01),
            mock.patch.object(fleet_router, "OCCUPANT_TTL_S", 0.01),
            mock.patch.object(qwen_session, "RELOAD_RETRY_S", 0.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        qwen_session.reset_state()
        self.addCleanup(qwen_session.reset_state)

    @property
    def approvals(self) -> ApprovalStore:
        return self.module.guardian.approvals

    def completion(self, model: str, stream: bool = False) -> dict:
        return {"model": model, "stream": stream, "messages": [{"role": "user", "content": "hi"}]}

    async def post(self, model: str, headers: dict | None = None, stream: bool = False):
        headers = {"X-Guardian-Task-Id": "t1", **(headers or {})}
        resp = await self.client.post("/v1/chat/completions", json=self.completion(model, stream), headers=headers)
        await resp.read()
        return resp

    def approve(self, task_id: str = "t1") -> str:
        record = self.approvals.create_pending(task_id)
        self.approvals.decide(record["id"], True, "carter")
        return record["id"]

    async def settled(self) -> None:
        """The reload runs after the response ends, so wait for the session to close."""
        await self.until(lambda: not qwen_session.state.session_active)

    async def until(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("condition not reached")


class DirectRequestTests(QwenCase):
    async def test_unapproved_qwen_request_is_409_with_approval_id_and_nothing_swaps(self) -> None:
        resp = await self.post(CONSULT, headers={"X-Guardian-Task-Id": "task-9"})
        body = await resp.json()
        self.assertEqual(409, resp.status)
        self.assertEqual("needs_approval", body["error"]["code"])
        self.assertTrue(body["approval_id"].startswith("qa_"))
        self.assertEqual("pending", body["approval_status"])
        self.assertEqual([], self.ssh_calls)
        self.assertEqual([], self.aiwa.chat_hits)
        self.assertEqual(CLERK, self.aiwa.occupant_id)

    async def test_retry_for_the_same_task_gets_the_same_approval(self) -> None:
        first = await (await self.post(CONSULT, headers={"X-Guardian-Task-Id": "task-9"})).json()
        again = await (await self.post(CONSULT, headers={"X-Guardian-Task-Id": "task-9"})).json()
        self.assertEqual(first["approval_id"], again["approval_id"])

    async def test_string_gate_no_longer_authorizes_qwen(self) -> None:
        for headers in ({"X-Guardian-Gate": "operator"}, {"X-Guardian-Gate": "frontier"}):
            resp = await self.post(CONSULT, headers=headers)
            self.assertEqual(409, resp.status)
        resp = await self.client.post("/__guardian/swap", json={"to": "consult", "gate": "operator"})
        self.assertEqual(403, resp.status)
        self.assertEqual("consult_approval_required", (await resp.json())["error"]["code"])
        self.assertEqual([], self.ssh_calls)

    async def test_pending_approval_is_not_enough(self) -> None:
        record = self.approvals.create_pending("t1")
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": record["id"]})
        self.assertEqual("needs_approval", (await resp.json())["error"]["code"])
        self.assertEqual([], self.ssh_calls)

    async def test_denied_approval_is_403(self) -> None:
        record = self.approvals.create_pending("t1")
        self.approvals.decide(record["id"], False, "carter")
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": record["id"]})
        self.assertEqual(403, resp.status)
        self.assertEqual("approval_denied", (await resp.json())["error"]["code"])
        self.assertEqual([], self.ssh_calls)

    async def test_unknown_approval_id_is_404(self) -> None:
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": "qa_nope"})
        self.assertEqual(404, resp.status)

    async def test_approved_run_order_is_drain_load_run_reload(self) -> None:
        self.aiwa.delay_by_model = {CLERK: 0.2}
        approval_id = self.approve()
        nemotron = asyncio.create_task(self.post(CLERK))
        await self.until(lambda: self.aiwa.in_flight == 1)
        qwen = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        first = await nemotron
        await self.settled()
        self.assertEqual(200, qwen.status)
        self.assertEqual(200, first.status, "the running Nemotron task must not be killed")
        self.assertEqual(
            [f"start:{CLERK}", f"end:{CLERK}", "load:consult", f"start:{CONSULT}", f"end:{CONSULT}", "load:clerk"],
            self.aiwa.events,
        )
        self.assertEqual(CLERK, self.aiwa.occupant_id)
        self.assertEqual("used", self.approvals.get(approval_id)["status"])
        self.assertFalse(qwen_session.state.session_active)

    async def test_approval_cannot_be_spent_by_another_task(self) -> None:
        approval_id = self.approve("t1")
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id, "X-Guardian-Task-Id": "intruder"})
        self.assertEqual(403, resp.status)
        body = await resp.json()
        self.assertEqual("approval_wrong_task", body["error"]["code"])
        self.assertNotIn("approval_id", body)
        self.assertEqual([], self.ssh_calls)
        self.assertEqual("approved", self.approvals.get(approval_id)["status"], "the owner can still use it")

    async def test_approval_cannot_be_used_twice(self) -> None:
        approval_id = self.approve()
        ok = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        await self.settled()
        again = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        self.assertEqual(200, ok.status)
        self.assertEqual(409, again.status)
        self.assertEqual("approval_used", (await again.json())["error"]["code"])
        self.assertEqual(["consult", "clerk"], self.ssh_calls)

    async def test_expired_approval_is_rejected(self) -> None:
        record = self.approvals.create_pending("t1", ttl_s=0.05)
        self.approvals.decide(record["id"], True, "carter")
        await asyncio.sleep(0.08)
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": record["id"]})
        self.assertEqual("approval_expired", (await resp.json())["error"]["code"])
        self.assertEqual([], self.ssh_calls)

    async def test_failed_qwen_task_still_reloads_nemotron(self) -> None:
        self.aiwa.fail_models = {CONSULT}
        approval_id = self.approve()
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        await self.settled()
        self.assertEqual(500, resp.status)
        self.assertEqual(["consult", "clerk"], self.ssh_calls)
        self.assertEqual(CLERK, self.aiwa.occupant_id)

    async def test_qwen_load_failure_reloads_nemotron_and_reports(self) -> None:
        self.swap_fail = {"consult"}
        approval_id = self.approve()
        resp = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        await self.settled()
        self.assertGreaterEqual(resp.status, 400)
        self.assertEqual(CLERK, self.aiwa.occupant_id)
        self.assertEqual([], self.aiwa.chat_hits)

    async def test_clerk_request_during_qwen_session_waits_then_is_served_by_nemotron(self) -> None:
        self.aiwa.delay_by_model = {CONSULT: 0.2}
        approval_id = self.approve()
        qwen = asyncio.create_task(self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id}))
        await self.until(lambda: self.aiwa.in_flight == 1)
        clerk = await self.post(CLERK)
        await qwen
        await self.settled()
        self.assertEqual(200, clerk.status)
        self.assertLess(self.aiwa.events.index("load:clerk"), self.aiwa.events.index(f"start:{CLERK}"))

    async def test_drain_timeout_leaves_running_task_and_approval_untouched(self) -> None:
        self.aiwa.delay_by_model = {CLERK: 0.3}
        approval_id = self.approve()
        with mock.patch.object(aiwa_swap, "SWAP_DRAIN_TIMEOUT_S", 0.05):
            nemotron = asyncio.create_task(self.post(CLERK))
            await self.until(lambda: self.aiwa.in_flight == 1)
            qwen = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
            first = await nemotron
        self.assertEqual(409, qwen.status)
        self.assertEqual("aiwa_busy", (await qwen.json())["error"]["code"])
        self.assertEqual(200, first.status)
        self.assertEqual([], self.ssh_calls)
        self.assertEqual("approved", self.approvals.get(approval_id)["status"], "a retry may still use it")
        retry = await self.post(CONSULT, headers={"X-Guardian-Approval-Id": approval_id})
        await self.settled()
        self.assertEqual(200, retry.status)


class SessionUnitTests(QwenCase):
    async def test_cancel_during_qwen_run_still_reloads_nemotron(self) -> None:
        approval_id = self.approve()
        started = asyncio.Event()

        async def run():
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(
            qwen_session.run_approved_qwen(
                approvals=self.approvals, approval_id=approval_id, task_id="t1", client=self.module.guardian._client, run=run
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(["consult", "clerk"], self.ssh_calls)
        self.assertEqual(CLERK, self.aiwa.occupant_id)
        self.assertFalse(model_slots.get_slots().exclusive_held("r9700"))

    async def test_second_cancel_during_reload_keeps_the_lock_until_it_finishes(self) -> None:
        approval_id = self.approve()
        started = asyncio.Event()

        async def run():
            started.set()
            await asyncio.sleep(30)

        task = asyncio.create_task(
            qwen_session.run_approved_qwen(
                approvals=self.approvals, approval_id=approval_id, task_id="t1",
                client=self.module.guardian._client, run=run,
            )
        )
        await started.wait()
        type(self).swap_delay = 0.3  # make the Nemotron reload slow enough to cancel into
        self.addCleanup(setattr, type(self), "swap_delay", 0.0)
        task.cancel()
        await self.until(lambda: self.ssh_calls == ["consult", "clerk"])  # reload in flight
        task.cancel()
        await asyncio.sleep(0.05)
        self.assertTrue(model_slots.get_slots().exclusive_held("r9700"), "lock released while reload still running")
        self.assertTrue(qwen_session.state.session_active)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(CLERK, self.aiwa.occupant_id)
        self.assertFalse(model_slots.get_slots().exclusive_held("r9700"))
        self.assertFalse(qwen_session.state.session_active)

    async def test_failed_reload_is_reported_on_health_and_retried_until_it_works(self) -> None:
        approval_id = self.approve()
        self.swap_fail = {"clerk"}

        async def run():
            return "ok"

        await qwen_session.run_approved_qwen(
            approvals=self.approvals, approval_id=approval_id, task_id="t1",
            client=self.module.guardian._client, run=run,
        )
        health = await (await self.client.get("/__guardian/health")).json()
        self.assertTrue(health["nemotron_reload_failed"])
        self.assertIsNotNone(health["nemotron_reload_failed_since"])
        self.assertEqual(CONSULT, self.aiwa.occupant_id)

        self.assertFalse(await qwen_session.retry_failed_reload(self.module.guardian._client))  # still failing
        self.assertTrue(qwen_session.state.reload_failed)
        self.swap_fail = set()
        retrier = asyncio.create_task(qwen_session.reload_retrier(lambda: self.module.guardian._client, 0.01))
        self.addCleanup(retrier.cancel)
        await self.until(lambda: not qwen_session.state.reload_failed)
        self.assertEqual(CLERK, self.aiwa.occupant_id)
        health = await (await self.client.get("/__guardian/health")).json()
        self.assertFalse(health["nemotron_reload_failed"])
        self.assertGreaterEqual(health["nemotron_reload_retries"], 2)

    async def test_retry_does_nothing_when_no_reload_is_outstanding_or_a_session_runs(self) -> None:
        self.assertFalse(await qwen_session.retry_failed_reload(self.module.guardian._client))
        qwen_session.state.reload_failed = True
        qwen_session.state.session_active = True
        self.assertFalse(await qwen_session.retry_failed_reload(self.module.guardian._client))
        self.assertEqual([], self.ssh_calls)

    async def test_exception_in_run_reloads_nemotron_and_propagates(self) -> None:
        approval_id = self.approve()

        async def run():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await qwen_session.run_approved_qwen(
                approvals=self.approvals, approval_id=approval_id, task_id="t1", client=self.module.guardian._client, run=run
            )
        self.assertEqual(["consult", "clerk"], self.ssh_calls)

    async def test_reload_failure_is_retried_then_flagged(self) -> None:
        approval_id = self.approve()
        self.swap_fail = {"clerk"}

        async def run():
            return "ok"

        result = await qwen_session.run_approved_qwen(
            approvals=self.approvals, approval_id=approval_id, task_id="t1", client=self.module.guardian._client, run=run
        )
        self.assertEqual("ok", result)
        self.assertEqual(["consult"] + ["clerk"] * qwen_session.RELOAD_ATTEMPTS, self.ssh_calls)
        self.assertTrue(qwen_session.state.reload_failed)

    async def test_startup_recovery_reloads_nemotron_when_qwen_is_loaded(self) -> None:
        self.aiwa.occupant_id = CONSULT
        self.assertTrue(await qwen_session.recover_resting_model(self.module.guardian._client))
        self.assertEqual(["clerk"], self.ssh_calls)
        self.assertEqual(CLERK, self.aiwa.occupant_id)

    async def test_startup_recovery_leaves_nemotron_alone(self) -> None:
        self.assertFalse(await qwen_session.recover_resting_model(self.module.guardian._client))
        self.assertEqual([], self.ssh_calls)

    async def test_startup_recovery_skips_while_a_session_is_running(self) -> None:
        self.aiwa.occupant_id = CONSULT
        qwen_session.state.session_active = True
        self.assertFalse(await qwen_session.recover_resting_model(self.module.guardian._client))
        self.assertEqual([], self.ssh_calls)

    async def test_startup_recovery_skips_when_aiwa_unreachable(self) -> None:
        self.aiwa.models_status = 503
        self.assertFalse(await qwen_session.recover_resting_model(self.module.guardian._client))
        self.assertEqual([], self.ssh_calls)


class QueuedJobTests(QwenCase):
    ENV = {**QwenCase.ENV, "GUARDIAN_PER_MODEL_QUEUES": "true"}

    def job_body(self, key: str, model: str, approval_id: str | None = None) -> dict:
        body = {
            "source": "t", "idempotency_key": key, "decision_context": {"summary": "s"},
            "request": {"model": model, "messages": [{"role": "user", "content": "hi"}]},
        }
        if approval_id:
            body["approval_id"] = approval_id
        return body

    async def start_worker(self) -> None:
        task = asyncio.create_task(self.module.queue_worker(None))

        async def stop() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)

    async def wait_done(self, job_id: str) -> dict:
        for _ in range(600):
            job = self.module.guardian.job_store.get(job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                return job.as_api()
            await asyncio.sleep(0.01)
        self.fail("job did not finish")

    async def test_consult_job_without_approval_is_refused_and_creates_a_pending_one(self) -> None:
        resp = await self.client.post("/__guardian/jobs", json=self.job_body("k1", "qwen3.8-27b"))
        body = await resp.json()
        self.assertEqual((409, "needs_approval"), (resp.status, body["error"]["code"]))
        pending = (await (await self.client.get("/__guardian/approvals?status=pending")).json())["approvals"]
        self.assertEqual([body["approval_id"]], [a["id"] for a in pending])
        self.assertEqual("k1", pending[0]["task_id"])
        self.assertEqual(0, self.module.guardian.job_store.summary()["queued"])

    async def test_approved_consult_job_runs_as_a_session_and_nemotron_resumes_after(self) -> None:
        await self.start_worker()
        self.aiwa.delay_by_model = {CONSULT: 0.15}
        first = await self.client.post("/__guardian/jobs", json=self.job_body("k1", "qwen3.8-27b"))
        approval_id = (await first.json())["approval_id"]
        decided = await self.client.post(
            f"/__guardian/approvals/{approval_id}/decide", json={"decision": "approve", "decided_by": "carter"},
            headers=self.DECIDER,
        )
        self.assertEqual("approved", (await decided.json())["status"])
        qwen_job = await self.client.post("/__guardian/jobs", json=self.job_body("k1", "qwen3.8-27b", approval_id))
        self.assertEqual(202, qwen_job.status, await qwen_job.text())
        qwen_id = (await qwen_job.json())["job_id"]
        await self.until(lambda: self.module.guardian.job_store.get(qwen_id).status == "running")
        nemo = await self.client.post("/__guardian/jobs", json=self.job_body("k2", "clerk"))
        nemo_id = (await nemo.json())["job_id"]
        self.assertEqual("succeeded", (await self.wait_done(qwen_id))["status"])
        self.assertEqual("succeeded", (await self.wait_done(nemo_id))["status"])
        events = self.aiwa.events
        self.assertLess(events.index("load:consult"), events.index(f"start:{CONSULT}"))
        self.assertLess(events.index(f"end:{CONSULT}"), events.index("load:clerk"))
        self.assertLess(events.index("load:clerk"), events.index(f"start:{CLERK}"), "queued Nemotron work waited")
        self.assertEqual("used", self.approvals.get(approval_id)["status"])

    async def test_failed_consult_job_reloads_nemotron(self) -> None:
        await self.start_worker()
        self.aiwa.fail_models = {CONSULT}
        approval_id = self.approve("k9")
        resp = await self.client.post("/__guardian/jobs", json=self.job_body("k9", "qwen3.8-27b", approval_id))
        job = await self.wait_done((await resp.json())["job_id"])
        await self.settled()
        self.assertEqual("failed", job["status"])
        self.assertEqual(["consult", "clerk"], self.ssh_calls)

    async def test_consult_job_whose_approval_was_used_elsewhere_fails_cleanly(self) -> None:
        await self.start_worker()
        approval_id = self.approve("k5")
        resp = await self.client.post("/__guardian/jobs", json=self.job_body("k5", "qwen3.8-27b", approval_id))
        self.assertEqual(202, resp.status)
        job = await self.wait_done((await resp.json())["job_id"])
        self.assertEqual("succeeded", job["status"])
        replay = await self.client.post("/__guardian/jobs", json=self.job_body("k5b", "qwen3.8-27b", approval_id))
        self.assertEqual(403, replay.status, "another task cannot spend this approval")
        self.assertEqual("approval_wrong_task", (await replay.json())["error"]["code"])
        self.assertNotIn("approval_id", await replay.json(), "must not echo the other task's record")


class ApprovalEndpointTests(QwenCase):
    async def decide(self, approval_id: str, **body):
        return await self.client.post(f"/__guardian/approvals/{approval_id}/decide", json=body, headers=self.DECIDER)

    async def test_decide_validation_and_state_rules(self) -> None:
        record = self.approvals.create_pending("t")
        self.assertEqual(400, (await self.decide(record["id"], decision="maybe", decided_by="c")).status)
        self.assertEqual(400, (await self.decide(record["id"], decision="approve")).status)
        self.assertEqual(404, (await self.decide("qa_nope", decision="approve", decided_by="c")).status)
        ok = await self.decide(record["id"], decision="approve", decided_by="carter")
        body = await ok.json()
        self.assertEqual(("approved", "carter"), (body["status"], body["decided_by"]))
        again = await self.decide(record["id"], decision="deny", decided_by="carter")
        self.assertEqual(409, again.status)
        self.assertEqual("approved", self.approvals.get(record["id"])["status"])

    async def test_deciding_needs_the_approval_token_even_from_loopback(self) -> None:
        record = self.approvals.create_pending("t1")
        url = f"/__guardian/approvals/{record['id']}/decide"
        body = {"decision": "approve", "decided_by": "carter"}
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}):
            resp = await self.client.post(url, json=body, headers=headers)
            self.assertEqual(403, resp.status)
            self.assertEqual("approval_forbidden", (await resp.json())["error"]["code"])
        self.assertEqual("pending", self.approvals.get(record["id"])["status"])
        ok = await self.client.post(url, json=body, headers=self.DECIDER)
        self.assertEqual(200, ok.status)

    async def test_deciding_is_disabled_when_no_token_is_configured(self) -> None:
        record = self.approvals.create_pending("t1")
        self.module.QWEN_APPROVAL_TOKEN = ""
        resp = await self.client.post(
            f"/__guardian/approvals/{record['id']}/decide",
            json={"decision": "approve", "decided_by": "x"}, headers=self.DECIDER,
        )
        self.assertEqual(503, resp.status)
        self.assertEqual("approval_token_not_configured", (await resp.json())["error"]["code"])
        self.assertEqual("pending", self.approvals.get(record["id"])["status"])

    async def test_list_filters_by_status(self) -> None:
        a = self.approvals.create_pending("a")
        b = self.approvals.create_pending("b")
        self.approvals.decide(b["id"], True, "c")
        pending = (await (await self.client.get("/__guardian/approvals?status=pending")).json())["approvals"]
        self.assertEqual([a["id"]], [r["id"] for r in pending])
        everything = (await (await self.client.get("/__guardian/approvals")).json())["approvals"]
        self.assertEqual(2, len(everything))
        self.assertEqual(400, (await self.client.get("/__guardian/approvals?status=bogus")).status)

    async def test_remote_without_token_is_refused(self) -> None:
        original = self.module._queue_authorized
        self.module._queue_authorized = lambda request: False
        try:
            self.assertEqual(403, (await self.client.get("/__guardian/approvals")).status)
            self.assertEqual(403, (await self.decide("qa_x", decision="approve", decided_by="c")).status)
        finally:
            self.module._queue_authorized = original


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        import sqlite3

        self.now = 1000.0
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.store = ApprovalStore(self.conn, clock=lambda: self.now)

    def test_pending_expires_and_cannot_be_decided(self) -> None:
        record = self.store.create_pending("t", ttl_s=10)
        self.now += 11
        self.assertEqual("expired", self.store.get(record["id"])["status"])
        with self.assertRaises(ApprovalError):
            self.store.decide(record["id"], True, "c")

    def test_new_approval_after_expiry_for_same_task(self) -> None:
        first = self.store.create_pending("t", ttl_s=10)
        self.now += 11
        second = self.store.create_pending("t", ttl_s=10)
        self.assertNotEqual(first["id"], second["id"])

    def test_check_and_consume_are_bound_to_their_task(self) -> None:
        record = self.store.create_pending("owner")
        self.store.decide(record["id"], True, "carter")
        for call in (self.store.check_usable, self.store.consume):
            with self.assertRaises(ApprovalError) as ctx:
                call(record["id"], "intruder")
            self.assertEqual(("approval_wrong_task", 403), (ctx.exception.code, ctx.exception.status))
            self.assertIsNone(ctx.exception.approval)
        self.assertEqual("approved", self.store.get(record["id"])["status"])
        self.assertEqual("used", self.store.consume(record["id"], "owner")["status"])

    def test_consume_is_once_only(self) -> None:
        record = self.store.create_pending("t")
        self.store.decide(record["id"], True, "c")
        self.store.consume(record["id"])
        with self.assertRaises(ApprovalError) as ctx:
            self.store.consume(record["id"])
        self.assertEqual("approval_used", ctx.exception.code)

    def test_consume_rejects_unapproved_and_expired(self) -> None:
        pending = self.store.create_pending("a")
        with self.assertRaises(ApprovalError) as ctx:
            self.store.consume(pending["id"])
        self.assertEqual("needs_approval", ctx.exception.code)
        approved = self.store.create_pending("b", ttl_s=5)
        self.store.decide(approved["id"], True, "c")
        self.now += 6
        with self.assertRaises(ApprovalError) as ctx:
            self.store.consume(approved["id"])
        self.assertEqual("approval_expired", ctx.exception.code)


class FlagOffTests(GuardianHarnessCase):
    ENV = {"GUARDIAN_QWEN_APPROVAL": "false", "GUARDIAN_MODEL_SLOTS": "false"}

    async def test_old_downgrade_still_applies_with_flag_off(self) -> None:
        resp = await self.client.post(
            "/v1/chat/completions",
            json={"model": CONSULT, "stream": False, "messages": [{"role": "user", "content": "hi"}]},
        )
        await resp.read()
        self.assertEqual(200, resp.status)
        self.assertEqual(CLERK, self.aiwa.chat_hits[0]["model"], "silent downgrade to Nemotron is unchanged")


if __name__ == "__main__":
    unittest.main()
