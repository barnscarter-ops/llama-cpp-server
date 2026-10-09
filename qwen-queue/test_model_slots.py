"""Step 2: per-model slots, R9700 lock, swap exclusivity."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

import aiwa_swap
import model_registry as mr
import model_slots
from guardian_http_harness import GuardianHarnessCase

CLERK_ID = "nemotron-3.5-lightning-30b-a3b"
CONSULT_ID = "qwen3.8-27b"


class ModelSlotsUnitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.slots = model_slots.ModelSlots(mr.load_registry())

    async def hold(self, model_id: str, log: list, tag: str, hold_s: float = 0.05) -> None:
        async with self.slots.acquire(model_id):
            log.append(f"start:{tag}")
            await asyncio.sleep(hold_s)
            log.append(f"end:{tag}")

    async def test_same_model_is_strictly_serialized(self) -> None:
        log: list[str] = []
        await asyncio.gather(*(self.hold("nemotron-r9700", log, str(i)) for i in range(3)))
        self.assertEqual(["start:0", "end:0", "start:1", "end:1", "start:2", "end:2"], log)

    async def test_different_hosts_run_in_parallel(self) -> None:
        log: list[str] = []
        await asyncio.gather(
            self.hold("workbench-local", log, "wb"), self.hold("nemotron-r9700", log, "r9")
        )
        self.assertEqual(["start:wb", "start:r9"], log[:2])

    async def test_cancellation_releases_slot(self) -> None:
        log: list[str] = []
        task = asyncio.create_task(self.hold("nemotron-r9700", log, "a", hold_s=10))
        await asyncio.sleep(0.01)
        self.assertEqual(1, self.slots.in_flight("nemotron-r9700"))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(0, self.slots.in_flight("nemotron-r9700"))
        await asyncio.wait_for(self.hold("nemotron-r9700", log, "b", hold_s=0), 1)

    async def test_cancelled_waiter_does_not_leak_a_slot(self) -> None:
        log: list[str] = []
        first = asyncio.create_task(self.hold("nemotron-r9700", log, "a", hold_s=0.1))
        await asyncio.sleep(0.01)
        waiter = asyncio.create_task(self.hold("nemotron-r9700", log, "w"))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await first
        await asyncio.wait_for(self.hold("nemotron-r9700", log, "c", hold_s=0), 1)
        self.assertEqual(0, self.slots.in_flight("nemotron-r9700"))

    async def test_exception_releases_slot(self) -> None:
        with self.assertRaises(RuntimeError):
            async with self.slots.acquire("nemotron-r9700"):
                raise RuntimeError("boom")
        self.assertEqual(0, self.slots.in_flight("nemotron-r9700"))
        await asyncio.wait_for(self.hold("nemotron-r9700", [], "x", hold_s=0), 1)

    async def test_unknown_model_is_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            async with self.slots.acquire("nope"):
                pass

    async def test_exclusive_drains_then_blocks_both_r9700_models_but_not_workbench(self) -> None:
        log: list[str] = []
        running = asyncio.create_task(self.hold("nemotron-r9700", log, "inflight", hold_s=0.1))
        await asyncio.sleep(0.01)

        async def swap() -> None:
            async with self.slots.exclusive("r9700", timeout=2):
                log.append("swap:start")
                await asyncio.sleep(0.1)
                log.append("swap:end")

        swapper = asyncio.create_task(swap())
        await asyncio.sleep(0.01)
        late_clerk = asyncio.create_task(self.hold("nemotron-r9700", log, "late_clerk", hold_s=0))
        late_qwen = asyncio.create_task(self.hold("qwen27b-r9700", log, "late_qwen", hold_s=0))
        await self.hold("workbench-local", log, "wb", hold_s=0)
        self.assertIn("start:wb", log)
        self.assertNotIn("swap:start", log, "swap must wait for in-flight work")
        await asyncio.gather(running, swapper, late_clerk, late_qwen)
        self.assertLess(log.index("end:inflight"), log.index("swap:start"))
        self.assertLess(log.index("swap:end"), log.index("start:late_clerk"))
        self.assertLess(log.index("swap:end"), log.index("start:late_qwen"))

    async def test_exclusive_timeout_does_not_kill_inflight_and_unblocks_new_work(self) -> None:
        log: list[str] = []
        running = asyncio.create_task(self.hold("nemotron-r9700", log, "inflight", hold_s=0.2))
        await asyncio.sleep(0.01)
        with self.assertRaises(model_slots.DrainTimeout):
            async with self.slots.exclusive("r9700", timeout=0.05):
                self.fail("must not enter")
        await running
        self.assertEqual(["start:inflight", "end:inflight"], log)
        self.assertFalse(self.slots.exclusive_held("r9700"))
        await asyncio.wait_for(self.hold("qwen27b-r9700", log, "after", hold_s=0), 1)

    async def test_two_swaps_do_not_overlap(self) -> None:
        log: list[str] = []

        async def swap(tag: str) -> None:
            async with self.slots.exclusive("r9700", timeout=2):
                log.append(f"start:{tag}")
                await asyncio.sleep(0.03)
                log.append(f"end:{tag}")

        await asyncio.gather(swap("a"), swap("b"))
        self.assertEqual(["start:a", "end:a", "start:b", "end:b"], log)


class HttpSlotTests(GuardianHarnessCase):
    ENV = {"GUARDIAN_MODEL_SLOTS": "true"}

    def completion(self, model: str) -> dict:
        return {"model": model, "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    async def post(self, model: str):
        resp = await self.client.post("/v1/chat/completions", json=self.completion(model))
        await resp.read()
        return resp

    async def test_two_simultaneous_r9700_requests_do_not_overlap(self) -> None:
        self.aiwa.delay_s = 0.1
        a, b = await asyncio.gather(self.post(CLERK_ID), self.post(CLERK_ID))
        self.assertEqual((200, 200), (a.status, b.status))
        self.assertEqual(1, self.aiwa.max_in_flight)
        self.assertEqual(2, len(self.aiwa.chat_hits))

    async def test_two_simultaneous_workbench_requests_do_not_overlap(self) -> None:
        self.llama.delay_s = 0.1
        self.module.guardian._llama_up = True
        a, b = await asyncio.gather(self.post("local-llm"), self.post("local-llm"))
        self.assertEqual((200, 200), (a.status, b.status))
        self.assertEqual(1, self.llama.max_in_flight)

    async def test_workbench_and_r9700_still_run_in_parallel(self) -> None:
        self.aiwa.delay_s = 0.1
        self.llama.delay_s = 0.1
        self.module.guardian._llama_up = True
        await asyncio.gather(self.post("local-llm"), self.post(CLERK_ID))
        self.assertEqual(1, self.aiwa.max_in_flight)
        self.assertEqual(1, self.llama.max_in_flight)

    async def test_queued_job_and_direct_call_share_the_r9700_slot(self) -> None:
        self.aiwa.delay_s = 0.1
        job, _ = self.module.guardian.job_store.submit(
            idempotency_key="k1", source="t", priority=50,
            request=self.completion(CLERK_ID), decision={"route": "queue_local"},
        )
        claimed = self.module.guardian.job_store.claim_next()
        runner = asyncio.create_task(self.module.run_queued_job(claimed))
        await asyncio.sleep(0.02)
        resp = await self.post(CLERK_ID)
        await runner
        self.assertEqual(200, resp.status)
        self.assertEqual(1, self.aiwa.max_in_flight)
        self.assertEqual("succeeded", self.module.guardian.job_store.get(job.job_id).status)

    async def test_swap_waits_for_inflight_and_blocks_new_completions(self) -> None:
        self.aiwa.delay_s = 0.15
        wb = Path(self.temp_dir.name) / "WORKBOARD.md"
        wb.write_text("# board\n", encoding="utf-8")
        events = self.aiwa.events

        async def fake_ssh(to: str):
            events.append(f"ssh:{to}")
            self.aiwa.occupant_id = CONSULT_ID
            return 0, ""

        env = {"FLEET_SWAP_OWNER": "true", "WORKBOARD_PATH": str(wb)}
        with mock.patch.dict(os.environ, env), mock.patch.object(aiwa_swap, "run_swap_ssh", fake_ssh), \
                mock.patch.object(aiwa_swap, "SWAP_POLL_S", 0.01), \
                mock.patch("fleet_router.OCCUPANT_TTL_S", 0.01):
            inflight = asyncio.create_task(self.post(CLERK_ID))
            await asyncio.sleep(0.03)
            swap = asyncio.create_task(
                aiwa_swap.perform_swap("consult", self.module.guardian._client, gate="operator")
            )
            await asyncio.sleep(0.03)
            late = asyncio.create_task(self.post(CLERK_ID))
            swap_resp = await swap
            first = await inflight
            late_resp = await late
        self.assertEqual(200, swap_resp.status)
        self.assertEqual(200, first.status)
        self.assertLess(events.index("end:" + CLERK_ID), events.index("ssh:consult"))
        # The late clerk request waited out the swap, found Qwen loaded, and was refused rather than misrouted.
        self.assertEqual(409, late_resp.status)
        self.assertEqual(1, len([e for e in events if e == "start:" + CLERK_ID]))

    async def test_swap_times_out_without_killing_running_request(self) -> None:
        self.aiwa.delay_s = 0.3
        wb = Path(self.temp_dir.name) / "WORKBOARD.md"
        wb.write_text("# board\n", encoding="utf-8")
        env = {"FLEET_SWAP_OWNER": "true", "WORKBOARD_PATH": str(wb)}
        with mock.patch.dict(os.environ, env), mock.patch.object(aiwa_swap, "SWAP_DRAIN_TIMEOUT_S", 0.05):
            inflight = asyncio.create_task(self.post(CLERK_ID))
            await asyncio.sleep(0.03)
            swap_resp = await aiwa_swap.perform_swap("consult", self.module.guardian._client, gate="operator")
            first = await inflight
        self.assertEqual(409, swap_resp.status)
        self.assertEqual(200, first.status)
        self.assertFalse(aiwa_swap.swap_slot.busy)


class SwapErrorMappingTests(GuardianHarnessCase):
    ENV = {"GUARDIAN_MODEL_SLOTS": "true"}

    async def test_ssh_timeout_is_not_reported_as_drain_failure(self) -> None:
        wb = Path(self.temp_dir.name) / "WORKBOARD.md"
        wb.write_text("# board\n", encoding="utf-8")

        async def ssh_times_out(to: str):
            raise asyncio.TimeoutError("ssh stalled")

        env = {"FLEET_SWAP_OWNER": "true", "WORKBOARD_PATH": str(wb)}
        with mock.patch.dict(os.environ, env), mock.patch.object(aiwa_swap, "run_swap_ssh", ssh_times_out):
            with self.assertRaises(asyncio.TimeoutError) as ctx:
                await aiwa_swap.perform_swap("consult", self.module.guardian._client, gate="operator")
        self.assertNotIsInstance(ctx.exception, model_slots.DrainTimeout)
        self.assertFalse(aiwa_swap.swap_slot.busy)
        self.assertFalse(model_slots.get_slots().exclusive_held("r9700"))


class FlagOffTests(GuardianHarnessCase):
    """Default behavior is untouched: the missing R9700 lock is still missing with the flag off."""

    ENV = {"GUARDIAN_MODEL_SLOTS": "false"}

    async def test_flag_off_does_not_serialize_r9700(self) -> None:
        self.aiwa.delay_s = 0.1
        body = {"model": CLERK_ID, "stream": False, "messages": [{"role": "user", "content": "hi"}]}

        async def one():
            r = await self.client.post("/v1/chat/completions", json=body)
            await r.read()

        await asyncio.gather(one(), one())
        self.assertEqual(2, self.aiwa.max_in_flight)

    async def test_flag_off_workbench_still_serialized_by_generation_lock(self) -> None:
        self.llama.delay_s = 0.1
        self.module.guardian._llama_up = True
        body = {"model": "local-llm", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

        async def one():
            r = await self.client.post("/v1/chat/completions", json=body)
            await r.read()

        await asyncio.gather(one(), one())
        self.assertEqual(1, self.llama.max_in_flight)


if __name__ == "__main__":
    unittest.main()
