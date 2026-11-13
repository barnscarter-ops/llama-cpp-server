"""Interactive (website chat) priority: token access, ordering, take-over, victim handling, no leaks."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest import mock

from aiohttp import web

import fleet_router
import guardian_env
import guardian_http_harness as harness
import interactive
import model_registry as mr
import model_slots
from guardian_http_harness import GuardianHarnessCase

CLERK_MODEL = "ornith-r9700"
TOKEN = "chat-token-" + "x" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
REQ = {"model": "clerk", "messages": [{"role": "user", "content": "hi"}]}


class PreemptEnv(unittest.TestCase):
    """Env flags for unit tests; restored afterwards."""

    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"GUARDIAN_INTERACTIVE_TOKEN": TOKEN, "GUARDIAN_INTERACTIVE_PREEMPT": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        interactive.reset()


class SlotOrderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.slots = model_slots.ModelSlots(mr.load_registry())
        self.order: list[str] = []

    async def take(self, tag: str, priority: int, gate: asyncio.Event | None = None) -> None:
        async with self.slots.acquire(CLERK_MODEL, priority=priority):
            self.order.append(tag)
            if gate:
                await gate.wait()

    async def test_interactive_waiter_is_served_before_earlier_default_waiters(self) -> None:
        gate = asyncio.Event()
        first = asyncio.create_task(self.take("first", 0, gate))
        await asyncio.sleep(0)
        waiters = [asyncio.create_task(self.take(tag, prio)) for tag, prio in (("d1", 0), ("d2", 0), ("chat", 10), ("d3", 0))]
        await asyncio.sleep(0.01)
        gate.set()
        await asyncio.wait_for(asyncio.gather(first, *waiters), 2)
        self.assertEqual(["first", "chat", "d1", "d2", "d3"], self.order)


class SlotTakeoverTests(PreemptEnv, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.slots = model_slots.ModelSlots(mr.load_registry())

    async def run_victim(self, holder: model_slots.Holder) -> None:
        async with self.slots.acquire(CLERK_MODEL, holder=holder):
            await asyncio.sleep(30)

    async def test_cancelled_waiter_or_victim_never_leaks_a_slot_or_blocks_a_swap(self) -> None:
        # A preempt signal is raised for the interactive waiter...
        victim = model_slots.Holder("request", "v1", "clerk")
        victim_task = asyncio.create_task(self.run_victim(victim))
        await asyncio.sleep(0.01)
        chat = asyncio.create_task(self._chat())
        await asyncio.sleep(0.01)
        self.assertTrue(victim.preempted.is_set())
        # ...the waiter gives up, then the preempted holder is cancelled: nothing may stay held.
        chat.cancel()
        victim_task.cancel()
        await asyncio.gather(chat, victim_task, return_exceptions=True)
        self.assertEqual(0, self.slots.in_flight(CLERK_MODEL))
        async with self.slots.exclusive("r9700", timeout=1):
            pass
        async with self.slots.acquire(CLERK_MODEL):
            pass

    async def _chat(self) -> None:
        async with self.slots.acquire(CLERK_MODEL, priority=interactive.PRIORITY_INTERACTIVE):
            await asyncio.sleep(0)

    async def test_a_swap_is_never_preempted_and_blocks_take_over(self) -> None:
        victim = model_slots.Holder("request", "v2", "clerk")
        victim_task = asyncio.create_task(self.run_victim(victim))
        await asyncio.sleep(0.01)
        swap_task = asyncio.create_task(self._swap())
        await asyncio.sleep(0.01)  # swap now waits for the victim to drain
        chat = asyncio.create_task(self._chat())
        await asyncio.sleep(0.01)
        self.assertFalse(victim.preempted.is_set())
        victim_task.cancel()
        await asyncio.gather(victim_task, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(swap_task, chat), 2)

    async def _swap(self) -> None:
        async with self.slots.exclusive("r9700", timeout=5):
            await asyncio.sleep(0)


class StreamingAiwa(harness.FakeAiwa):
    """Fake AIWA whose next completions can hang (JSON) or stream forever (SSE) until released."""

    def __init__(self) -> None:
        super().__init__()
        self.block_next = 0
        self.release = asyncio.Event()
        self.auth_seen: list[str | None] = []
        self.stream_dropped = False

    async def chat(self, request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        self.chat_hits.append(payload)
        self.auth_seen.append(request.headers.get("Authorization"))
        if payload.get("stream"):
            resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            try:
                await resp.write(b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n')
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
                    await resp.write(b": keepalive\n\n")
            finally:
                self.stream_dropped = not self.release.is_set()  # left the loop without being released: client went away
            return resp
        if self.block_next:
            self.block_next -= 1
            await self.release.wait()
        return web.json_response(
            {"id": "c", "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        )


class InteractiveHttpBase(GuardianHarnessCase):
    ENV = {
        "GUARDIAN_MODEL_SLOTS": "true",
        "GUARDIAN_INTERACTIVE_TOKEN": TOKEN,
        "GUARDIAN_INTERACTIVE_PREEMPT": "true",
        "GUARDIAN_PREEMPT_MAX_PER_JOB": "3",
        # Preemption tests use the test client as both callers, so the loopback guard is off here.
        "AIWA_PROXY_LOOPBACK_ONLY": "false",
    }

    async def asyncSetUp(self) -> None:
        interactive.reset()
        patcher = mock.patch.object(harness, "FakeAiwa", StreamingAiwa)
        patcher.start()
        self.addCleanup(patcher.stop)
        await super().asyncSetUp()

    async def chat(self, headers: dict | None = None, **extra) -> object:
        return await self.client.post("/v1/chat/completions", json={**REQ, **extra}, headers=headers or {})

    async def health(self) -> dict:
        resp = await self.client.get("/__guardian/health")
        return await resp.json()


class LoopbackExceptionTests(InteractiveHttpBase):
    ENV = {**InteractiveHttpBase.ENV, "AIWA_PROXY_LOOPBACK_ONLY": "true"}

    async def test_remote_caller_needs_the_exact_token_and_token_is_not_forwarded(self) -> None:
        with mock.patch.object(fleet_router, "LOOPBACK_REMOTES", frozenset()):  # make the test client look remote
            for headers in ({}, {"Authorization": "Bearer wrong-" + "x" * 40}, {"Authorization": "Basic " + TOKEN}):
                resp = await self.chat(headers)
                self.assertEqual(403, resp.status)
                self.assertEqual("aiwa_proxy_loopback_only", (await resp.json())["error"]["code"])
            self.assertEqual([], self.aiwa.chat_hits)
            # Interactive callers are clerk-only: a consult alias is served by the clerk and a gate header is ignored.
            resp = await self.chat({**AUTH, "X-Guardian-Gate": "operator"}, model="qwen3.8-27b")
            self.assertEqual(200, resp.status)
            self.assertEqual("clerk", resp.headers["X-Guardian-Seat"])
        self.assertEqual(fleet_router.SERVING_IDS["clerk"], self.aiwa.chat_hits[-1]["model"])
        self.assertEqual([None], self.aiwa.auth_seen)

    async def test_short_token_is_ignored_and_named_in_health(self) -> None:
        short = "s" * 31
        with mock.patch.dict(os.environ, {"GUARDIAN_INTERACTIVE_TOKEN": short}), mock.patch.object(
            fleet_router, "LOOPBACK_REMOTES", frozenset()
        ):
            resp = await self.chat({"Authorization": f"Bearer {short}"})
            self.assertEqual(403, resp.status)
            health = await self.health()
        self.assertFalse(health["interactive"]["enabled"])
        self.assertTrue(any("GUARDIAN_INTERACTIVE_TOKEN" in gap for gap in health["turn_on_gaps"]))


class FlagsUnsetTests(InteractiveHttpBase):
    ENV = {"GUARDIAN_MODEL_SLOTS": "false", "GUARDIAN_INTERACTIVE_TOKEN": "", "GUARDIAN_INTERACTIVE_PREEMPT": "false"}

    async def test_without_the_token_setting_the_header_changes_nothing(self) -> None:
        with mock.patch.object(fleet_router, "LOOPBACK_REMOTES", frozenset()):
            self.assertEqual(403, (await self.chat(AUTH)).status)
        health = await self.health()
        self.assertEqual({"enabled": False, "preempt_enabled": False, "preemptions": 0, "last_preempt_at": None}, health["interactive"])
        self.assertEqual([], health["turn_on_gaps"])
        self.assertEqual([], guardian_env.turn_on_gaps({}))


class TakeoverHttpTests(InteractiveHttpBase):
    async def wait_for_upstream(self, count: int) -> None:
        for _ in range(200):
            if len(self.aiwa.chat_hits) >= count:
                return
            await asyncio.sleep(0.01)
        self.fail("upstream request never arrived")

    async def test_streaming_victim_is_ended_with_an_error_event_and_chat_runs(self) -> None:
        victim = await self.chat(stream=True)
        first = await victim.content.readany()
        self.assertIn(b"he", first)
        chat = await asyncio.wait_for(self.chat(AUTH), 5)
        self.assertEqual(200, chat.status)
        tail = (await asyncio.wait_for(victim.content.read(), 5)).decode()
        self.assertIn("preempted_by_interactive", tail)
        self.assertEqual("preempted_by_interactive", json.loads(tail.split("data: ")[-1])["error"]["code"])
        for _ in range(100):  # the fake notices the closed connection on its next keepalive write
            if self.aiwa.stream_dropped:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.aiwa.stream_dropped)
        self.assertEqual(0, model_slots.get_slots().in_flight(CLERK_MODEL))
        health = await self.health()
        self.assertEqual(1, health["interactive"]["preemptions"])
        self.assertIsNotNone(health["interactive"]["last_preempt_at"])

    async def test_victim_before_headers_gets_503_with_retry_after(self) -> None:
        self.aiwa.block_next = 1
        victim = asyncio.create_task(self.chat())
        await self.wait_for_upstream(1)
        chat = await asyncio.wait_for(self.chat(AUTH), 5)
        self.assertEqual(200, chat.status)
        resp = await asyncio.wait_for(victim, 5)
        self.assertEqual(503, resp.status)
        self.assertEqual("5", resp.headers["Retry-After"])
        self.assertEqual("preempted_by_interactive", (await resp.json())["error"]["code"])
        self.assertEqual("clerk", resp.headers["X-Guardian-Seat"])

    async def test_preemption_off_means_chat_waits_for_the_running_request(self) -> None:
        with mock.patch.dict(os.environ, {"GUARDIAN_INTERACTIVE_PREEMPT": "false"}):
            self.aiwa.block_next = 1
            victim = asyncio.create_task(self.chat())
            await self.wait_for_upstream(1)
            chat = asyncio.create_task(self.chat(AUTH))
            await asyncio.sleep(0.2)
            self.assertFalse(chat.done())
            self.aiwa.release.set()
            self.assertEqual(200, (await asyncio.wait_for(victim, 5)).status)
            self.assertEqual(200, (await asyncio.wait_for(chat, 5)).status)

    async def start_job(self, key: str):
        store = self.module.guardian.job_store
        job, _ = store.submit(
            idempotency_key=key, source="t", priority=50, request=dict(REQ),
            decision={"route": "queue_local"}, model_id=CLERK_MODEL,
        )
        return store, store.claim_next(CLERK_MODEL)

    async def test_queue_job_is_requeued_without_costing_an_attempt_and_the_cap_stops_it(self) -> None:
        with mock.patch.dict(os.environ, {"GUARDIAN_PREEMPT_MAX_PER_JOB": "1"}):
            self.aiwa.block_next = 1
            store, job = await self.start_job("j1")
            self.assertEqual(1, store.get(job.job_id).attempts)
            run = asyncio.create_task(self.module.run_queued_job(job))
            await self.wait_for_upstream(1)
            self.assertEqual(200, (await asyncio.wait_for(self.chat(AUTH), 5)).status)
            await asyncio.wait_for(run, 5)
            requeued = store.get(job.job_id)
            self.assertEqual(("queued", 0), (requeued.status, requeued.attempts))

            # Second run: the job has used its one preemption, so chat waits in line instead.
            self.aiwa.block_next = 1
            job = store.claim_next(CLERK_MODEL)
            run = asyncio.create_task(self.module.run_queued_job(job))
            await self.wait_for_upstream(3)
            chat = asyncio.create_task(self.chat(AUTH))
            await asyncio.sleep(0.2)
            self.assertFalse(chat.done())
            self.assertEqual("running", store.get(job.job_id).status)
            self.aiwa.release.set()
            await asyncio.wait_for(run, 5)
            self.assertEqual(200, (await asyncio.wait_for(chat, 5)).status)
            self.assertEqual("succeeded", store.get(job.job_id).status)
            self.assertEqual(1, (await self.health())["interactive"]["preemptions"])


if __name__ == "__main__":
    unittest.main()
