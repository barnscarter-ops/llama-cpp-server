"""Step 8: Guardian owns the cloud key, clamps, caps and serves deepseek-flash. Fake provider only."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import unittest

import cloud_backend as cb
import model_registry as mr
from guardian_http_harness import GuardianHarnessCase
from test_task_api import TaskHttpBase, payload

KEY = "sk-cloud-SECRET-789"
REGISTRY = mr.load_registry()
SPEC = REGISTRY.get("deepseek-flash")
MSG = {"messages": [{"role": "user", "content": "hello"}]}


def provider_ok(prompt: int = 1000, completion: int = 500) -> dict:
    return {"id": "ds-1", "choices": [{"message": {"role": "assistant", "content": "cloud-ok"}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


class FakeProvider:
    def __init__(self, *, status: int = 200, body=None, delay: float = 0.0, error: Exception | None = None) -> None:
        self.status, self.body, self.delay, self.error = status, body, delay, error
        self.calls: list[tuple[str, dict, dict]] = []
        self.in_flight = self.max_in_flight = 0
        self.gate: asyncio.Event | None = None

    async def __call__(self, url: str, headers: dict, body: bytes, timeout_s: float):
        self.calls.append((url, dict(headers), json.loads(body)))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.gate:
                await self.gate.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            raw = self.body if self.body is not None else provider_ok()
            return self.status, raw if isinstance(raw, bytes) else json.dumps(raw).encode()
        finally:
            self.in_flight -= 1


def set_env(testcase: unittest.TestCase, **values: str | None) -> None:
    for name, value in values.items():
        old = os.environ.get(name)
        testcase.addCleanup(lambda n=name, o=old: os.environ.pop(n, None) if o is None else os.environ.__setitem__(n, o))
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class BackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        set_env(self, GUARDIAN_CLOUD="true", GUARDIAN_DEEPSEEK_API_KEY=KEY, GUARDIAN_CLOUD_DAILY_CAP_USD=None,
                GUARDIAN_CLOUD_MAX_CONCURRENT=None, GUARDIAN_CLOUD_MAX_OUTPUT_TOKENS=None,
                GUARDIAN_CLOUD_MAX_CALLS_PER_WINDOW=None, GUARDIAN_CLOUD_MAX_REQUEST_BYTES=None)
        self.ledger = cb.CloudSpendLedger(sqlite3.connect(":memory:", isolation_level=None))
        self.now = 1_000_000.0

    def backend(self, provider: FakeProvider, **kw) -> cb.CloudBackend:
        return cb.CloudBackend(REGISTRY, self.ledger, transport=provider, clock=lambda: self.now, **kw)

    async def refused(self, coro) -> cb.CloudRefused:
        with self.assertRaises(cb.CloudRefused) as ctx:
            await coro
        return ctx.exception

    async def test_key_is_attached_to_the_provider_call_and_nowhere_else(self) -> None:
        provider = FakeProvider()
        result = await self.backend(provider).complete(SPEC, dict(MSG), wait=False)
        url, headers, sent = provider.calls[0]
        self.assertEqual((url, headers["Authorization"]), (cb.DEEPSEEK_ENDPOINT, f"Bearer {KEY}"))
        self.assertNotIn(KEY, json.dumps(sent))
        self.assertNotIn(KEY, json.dumps(result.body))
        self.assertEqual(sent["model"], "deepseek-flash")

    async def test_errors_never_echo_the_key_or_provider_text(self) -> None:
        for provider in (FakeProvider(error=ConnectionError(f"boom {KEY}")), FakeProvider(status=401, body=f"bad key {KEY}".encode()),
                         FakeProvider(body=b"not json " + KEY.encode())):
            exc = await self.refused(self.backend(provider).complete(SPEC, dict(MSG), wait=False))
            self.assertNotIn(KEY, f"{exc.code} {exc.message}")

    async def test_output_tokens_are_clamped_and_stream_is_forced_off(self) -> None:
        cases = [({"max_tokens": 1_000_000}, 8192), ({}, 8192), ({"max_tokens": 100}, 100), ({"max_tokens": -5}, 8192),
                 ({"max_tokens": True}, 8192), ({"max_completion_tokens": 999_999}, 8192)]
        for extra, want in cases:
            with self.subTest(extra):
                provider = FakeProvider()
                await self.backend(provider).complete(SPEC, {**MSG, "stream": True, **extra}, wait=False)
                sent = provider.calls[0][2]
                self.assertEqual((sent["max_tokens"], sent["stream"]), (want, False))
                self.assertNotIn("max_completion_tokens", sent)

    async def test_clamp_is_configurable(self) -> None:
        set_env(self, GUARDIAN_CLOUD_MAX_OUTPUT_TOKENS="256")
        provider = FakeProvider()
        await self.backend(provider).complete(SPEC, {**MSG, "max_tokens": 4000}, wait=False)
        self.assertEqual(provider.calls[0][2]["max_tokens"], 256)

    async def test_spend_settles_to_actual_usage(self) -> None:
        result = await self.backend(FakeProvider(body=provider_ok(10_000, 2_000))).complete(SPEC, dict(MSG), wait=False)
        # 10k in at $0.0003/1k + 2k out at $0.0012/1k = $0.0054
        self.assertEqual(result.cost_microusd, 5_400)
        self.assertEqual(self.ledger.spent("deepseek-flash", self.now), 5_400)
        self.assertGreater(result.reserved_microusd, result.cost_microusd)

    async def test_cap_exhaustion_refuses_and_marks_the_model_unavailable(self) -> None:
        set_env(self, GUARDIAN_CLOUD_DAILY_CAP_USD="0.02")
        backend = self.backend(FakeProvider(body=provider_ok(10_000, 2_000)))
        await backend.complete(SPEC, dict(MSG), wait=False)  # 5400 of 20000 micro-USD
        self.assertTrue(backend.state(SPEC).cap_left)
        self.ledger.adjust("deepseek-flash", 10_000, self.now)  # headroom now below one full-length reply
        self.assertFalse(backend.state(SPEC).cap_left)
        provider = FakeProvider()
        exc = await self.refused(self.backend(provider).complete(SPEC, dict(MSG), wait=False))
        self.assertEqual((exc.code, exc.status), ("spend_cap", 429))
        self.assertEqual(provider.calls, [])
        probe = mr.ReadinessProbe(lambda: mr.WorkbenchState(llama_up=True), None, backend.state)  # type: ignore[arg-type]
        self.assertEqual((await probe.readiness(SPEC)).as_api(), {"state": "unavailable", "reason": "spend_cap"})

    async def test_new_utc_day_restores_headroom(self) -> None:
        set_env(self, GUARDIAN_CLOUD_DAILY_CAP_USD="0.02")
        backend = self.backend(FakeProvider(body=provider_ok(1000, 500)))
        self.ledger.reserve("deepseek-flash", 15_000, 10**9, self.now)
        self.assertFalse(backend.state(SPEC).cap_left)
        self.now += 86_400
        self.assertTrue(backend.state(SPEC).cap_left)

    async def test_concurrency_limit_refuses_direct_calls_and_queues_waiting_ones(self) -> None:
        provider = FakeProvider()
        provider.gate = asyncio.Event()
        backend = self.backend(provider)
        first = asyncio.create_task(backend.complete(SPEC, dict(MSG), wait=False))
        second = asyncio.create_task(backend.complete(SPEC, dict(MSG), wait=False))
        await asyncio.sleep(0.05)
        self.assertEqual(backend.in_flight("deepseek-flash"), 2)
        exc = await self.refused(backend.complete(SPEC, dict(MSG), wait=False))
        self.assertEqual(exc.code, "cloud_busy")
        waiter = asyncio.create_task(backend.complete(SPEC, dict(MSG), wait=True))
        await asyncio.sleep(0.05)
        self.assertFalse(waiter.done())
        provider.gate.set()
        await asyncio.gather(first, second, waiter)
        self.assertEqual(provider.max_in_flight, 2)
        self.assertEqual(backend.in_flight("deepseek-flash"), 0)

    async def test_concurrency_is_configurable(self) -> None:
        set_env(self, GUARDIAN_CLOUD_MAX_CONCURRENT="1")
        self.assertEqual(self.backend(FakeProvider()).max_concurrent("deepseek-flash"), 1)

    async def test_call_window_limit(self) -> None:
        set_env(self, GUARDIAN_CLOUD_MAX_CALLS_PER_WINDOW="2")
        backend = self.backend(FakeProvider())
        for _ in range(2):
            await backend.complete(SPEC, dict(MSG), wait=False)
        exc = await self.refused(backend.complete(SPEC, dict(MSG), wait=False))
        self.assertEqual(exc.code, "rate_limited")
        self.now += 3601
        await backend.complete(SPEC, dict(MSG), wait=False)

    async def test_reservation_is_refunded_unless_the_provider_may_have_billed(self) -> None:
        for provider, expect_zero in ((FakeProvider(status=500), True), (FakeProvider(error=ConnectionError("x")), True),
                                      (FakeProvider(error=asyncio.TimeoutError()), False),
                                      (FakeProvider(body={"choices": []}), False)):
            with self.subTest(status=provider.status, error=provider.error):
                self.ledger = cb.CloudSpendLedger(sqlite3.connect(":memory:", isolation_level=None))
                await self.refused(self.backend(provider).complete(SPEC, dict(MSG), wait=False)) if provider.body is None else \
                    await self.backend(provider).complete(SPEC, dict(MSG), wait=False)
                spent = self.ledger.spent("deepseek-flash", self.now)
                self.assertEqual(spent == 0, expect_zero)

    async def test_missing_key_flag_off_and_oversize_never_call_the_provider(self) -> None:
        provider = FakeProvider()
        backend = self.backend(provider)
        set_env(self, GUARDIAN_DEEPSEEK_API_KEY=None)
        self.assertEqual((await self.refused(backend.complete(SPEC, dict(MSG), wait=False))).code, "cloud_key_missing")
        set_env(self, GUARDIAN_DEEPSEEK_API_KEY=KEY, GUARDIAN_CLOUD="false")
        self.assertEqual((await self.refused(backend.complete(SPEC, dict(MSG), wait=False))).code, "cloud_disabled")
        set_env(self, GUARDIAN_CLOUD="true")
        huge = {"messages": [{"role": "user", "content": "x" * 300_000}]}
        self.assertEqual((await self.refused(backend.complete(SPEC, huge, wait=False))).code, "request_too_large")
        self.assertEqual((await self.refused(backend.complete(SPEC, {"messages": []}, wait=False))).code, "invalid_request")
        self.assertEqual(provider.calls, [])

    async def test_local_model_is_refused(self) -> None:
        local = REGISTRY.get("workbench-local")
        exc = await self.refused(self.backend(FakeProvider()).complete(local, dict(MSG), wait=False))
        self.assertEqual(exc.code, "not_a_cloud_model")

    async def test_secret_manager_key_when_no_env_key(self) -> None:
        set_env(self, GUARDIAN_DEEPSEEK_API_KEY=None,
                GUARDIAN_DEEPSEEK_API_KEY_SECRET_REF="projects/carter-personal-secrets/secrets/deepseek/versions/latest",
                GUARDIAN_DEEPSEEK_API_KEY_SECRET_PRINCIPAL="carter@example.com")

        async def accessor(ref):
            return "carter@example.com", KEY

        provider = FakeProvider()
        await self.backend(provider, accessor=accessor).complete(SPEC, dict(MSG), wait=False)
        self.assertEqual(provider.calls[0][1]["Authorization"], f"Bearer {KEY}")


class CloudHttpBase(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true", "GUARDIAN_CLOUD": "true", "GUARDIAN_DEEPSEEK_API_KEY": KEY}

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.provider = FakeProvider()
        self.module.CLOUD_TRANSPORT = self.provider

    def only_cloud_is_ready(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        self.aiwa.models_status = 503

    async def chat(self, **over):
        return await self.client.post("/v1/chat/completions", json={"model": "deepseek-flash", **MSG, **over})


class CloudHttpTests(CloudHttpBase):
    async def test_direct_completion_is_served_by_guardian(self) -> None:
        self.module.guardian._llama_up = False
        response = await self.chat()
        text = await response.text()
        self.assertEqual(response.status, 200, text)
        self.assertIn("cloud-ok", text)
        self.assertNotIn(KEY, text)
        self.assertEqual(self.llama.hits, [])
        self.assertEqual(self.aiwa.chat_hits, [])

    async def test_streaming_and_busy_and_cap_have_stable_errors(self) -> None:
        self.assertEqual((await self.chat(stream=True)).status, 400)
        self.provider.gate = asyncio.Event()
        held = [asyncio.create_task(self.chat()) for _ in range(2)]
        await asyncio.sleep(0.1)
        busy = await self.chat()
        self.assertEqual((busy.status, (await busy.json())["error"]["code"]), (429, "cloud_busy"))
        self.provider.gate.set()
        await asyncio.gather(*held)

    async def test_flag_off_keeps_the_409_refusal(self) -> None:
        os.environ["GUARDIAN_CLOUD"] = "false"
        response = await self.chat()
        self.assertEqual((response.status, (await response.json())["error"]["code"]), (409, "route_cloud"))
        self.assertEqual(self.provider.calls, [])

    async def test_models_endpoint_reports_cloud_readiness_and_spend_cap(self) -> None:
        models = {m["id"]: m for m in (await (await self.client.get("/__guardian/models")).json())["models"]}
        self.assertEqual(models["deepseek-flash"]["readiness"]["state"], "ready")
        os.environ["GUARDIAN_CLOUD_DAILY_CAP_USD"] = "0.01"
        self.addCleanup(os.environ.pop, "GUARDIAN_CLOUD_DAILY_CAP_USD", None)
        await self.chat()
        models = {m["id"]: m for m in (await (await self.client.get("/__guardian/models")).json())["models"]}
        self.assertEqual(models["deepseek-flash"]["readiness"], {"state": "unavailable", "reason": "spend_cap"})
        self.assertNotIn(KEY, json.dumps(models))

    async def test_task_api_dispatches_an_internet_task_to_cloud(self) -> None:
        self.only_cloud_is_ready()
        await self.start_worker()
        response = await self.submit(clearance="internet", cost_ceiling_usd=5)
        self.assertEqual(response.status, 202, await response.text())
        body = await self.wait_status("t1", "succeeded")
        self.assertEqual(body["evidence"]["chosen_model"], "deepseek-flash")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertNotIn(KEY, json.dumps(body))

    async def test_lan_task_never_reaches_cloud(self) -> None:
        self.only_cloud_is_ready()
        body = await (await self.submit(clearance="lan", cost_ceiling_usd=5)).json()
        self.assertEqual((body["status"], body["reason"]), ("selection_failed", "no_candidates"))
        excluded = {e["id"]: e["reason"] for e in body["evidence"]["excluded"]}
        self.assertTrue(excluded["deepseek-flash"].startswith("egress"), excluded)
        self.assertEqual(self.provider.calls, [])

    async def test_pc_task_with_a_cost_ceiling_never_reaches_cloud(self) -> None:
        self.only_cloud_is_ready()
        body = await (await self.submit(clearance="pc", cost_ceiling_usd=5)).json()
        self.assertEqual(body["status"], "selection_failed")
        self.assertEqual(self.provider.calls, [])

    async def test_queued_cloud_job_runs_on_the_cloud_provider_not_the_local_model(self) -> None:
        await self.start_worker()
        response = await self.client.post("/__guardian/jobs", json={
            "idempotency_key": "cloud-job-1", "source": "t", "decision_context": {"summary": "s"},
            "request": {"model": "deepseek-flash", **MSG}})
        self.assertEqual(response.status, 202, await response.text())
        job_id = (await response.json())["job_id"]
        for _ in range(100):
            job = await (await self.client.get(f"/__guardian/jobs/{job_id}")).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.03)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(job["model_id"], "deepseek-flash")
        self.assertEqual(job["result"]["cloud"]["model"], "deepseek-flash")
        self.assertEqual(self.llama.hits, [])


class CloudPerModelQueueTests(CloudHttpBase):
    ENV = {**CloudHttpBase.ENV, "GUARDIAN_PER_MODEL_QUEUES": "true"}

    async def test_cloud_jobs_run_in_parallel_up_to_max_concurrent(self) -> None:
        self.only_cloud_is_ready()
        self.provider.delay = 0.2
        await self.start_worker()
        for n in range(3):
            self.assertEqual((await self.submit(task_id=f"t{n}", clearance="internet", cost_ceiling_usd=5)).status, 202)
        for n in range(3):
            await self.wait_status(f"t{n}", "succeeded", timeout=10)
        self.assertEqual(self.provider.max_in_flight, 2)


class CloudFlagOffTests(GuardianHarnessCase):
    async def test_queued_cloud_job_with_flag_off_fails_instead_of_reaching_the_local_model(self) -> None:
        self.module.guardian._llama_up = True
        self.module.guardian.job_store.submit(
            idempotency_key="k", source="t", priority=50, request={"model": "deepseek-flash", **MSG},
            decision={"route": "queue_local"}, model_id="deepseek-flash")
        job = self.module.guardian.job_store.claim_next(None)
        await self.module.run_queued_job(job)
        done = self.module.guardian.job_store.get(job.job_id)
        self.assertEqual(done.status, "failed")
        self.assertIn("cloud_disabled", done.error)
        self.assertEqual(self.llama.hits, [])


if __name__ == "__main__":
    unittest.main()
