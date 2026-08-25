"""Fast, offline checks for the guardian queue's durable invariants."""

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import aiohttp

from guardian_queue import JobStore, parse_hermes_decision
import fleet_router
from fleet_router import OccupantCache


SCRIPTS_DIR = Path(__file__).resolve().parent


class StaticDecider:
    def __init__(self, decision=None):
        self.calls = 0
        self.decision = decision or {
            "route": "queue_local",
            "reason": "bounded implementation",
            "priority": 55,
        }

    async def decide(self, context, queue):
        self.calls += 1
        return dict(self.decision)


class StubOccupantCache:
    def __init__(self, occupant="clerk", model_id="nemotron-3.5-lightning-30b-a3b", reachable=True):
        self.occupant = occupant
        self.model_id = model_id
        self.reachable = reachable

    def invalidate(self) -> None:
        pass

    async def get(self, client):
        return self.occupant, self.model_id, self.reachable


class GuardianQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = JobStore(str(Path(self.temp_dir.name) / "jobs.sqlite3"))

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_claims_priority_then_preserves_idempotency(self) -> None:
        decision = {"route": "queue_local", "reason": "bounded patch", "priority": 20}
        request = {"messages": [{"role": "user", "content": "test"}], "stream": False}
        low, created = self.store.submit(
            idempotency_key="low", source="test", priority=20, request=request, decision=decision
        )
        self.assertTrue(created)
        duplicate, created = self.store.submit(
            idempotency_key="low", source="test", priority=20, request=request, decision=decision
        )
        self.assertFalse(created)
        self.assertEqual(low.job_id, duplicate.job_id)
        high, _ = self.store.submit(
            idempotency_key="high", source="test", priority=90, request=request, decision=decision
        )
        self.assertEqual(high.job_id, self.store.claim_next().job_id)
        self.assertEqual(low.job_id, self.store.claim_next().job_id)

    def test_restart_requeues_running_job(self) -> None:
        decision = {"route": "queue_local", "reason": "bounded patch", "priority": 50}
        job, _ = self.store.submit(
            idempotency_key="restart", source="test", priority=50,
            request={"messages": [{"role": "user", "content": "test"}], "stream": False},
            decision=decision,
        )
        self.assertEqual(job.job_id, self.store.claim_next().job_id)
        self.assertEqual(1, self.store.recover_interrupted())
        self.assertEqual("queued", self.store.get(job.job_id).status)

    def test_parses_only_valid_hermes_route(self) -> None:
        decision = parse_hermes_decision(
            "```json\n{\"route\":\"queue_qwen\",\"reason\":\"bounded task\",\"priority\":42}\n```"
        )
        self.assertEqual("queue_local", decision["route"])
        with self.assertRaises(Exception):
            parse_hermes_decision('{"route":"anything", "reason":"no", "priority":1}')

    def test_logs_all_decisions_including_rejections(self) -> None:
        self.store.log_decision("codex", {"route": "bypass", "reason": "trivial", "priority": 10}, {"summary": "fix typo"})
        self.store.log_decision("claude", {"route": "queue_local", "reason": "well-scoped", "priority": 60}, {"summary": "add test"})
        rows = self.store._conn.execute("SELECT route, source FROM guardian_decisions ORDER BY id").fetchall()
        self.assertEqual(2, len(rows))
        self.assertEqual("bypass", rows[0]["route"])
        self.assertEqual("queue_local", rows[1]["route"])

    def test_normalizes_legacy_route_to_local(self) -> None:
        decision = parse_hermes_decision('{"route":"queue_qwen","reason":"legacy caller","priority":42}')
        self.assertEqual("queue_local", decision["route"])


class GuardianQueueHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._env_backup = {
            k: os.environ.get(k)
            for k in ("GUARDIAN_QUEUE_DB", "FLEET_ROUTER", "HERMES_DECIDER_ENABLED")
        }
        os.environ["GUARDIAN_QUEUE_DB"] = str(Path(self.temp_dir.name) / "guardian.sqlite3")
        os.environ["FLEET_ROUTER"] = "false"
        os.environ["HERMES_DECIDER_ENABLED"] = "false"
        spec = importlib.util.spec_from_file_location(
            "guardian_http_test", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.decider = StaticDecider()
        self.module.guardian.hermes_decider = self.decider
        self.module.guardian.queue_event = asyncio.Event()
        self.session = aiohttp.ClientSession()
        self.module.guardian._client = self.session

        app = web.Application()
        app.router.add_post("/__guardian/jobs", self.module.queue_submit)
        app.router.add_get("/__guardian/jobs/{job_id}", self.module.queue_status)
        app.router.add_post("/__guardian/jobs/{job_id}/cancel", self.module.queue_cancel)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.session.close()
        self.module.guardian.job_store.close()
        self.temp_dir.cleanup()
        fleet_router.occupant_cache = OccupantCache()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop("guardian_http_test", None)

    def _payload(self, model="ignored", key="http-test"):
        return {
            "source": "test",
            "idempotency_key": key,
            "decision_context": {"summary": "Add one small pure helper", "expected_lines": 20},
            "request": {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": "test"}],
            },
        }

    async def test_submit_status_and_cancel_are_loopback_safe(self) -> None:
        # FLEET_ROUTER explicitly off: submit still forces local-llm (GLM-only).
        self.assertEqual("false", os.environ["FLEET_ROUTER"])
        response = await self.client.post("/__guardian/jobs", json=self._payload())
        self.assertEqual(202, response.status)
        submitted = await response.json()
        self.assertEqual("queued", submitted["status"])
        self.assertEqual("local-llm", self.module.guardian.job_store.get(submitted["job_id"]).request["model"])

        response = await self.client.get(f"/__guardian/jobs/{submitted['job_id']}")
        self.assertEqual(200, response.status)
        response = await self.client.post(f"/__guardian/jobs/{submitted['job_id']}/cancel")
        self.assertEqual(200, response.status)
        self.assertEqual("cancelled", (await response.json())["status"])

    async def test_normalizes_retired_model_alias(self) -> None:
        body, changed = self.module.normalize_legacy_model_alias(b'{"model":"qwen3.6-35b","messages":[]}')
        self.assertTrue(changed)
        self.assertEqual("local-llm", __import__("json").loads(body)["model"])

    async def test_fleet_on_clerk_model_not_rewritten(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        response = await self.client.post("/__guardian/jobs", json=self._payload(model="clerk", key="clerk-keep"))
        self.assertEqual(202, response.status)
        submitted = await response.json()
        self.assertEqual("queued", submitted["status"])
        self.assertEqual(
            "clerk",
            self.module.guardian.job_store.get(submitted["job_id"]).request["model"],
        )

    async def test_fleet_on_hermes_off_enqueues_without_hermes(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        os.environ["HERMES_DECIDER_ENABLED"] = "false"
        self.decider.calls = 0
        for model in ("clerk", "local-llm"):
            response = await self.client.post(
                "/__guardian/jobs", json=self._payload(model=model, key=f"nohermes-{model}")
            )
            self.assertEqual(202, response.status, await response.text())
            self.assertEqual("queued", (await response.json())["status"])
        self.assertEqual(0, self.decider.calls)

    async def test_fleet_on_hermes_fallback_cloud_rejected(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        os.environ["HERMES_DECIDER_ENABLED"] = "true"
        self.module.guardian.hermes_decider = StaticDecider(
            {"route": "fallback_cloud", "reason": "wants cloud", "priority": 10}
        )
        for model in ("clerk", "local-llm"):
            response = await self.client.post(
                "/__guardian/jobs", json=self._payload(model=model, key=f"fbcloud-{model}")
            )
            self.assertEqual(503, response.status)
            body = await response.json()
            self.assertEqual("hermes_decision_unavailable", body["error"]["code"])
            self.assertIsNone(self.module.guardian.job_store.get_by_idempotency_key(f"fbcloud-{model}"))

    async def test_fleet_on_hermes_error_fails_closed(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        os.environ["HERMES_DECIDER_ENABLED"] = "true"

        class BrokenDecider:
            async def decide(self, context, queue):
                raise RuntimeError("nope")

        self.module.guardian.hermes_decider = BrokenDecider()
        response = await self.client.post("/__guardian/jobs", json=self._payload(model="clerk", key="broken"))
        self.assertEqual(503, response.status)
        body = await response.json()
        self.assertEqual("hermes_decision_unavailable", body["error"]["code"])

    async def test_fleet_on_consult_wrong_occupant_409(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        fleet_router.occupant_cache = StubOccupantCache(occupant="clerk")
        response = await self.client.post("/__guardian/jobs", json=self._payload(model="consult", key="consult-409"))
        self.assertEqual(409, response.status)
        body = await response.json()
        self.assertEqual("aiwa_wrong_occupant", body["error"]["code"])
        self.assertIsNone(self.module.guardian.job_store.get_by_idempotency_key("consult-409"))

    async def test_fleet_on_consult_unreachable_503(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        fleet_router.occupant_cache = StubOccupantCache(occupant="unknown", model_id=None, reachable=False)
        response = await self.client.post("/__guardian/jobs", json=self._payload(model="consult", key="consult-503"))
        self.assertEqual(503, response.status)
        body = await response.json()
        self.assertEqual("aiwa_unreachable", body["error"]["code"])

    async def test_fleet_on_cloud_model_409_route_cloud(self) -> None:
        os.environ["FLEET_ROUTER"] = "true"
        response = await self.client.post("/__guardian/jobs", json=self._payload(model="gpt-4o", key="cloud-409"))
        self.assertEqual(409, response.status)
        body = await response.json()
        self.assertEqual("route_cloud", body["error"]["code"])
        self.assertIsNone(self.module.guardian.job_store.get_by_idempotency_key("cloud-409"))
        self.assertEqual(0, self.decider.calls)


if __name__ == "__main__":
    unittest.main()
