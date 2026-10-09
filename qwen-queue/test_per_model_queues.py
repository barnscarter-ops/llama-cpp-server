"""Step 3: per-model queues, additive model_id migration, /__guardian/queues."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from guardian_queue import JobStore
from guardian_http_harness import GuardianHarnessCase

CLERK = "nemotron-r9700"
WB = "workbench-local"
REQ = {"messages": [{"role": "user", "content": "hi"}]}


def submit(store: JobStore, key: str, model_id: str | None, priority: int = 50) -> str:
    job, _ = store.submit(
        idempotency_key=key, source="t", priority=priority, request=dict(REQ),
        decision={"route": "queue_local"}, model_id=model_id,
    )
    return job.job_id


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "q.sqlite3")
        self.store = JobStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_claim_is_scoped_to_the_model(self) -> None:
        a = submit(self.store, "a", CLERK)
        b = submit(self.store, "b", WB)
        self.assertEqual(b, self.store.claim_next(WB).job_id)
        self.assertIsNone(self.store.claim_next(WB))
        self.assertEqual(a, self.store.claim_next(CLERK).job_id)

    def test_priority_then_fifo_inside_one_model(self) -> None:
        first = submit(self.store, "1", CLERK, priority=10)
        second = submit(self.store, "2", CLERK, priority=90)
        third = submit(self.store, "3", CLERK, priority=90)
        order = [self.store.claim_next(CLERK).job_id for _ in range(3)]
        self.assertEqual([second, third, first], order)

    def test_legacy_claim_takes_any_model(self) -> None:
        a = submit(self.store, "a", CLERK)
        self.assertEqual(a, self.store.claim_next().job_id)

    def test_has_queued_work_scoped(self) -> None:
        submit(self.store, "a", CLERK)
        self.assertTrue(self.store.has_queued_work(CLERK))
        self.assertFalse(self.store.has_queued_work(WB))
        self.assertTrue(self.store.has_queued_work())

    def test_recovery_keeps_job_in_its_queue(self) -> None:
        job_id = submit(self.store, "a", CLERK)
        self.store.claim_next(CLERK)
        self.store.close()
        self.store = JobStore(self.db)
        self.assertEqual(1, self.store.recover_interrupted())
        self.assertIsNone(self.store.claim_next(WB))
        self.assertEqual(job_id, self.store.claim_next(CLERK).job_id)

    def test_queue_stats(self) -> None:
        submit(self.store, "a", CLERK)
        submit(self.store, "b", CLERK)
        submit(self.store, "c", None)
        running = self.store.claim_next(CLERK)
        stats = self.store.queue_stats()
        self.assertEqual(1, stats[CLERK]["queued"])
        self.assertEqual(1, stats[CLERK]["running"])
        self.assertEqual(running.job_id, stats[CLERK]["running_job_id"])
        self.assertGreaterEqual(stats[CLERK]["oldest_wait_s"], 0)
        self.assertEqual(1, stats["unassigned"]["queued"])
        self.assertNotIn(WB, stats)

    def test_as_api_carries_model_id(self) -> None:
        job_id = submit(self.store, "a", CLERK)
        self.assertEqual(CLERK, self.store.get(job_id).as_api()["model_id"])


class MigrationTests(unittest.TestCase):
    OLD_SCHEMA = """
        CREATE TABLE guardian_jobs (
            job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
            priority INTEGER NOT NULL, request_json TEXT NOT NULL, decision_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
            attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            started_at REAL, finished_at REAL, result_json TEXT, error TEXT)
    """

    def make_old_db(self, path: str) -> None:
        conn = sqlite3.connect(path)
        conn.execute(self.OLD_SCHEMA)
        for i, model in enumerate(("clerk", "local-llm", "mystery")):
            conn.execute(
                "INSERT INTO guardian_jobs VALUES (?,?,?,?,?,?,'queued',0,?,NULL,NULL,NULL,NULL)",
                (f"qj_{i}", f"k{i}", "old", 50, json.dumps({"model": model, **REQ}), "{}", time.time() + i),
            )
        conn.commit()
        conn.close()

    def test_old_schema_opens_adds_column_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.sqlite3")
            self.make_old_db(path)
            for _ in range(2):  # second open must be a no-op
                store = JobStore(path)
                cols = [r["name"] for r in store._conn.execute("PRAGMA table_info(guardian_jobs)")]
                self.assertEqual(1, cols.count("model_id"))
                self.assertEqual(3, store.summary()["queued"])
                store.close()

    def test_backfill_assigns_known_rows_and_leaves_unknown_null(self) -> None:
        resolver = lambda req: {"clerk": CLERK, "local-llm": WB}.get(req.get("model"))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.sqlite3")
            self.make_old_db(path)
            store = JobStore(path)
            self.assertEqual(2, store.backfill_model_ids(resolver))
            self.assertEqual(0, store.backfill_model_ids(resolver))
            self.assertEqual(CLERK, store.get("qj_0").model_id)
            self.assertEqual(WB, store.get("qj_1").model_id)
            self.assertIsNone(store.get("qj_2").model_id)
            store.close()


class QueueHttpBase(GuardianHarnessCase):
    def body(self, key: str, model: str) -> dict:
        return {
            "source": "t", "idempotency_key": key,
            "decision_context": {"summary": "s"},
            "request": {"model": model, **REQ},
        }

    async def start_worker(self) -> None:
        task = asyncio.create_task(self.module.queue_worker(None))

        async def stop() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)

    async def submit(self, key: str, model: str) -> str:
        resp = await self.client.post("/__guardian/jobs", json=self.body(key, model))
        self.assertEqual(202, resp.status, await resp.text())
        return (await resp.json())["job_id"]

    async def wait_done(self, job_id: str, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.module.guardian.job_store.get(job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                return job.as_api()
            await asyncio.sleep(0.01)
        self.fail(f"job {job_id} did not finish")


class PerModelQueueHttpTests(QueueHttpBase):
    ENV = {"GUARDIAN_PER_MODEL_QUEUES": "true"}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.module.guardian._llama_up = True  # Workbench jobs need no cold start

    async def test_slow_r9700_job_does_not_delay_workbench_job(self) -> None:
        self.aiwa.delay_s = 0.4
        await self.start_worker()
        slow = await self.submit("slow", "clerk")
        await asyncio.sleep(0.05)
        fast = await self.submit("fast", "local-llm")
        fast_job = await self.wait_done(fast)
        self.assertEqual("succeeded", fast_job["status"])
        self.assertEqual(WB, fast_job["model_id"])
        self.assertEqual("running", self.module.guardian.job_store.get(slow).status)
        slow_job = await self.wait_done(slow)
        self.assertLess(fast_job["finished_at"], slow_job["started_at"] + 0.4)
        self.assertEqual(CLERK, slow_job["model_id"])

    async def test_same_model_jobs_run_strictly_in_order(self) -> None:
        self.aiwa.delay_s = 0.1
        ids = [await self.submit(f"k{i}", "clerk") for i in range(3)]
        await self.start_worker()
        jobs = [await self.wait_done(i) for i in ids]
        self.assertEqual(1, self.aiwa.max_in_flight)
        for earlier, later in zip(jobs, jobs[1:]):
            self.assertLessEqual(earlier["finished_at"], later["started_at"])

    async def test_restart_requeues_interrupted_job_into_its_own_queue(self) -> None:
        store = self.module.guardian.job_store
        job_id = await self.submit("r", "local-llm")
        self.assertEqual(job_id, store.claim_next(WB).job_id)  # "running" when the guardian died
        await self.start_worker()
        job = await self.wait_done(job_id)
        self.assertEqual("succeeded", job["status"])
        self.assertEqual(WB, job["model_id"])

    async def test_premigration_rows_are_backfilled_and_run(self) -> None:
        store = self.module.guardian.job_store
        legacy, _ = store.submit(
            idempotency_key="legacy", source="old", priority=50,
            request={"model": "local-llm", **REQ}, decision={"route": "queue_local"},
        )
        self.assertIsNone(legacy.model_id)
        await self.start_worker()
        job = await self.wait_done(legacy.job_id)
        self.assertEqual("succeeded", job["status"])
        self.assertEqual(WB, job["model_id"])

    async def test_queues_endpoint(self) -> None:
        self.aiwa.delay_s = 0.3
        await self.start_worker()
        await self.submit("a", "clerk")
        await asyncio.sleep(0.05)
        await self.submit("b", "clerk")
        body = await (await self.client.get("/__guardian/queues")).json()
        self.assertTrue(body["per_model_queues"])
        self.assertEqual(
            ["workbench-local", "nemotron-r9700", "qwen27b-r9700", "deepseek-flash"], list(body["queues"])
        )
        clerk = body["queues"][CLERK]
        self.assertEqual((1, 1), (clerk["queued"], clerk["running"]))
        self.assertIsNotNone(clerk["running_job_id"])
        self.assertGreaterEqual(clerk["oldest_wait_s"], 0)
        self.assertEqual(0, body["queues"][WB]["queued"])


class ConcurrentWorkerCancelTests(QueueHttpBase):
    ENV = {"GUARDIAN_PER_MODEL_QUEUES": "true"}

    def build_routes(self, app) -> None:
        super().build_routes(app)
        app.router.add_post("/__guardian/workers", self.module.worker_submit)
        app.router.add_post("/__guardian/jobs/{job_id}/cancel", self.module.queue_cancel)

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.module.guardian._llama_up = True
        os.environ["LOCAL_WORKER_ENABLED"] = "true"
        os.environ["LOCAL_WORKER_ROOT"] = self.temp_dir.name
        self.addCleanup(os.environ.pop, "LOCAL_WORKER_ENABLED", None)
        self.addCleanup(os.environ.pop, "LOCAL_WORKER_ROOT", None)
        self.running: set[str] = set()

        async def fake_run_worker(spec, cancel_event=None, on_process=None):
            self.running.add(spec["task"])
            try:
                await cancel_event.wait()
            finally:
                self.running.discard(spec["task"])
            raise self.module.WorkerCancelled("cancelled")

        self.module.run_worker = fake_run_worker

    async def submit_worker(self, key: str, work_class: str) -> str:
        resp = await self.client.post("/__guardian/workers", json={
            "source": "t", "idempotency_key": key, "work_class": work_class,
            "workspace": self.temp_dir.name, "task": key,
        })
        self.assertEqual(202, resp.status, await resp.text())
        return (await resp.json())["job_id"]

    async def wait_running(self, job_id: str) -> None:
        for _ in range(200):
            if self.module.guardian.job_store.get(job_id).status == "running":
                return
            await asyncio.sleep(0.01)
        self.fail(f"{job_id} never started")

    async def test_cancelling_either_of_two_concurrent_workers_works(self) -> None:
        await self.start_worker()
        clerk = await self.submit_worker("clerk-task", "mechanical_execution")
        glm = await self.submit_worker("glm-task", "tool_execution")
        await self.wait_running(clerk)
        await self.wait_running(glm)
        self.assertEqual({"clerk-task", "glm-task"}, self.running)

        # The first-started worker used to lose its handles to the second one.
        resp = await self.client.post(f"/__guardian/jobs/{clerk}/cancel")
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("cancelled", (await resp.json())["status"])
        # Finishing the first must not clear the second's handles.
        self.assertEqual("running", self.module.guardian.job_store.get(glm).status)
        resp = await self.client.post(f"/__guardian/jobs/{glm}/cancel")
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("cancelled", (await resp.json())["status"])
        self.assertEqual({}, self.module.guardian.active_workers)


class SingleWorkerStillDefaultTests(QueueHttpBase):
    ENV = {"GUARDIAN_PER_MODEL_QUEUES": "false"}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.module.guardian._llama_up = True

    async def test_flag_off_keeps_one_global_worker(self) -> None:
        self.aiwa.delay_s = 0.3
        await self.start_worker()
        slow = await self.submit("slow", "clerk")
        await asyncio.sleep(0.05)
        fast = await self.submit("fast", "local-llm")
        slow_job = await self.wait_done(slow)
        fast_job = await self.wait_done(fast)
        self.assertGreaterEqual(fast_job["started_at"], slow_job["finished_at"])
        body = await (await self.client.get("/__guardian/queues")).json()
        self.assertFalse(body["per_model_queues"])


if __name__ == "__main__":
    unittest.main()
