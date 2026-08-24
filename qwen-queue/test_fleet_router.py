"""Fleet router: clerk/consult → AIWA without GLM locks or 8081."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import fleet_router
from fleet_router import (
    OccupantCache,
    parse_primary_model_id,
    role_for_aiwa_model_id,
    seat_for_model,
)


SCRIPTS_DIR = Path(__file__).resolve().parent


class RecordingClientSession:
    """Wraps ClientSession and records every outbound URL."""

    def __init__(self, real: aiohttp.ClientSession):
        self._real = real
        self.calls: list[tuple[str, str]] = []

    def _record(self, method: str, url: str) -> None:
        self.calls.append((method.upper(), str(url)))

    def request(self, method, url, **kwargs):
        self._record(method, url)
        return self._real.request(method, url, **kwargs)

    def get(self, url, **kwargs):
        self._record("GET", url)
        return self._real.get(url, **kwargs)

    def post(self, url, **kwargs):
        self._record("POST", url)
        return self._real.post(url, **kwargs)

    async def close(self) -> None:
        await self._real.close()

    def urls_matching(self, needle: str) -> list[tuple[str, str]]:
        return [(m, u) for m, u in self.calls if needle in u]


class TrackingLock:
    def __init__(self, real: asyncio.Lock):
        self._real = real
        self.acquire_count = 0

    async def __aenter__(self):
        self.acquire_count += 1
        return await self._real.__aenter__()

    async def __aexit__(self, *args):
        return await self._real.__aexit__(*args)

    def locked(self) -> bool:
        return self._real.locked()


class SeatForModelTests(unittest.TestCase):
    def test_aliases(self) -> None:
        self.assertEqual("glm", seat_for_model(None))
        self.assertEqual("glm", seat_for_model(""))
        self.assertEqual("glm", seat_for_model("local-llm"))
        self.assertEqual("glm", seat_for_model("qwen3-14b"))
        self.assertEqual("glm", seat_for_model("qwen3.6-35b"))
        self.assertEqual("clerk", seat_for_model("clerk"))
        self.assertEqual("clerk", seat_for_model("nemotron-3.5-lightning-30b-a3b"))
        self.assertEqual("consult", seat_for_model("consult"))
        self.assertEqual("consult", seat_for_model("qwen3.8-27b"))
        self.assertEqual("cloud", seat_for_model("gpt-4o"))

    def test_occupant_parse(self) -> None:
        self.assertEqual(
            "nemotron-3.5-lightning-30b-a3b",
            parse_primary_model_id({"data": [{"id": "nemotron-3.5-lightning-30b-a3b"}]}),
        )
        self.assertEqual("clerk", role_for_aiwa_model_id("nemotron-3.5-lightning-30b-a3b"))
        self.assertEqual("consult", role_for_aiwa_model_id("qwen3.8-27b"))


class FleetRouterHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._env_backup = {
            k: os.environ.get(k)
            for k in (
                "FLEET_ROUTER",
                "AIWA_BASE",
                "AIWA_PROXY_LOOPBACK_ONLY",
                "GUARDIAN_QUEUE_DB",
            )
        }
        os.environ["GUARDIAN_QUEUE_DB"] = str(Path(self.temp_dir.name) / "guardian.sqlite3")
        os.environ["FLEET_ROUTER"] = "true"
        os.environ["AIWA_PROXY_LOOPBACK_ONLY"] = "true"

        self.aiwa_hits: list[tuple[str, str, dict | None]] = []
        self.aiwa_occupant_id = "nemotron-3.5-lightning-30b-a3b"
        self.aiwa_models_delay_s = 0.0
        self.aiwa_models_fail = False

        async def aiwa_models(request: web.Request):
            if self.aiwa_models_delay_s:
                await asyncio.sleep(self.aiwa_models_delay_s)
            if self.aiwa_models_fail:
                raise web.HTTPServiceUnavailable()
            body = {"data": [{"id": self.aiwa_occupant_id}]}
            self.aiwa_hits.append(("GET", request.path, None))
            return web.json_response(body)

        async def aiwa_chat(request: web.Request):
            payload = await request.json()
            self.aiwa_hits.append(("POST", request.path, payload))
            return web.json_response(
                {
                    "id": "chatcmpl-aiwa",
                    "choices": [
                        {"message": {"role": "assistant", "content": "aiwa-ok"}, "finish_reason": "stop"}
                    ],
                }
            )

        async def aiwa_metrics(request: web.Request):
            self.aiwa_hits.append(("GET", request.path, None))
            return web.Response(text="# aiwa metrics\n", content_type="text/plain")

        aiwa_app = web.Application()
        aiwa_app.router.add_get("/v1/models", aiwa_models)
        aiwa_app.router.add_post("/v1/chat/completions", aiwa_chat)
        aiwa_app.router.add_get("/metrics", aiwa_metrics)
        self.aiwa_server = TestServer(aiwa_app, host="127.0.0.1")
        await self.aiwa_server.start_server()
        os.environ["AIWA_BASE"] = str(self.aiwa_server.make_url("")).rstrip("/")

        self.llama_hits: list[tuple[str, str]] = []

        async def llama_health(request: web.Request):
            self.llama_hits.append(("GET", request.path))
            return web.json_response({"status": "ok"})

        async def llama_models(request: web.Request):
            self.llama_hits.append(("GET", request.path))
            return web.json_response({"data": [{"id": "local-llm"}]})

        async def llama_metrics(request: web.Request):
            self.llama_hits.append(("GET", request.path))
            return web.Response(text="# glm metrics\n", content_type="text/plain")

        async def llama_chat(request: web.Request):
            payload = await request.json()
            self.llama_hits.append(("POST", request.path))
            return web.json_response(
                {
                    "id": "chatcmpl-glm",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": f"glm:{payload.get('model')}"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        llama_app = web.Application()
        llama_app.router.add_get("/v1/health", llama_health)
        llama_app.router.add_get("/v1/models", llama_models)
        llama_app.router.add_get("/metrics", llama_metrics)
        llama_app.router.add_post("/v1/chat/completions", llama_chat)
        self.llama_server = TestServer(llama_app, host="127.0.0.1")
        await self.llama_server.start_server()

        spec = importlib.util.spec_from_file_location(
            "guardian_fleet_test", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

        self.module.LLAMA_HOST = "127.0.0.1"
        self.module.LLAMA_PORT = self.llama_server.port

        real_session = aiohttp.ClientSession()
        self.recording = RecordingClientSession(real_session)
        self.module.guardian._client = self.recording
        self.module.guardian._llama_up = False
        self.module.guardian.active_requests = 0
        self.module.guardian.last_request_time = 1000.0

        self.lock = TrackingLock(self.module.guardian.generation_lock)
        self.module.guardian.generation_lock = self.lock

        self.ensure_calls: list[str] = []

        async def fake_ensure(*, reason: str = ""):
            self.ensure_calls.append(reason)
            return False

        self.module.ensure_llama_started = fake_ensure

        fleet_router.occupant_cache = OccupantCache()

        @web.middleware
        async def remote_override(request, handler):
            override = request.headers.get("X-Test-Remote")
            if override:
                request = request.clone(remote=override)
            return await handler(request)

        app = web.Application(middlewares=[remote_override])
        app.router.add_get("/__guardian/health", self.module.guardian_health)
        app.router.add_route("*", "/{tail:.*}", self.module.proxy_handler)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.recording.close()
        await self.aiwa_server.close()
        await self.llama_server.close()
        self.module.guardian.job_store.close()
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        fleet_router.occupant_cache = OccupantCache()
        sys.modules.pop("guardian_fleet_test", None)

    def _llama_target_calls(self) -> list[tuple[str, str]]:
        needle = f"127.0.0.1:{self.llama_server.port}"
        return self.recording.urls_matching(needle)

    def _completion(self, model, **extra):
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        }
        payload.update(extra)
        return payload

    async def test_clerk_post_while_glm_down_proxies_aiwa(self) -> None:
        self.module.guardian._llama_up = False
        before_active = self.module.guardian.active_requests
        before_idle = self.module.guardian.last_request_time

        for model in ("clerk", "nemotron-3.5-lightning-30b-a3b"):
            self.aiwa_hits.clear()
            self.ensure_calls.clear()
            self.recording.calls.clear()
            self.lock.acquire_count = 0
            fleet_router.occupant_cache.invalidate()

            resp = await self.client.post("/v1/chat/completions", json=self._completion(model))
            self.assertEqual(200, resp.status, await resp.text())
            body = await resp.json()
            self.assertEqual("aiwa-ok", body["choices"][0]["message"]["content"])

            self.assertEqual([], self.ensure_calls)
            self.assertEqual(0, self.lock.acquire_count)
            self.assertEqual(before_active, self.module.guardian.active_requests)
            self.assertEqual(before_idle, self.module.guardian.last_request_time)
            self.assertEqual([], self._llama_target_calls())
            self.assertEqual([], self.llama_hits)

            posts = [h for h in self.aiwa_hits if h[0] == "POST"]
            self.assertEqual(1, len(posts))
            self.assertEqual("nemotron-3.5-lightning-30b-a3b", posts[0][2]["model"])

    async def test_consult_while_clerk_occupant_is_409(self) -> None:
        self.aiwa_occupant_id = "nemotron-3.5-lightning-30b-a3b"
        fleet_router.occupant_cache.invalidate()
        self.recording.calls.clear()

        resp = await self.client.post("/v1/chat/completions", json=self._completion("consult"))
        self.assertEqual(409, resp.status)
        body = await resp.json()
        self.assertEqual("aiwa_wrong_occupant", body["error"]["code"])
        self.assertEqual("clerk", body["occupant"])
        self.assertEqual([], self.ensure_calls)
        self.assertEqual([], self._llama_target_calls())
        # Occupant probe only — no rewrite/proxy to AIWA chat.
        self.assertFalse(any(h[0] == "POST" for h in self.aiwa_hits))

    async def test_aiwa_timeout_is_503_unreachable(self) -> None:
        self.aiwa_models_delay_s = 3.0
        fleet_router.occupant_cache.invalidate()

        resp = await self.client.post("/v1/chat/completions", json=self._completion("clerk"))
        self.assertEqual(503, resp.status)
        body = await resp.json()
        self.assertEqual("aiwa_unreachable", body["error"]["code"])
        self.assertNotEqual("aiwa_wrong_occupant", body["error"]["code"])
        self.assertEqual([], self.ensure_calls)
        self.assertEqual([], self._llama_target_calls())

    async def test_glm_post_still_uses_generation_lock(self) -> None:
        self.module.guardian._llama_up = True

        async def up_true():
            return True

        self.module.guardian.is_llama_up = up_true
        self.lock.acquire_count = 0
        before_idle = self.module.guardian.last_request_time

        for model in ("local-llm", "qwen3-14b"):
            self.lock.acquire_count = 0
            self.llama_hits.clear()
            resp = await self.client.post("/v1/chat/completions", json=self._completion(model))
            self.assertEqual(200, resp.status, await resp.text())
            body = await resp.json()
            self.assertTrue(body["choices"][0]["message"]["content"].startswith("glm:"))
            self.assertGreaterEqual(self.lock.acquire_count, 1)
            self.assertTrue(any(p == "/v1/chat/completions" for _, p in self.llama_hits))

        self.assertGreater(self.module.guardian.last_request_time, before_idle)

    async def test_metrics_never_forwarded_to_aiwa(self) -> None:
        self.module.guardian._llama_up = False
        self.aiwa_hits.clear()
        resp = await self.client.get("/metrics")
        self.assertEqual(503, resp.status)
        body = await resp.json()
        self.assertEqual("llama_offline", body["error"]["code"])
        self.assertFalse(any(h[1] == "/metrics" for h in self.aiwa_hits))

        self.module.guardian._llama_up = True
        self.aiwa_hits.clear()
        self.llama_hits.clear()
        resp = await self.client.get("/metrics")
        self.assertEqual(200, resp.status)
        self.assertIn("glm metrics", await resp.text())
        self.assertFalse(any(h[1] == "/metrics" for h in self.aiwa_hits))
        self.assertTrue(any(p == "/metrics" for _, p in self.llama_hits))

    async def test_v1_models_glm_down_uses_cache_not_live_8081(self) -> None:
        self.module.guardian._llama_up = False
        self.recording.calls.clear()
        self.llama_hits.clear()
        self.aiwa_hits.clear()

        resp = await self.client.get("/v1/models")
        self.assertEqual(503, resp.status)
        body = await resp.json()
        self.assertEqual("llama_offline", body["error"]["code"])
        self.assertNotIn("data", body)
        text = json.dumps(body)
        self.assertNotIn("nemotron", text)
        self.assertNotIn("qwen3.8", text)
        self.assertEqual([], self._llama_target_calls())
        self.assertEqual([], self.llama_hits)
        self.assertEqual([], self.aiwa_hits)

    async def test_empty_model_stays_glm(self) -> None:
        self.module.guardian._llama_up = False

        async def down():
            return False

        self.module.guardian.is_llama_up = down
        self.ensure_calls.clear()
        payload = {"stream": False, "messages": [{"role": "user", "content": "hi"}]}
        resp = await self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(503, resp.status)
        body = await resp.json()
        self.assertEqual("llama_warming", body["error"]["code"])
        self.assertEqual(["request"], self.ensure_calls)
        self.assertFalse(any(h[0] == "POST" for h in self.aiwa_hits))

    async def test_tailscale_remote_clerk_forbidden(self) -> None:
        fleet_router.occupant_cache.invalidate()
        resp = await self.client.post(
            "/v1/chat/completions",
            json=self._completion("clerk"),
            headers={"X-Test-Remote": "100.124.41.115"},
        )
        self.assertEqual(403, resp.status)
        body = await resp.json()
        self.assertEqual("aiwa_proxy_loopback_only", body["error"]["code"])
        self.assertFalse(any(h[0] == "POST" for h in self.aiwa_hits))

    async def test_unknown_model_route_cloud(self) -> None:
        resp = await self.client.post("/v1/chat/completions", json=self._completion("gpt-4o"))
        self.assertEqual(409, resp.status)
        body = await resp.json()
        self.assertEqual("route_cloud", body["error"]["code"])
        self.assertEqual([], self.ensure_calls)
        self.assertFalse(any(h[0] == "POST" for h in self.aiwa_hits))

    async def test_fleet_router_false_clerk_follows_glm_path(self) -> None:
        os.environ["FLEET_ROUTER"] = "false"
        self.module.guardian._llama_up = False

        async def down():
            return False

        self.module.guardian.is_llama_up = down
        self.ensure_calls.clear()
        self.aiwa_hits.clear()

        resp = await self.client.post("/v1/chat/completions", json=self._completion("clerk"))
        self.assertEqual(503, resp.status)
        body = await resp.json()
        self.assertEqual("llama_warming", body["error"]["code"])
        self.assertEqual(["request"], self.ensure_calls)
        self.assertFalse(any(h[0] == "POST" for h in self.aiwa_hits))


if __name__ == "__main__":
    unittest.main()
