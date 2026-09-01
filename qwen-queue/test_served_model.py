"""Served-model of record: X-Guardian-* headers, served counters, consult gate."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import aiwa_swap
import fleet_router
from fleet_router import OccupantCache, SERVING_IDS

SCRIPTS_DIR = Path(__file__).resolve().parent


class ServedModelHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._env_backup = {
            k: os.environ.get(k)
            for k in (
                "FLEET_ROUTER",
                "FLEET_DEFAULT_SEAT",
                "FLEET_SWAP_OWNER",
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

        async def aiwa_models(request: web.Request):
            body = {"data": [{"id": self.aiwa_occupant_id}]}
            return web.json_response(body)

        async def aiwa_chat(request: web.Request):
            payload = await request.json()
            self.aiwa_hits.append(("POST", request.path, payload))
            if payload.get("stream"):
                resp = web.StreamResponse(
                    status=200, headers={"Content-Type": "text/event-stream"}
                )
                await resp.prepare(request)
                await resp.write(b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n')
                await resp.write(b"data: [DONE]\n\n")
                await resp.write_eof()
                return resp
            return web.json_response(
                {
                    "id": "chatcmpl-aiwa",
                    "choices": [
                        {"message": {"role": "assistant", "content": "aiwa-ok"}, "finish_reason": "stop"}
                    ],
                }
            )

        aiwa_app = web.Application()
        aiwa_app.router.add_get("/v1/models", aiwa_models)
        aiwa_app.router.add_post("/v1/chat/completions", aiwa_chat)
        self.aiwa_server = TestServer(aiwa_app, host="127.0.0.1")
        await self.aiwa_server.start_server()
        os.environ["AIWA_BASE"] = str(self.aiwa_server.make_url("")).rstrip("/")

        self.llama_hits: list[tuple[str, str]] = []

        async def llama_health(request: web.Request):
            return web.json_response({"status": "ok"})

        async def llama_chat(request: web.Request):
            payload = await request.json()
            self.llama_hits.append(("POST", request.path))
            if payload.get("stream"):
                resp = web.StreamResponse(
                    status=200, headers={"Content-Type": "text/event-stream"}
                )
                await resp.prepare(request)
                await resp.write(b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n')
                await resp.write(b"data: [DONE]\n\n")
                await resp.write_eof()
                return resp
            return web.json_response(
                {
                    "id": "chatcmpl-glm",
                    "choices": [
                        {"message": {"role": "assistant", "content": "glm-ok"}, "finish_reason": "stop"}
                    ],
                }
            )

        llama_app = web.Application()
        llama_app.router.add_get("/v1/health", llama_health)
        llama_app.router.add_post("/v1/chat/completions", llama_chat)
        self.llama_server = TestServer(llama_app, host="127.0.0.1")
        await self.llama_server.start_server()

        spec = importlib.util.spec_from_file_location(
            "guardian_served_test", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

        self.module.LLAMA_HOST = "127.0.0.1"
        self.module.LLAMA_PORT = self.llama_server.port

        self.module.guardian._client = aiohttp.ClientSession()
        self.module.guardian._llama_up = False
        self.module.guardian.active_requests = 0
        self.module.guardian.last_request_time = 1000.0

        fleet_router.occupant_cache = OccupantCache()

        # Swap is mocked for every test; the real perform_swap does SSH + WORKBOARD.
        self._orig_perform_swap = aiwa_swap.perform_swap
        self.swap_calls: list[tuple[str, str | None]] = []

        async def fake_perform_swap(to, client, gate=None):
            self.swap_calls.append((to, gate))
            return web.json_response(
                {"status": "ok", "occupant": to, "model_id": SERVING_IDS[to]}
            )

        aiwa_swap.perform_swap = fake_perform_swap

        @web.middleware
        async def remote_override(request, handler):
            override = request.headers.get("X-Test-Remote")
            if override:
                request = request.clone(remote=override)
            return await handler(request)

        app = web.Application(middlewares=[remote_override])
        app.router.add_get("/__guardian/health", self.module.guardian_health)
        app.router.add_get("/__guardian/seats", self.module.guardian_seats)
        app.router.add_route("*", "/{tail:.*}", self.module.proxy_handler)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.module.guardian._client.close()
        await self.aiwa_server.close()
        await self.llama_server.close()
        self.module.guardian.job_store.close()
        aiwa_swap.perform_swap = self._orig_perform_swap
        fleet_router.occupant_cache = OccupantCache()
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop("guardian_served_test", None)

    def _completion(self, model: str, stream: bool = False) -> dict:
        return {
            "model": model,
            "stream": stream,
            "messages": [{"role": "user", "content": "hi"}],
        }

    async def _wait_served(self, seat: str, count: int, rid: str, timeout: float = 5.0) -> bool:
        """Counters increment when the stream is fully handed back; poll past the
        client-read/server-record scheduling edge instead of asserting racy."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            entry = self.module.guardian.served.seats[seat]
            if entry["count"] >= count and entry["last_request_id"] == rid:
                return True
            await asyncio.sleep(0.01)
        return False

    async def _health(self) -> dict:
        resp = await self.client.get("/__guardian/health")
        self.assertEqual(200, resp.status, await resp.text())
        return await resp.json()

    def _assert_rid(self, rid: str | None) -> None:
        self.assertTrue(rid and len(rid) == 36, rid)

    # ── glm path ────────────────────────────────────────────────────────────

    async def test_glm_non_stream_headers_and_counter(self) -> None:
        resp = await self.client.post(
            "/v1/chat/completions", json=self._completion("local-llm")
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("glm", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual("local-llm", resp.headers.get("X-Guardian-Served-Model"))
        rid = resp.headers.get("X-Guardian-Request-Id")
        self._assert_rid(rid)
        body = await resp.json()
        self.assertEqual("glm-ok", body["choices"][0]["message"]["content"])

        self.assertTrue(await self._wait_served("glm", 1, rid))
        health = await self._health()
        served = health["served"]["glm"]
        self.assertEqual(1, served["count"])
        self.assertEqual("local-llm", served["last_model_id"])
        self.assertEqual(rid, served["last_request_id"])
        self.assertIsNotNone(served["last_at"])
        self.assertEqual(1, health["served_total"])
        self.assertEqual(0, health["served"]["clerk"]["count"])

    async def test_glm_stream_headers_and_counter(self) -> None:
        resp = await self.client.post(
            "/v1/chat/completions", json=self._completion("local-llm", stream=True)
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("glm", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual("local-llm", resp.headers.get("X-Guardian-Served-Model"))
        rid = resp.headers.get("X-Guardian-Request-Id")
        self._assert_rid(rid)
        text = await resp.text()
        self.assertIn("data:", text)

        self.assertTrue(await self._wait_served("glm", 1, rid))
        self.assertTrue(any(p == "/v1/chat/completions" for _, p in self.llama_hits))

    # ── clerk path ──────────────────────────────────────────────────────────

    async def test_clerk_non_stream_headers_and_counter(self) -> None:
        fleet_router.occupant_cache.invalidate()
        resp = await self.client.post("/v1/chat/completions", json=self._completion("clerk"))
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("clerk", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual(
            "nemotron-3.5-lightning-30b-a3b", resp.headers.get("X-Guardian-Served-Model")
        )
        rid = resp.headers.get("X-Guardian-Request-Id")
        self._assert_rid(rid)
        body = await resp.json()
        self.assertEqual("aiwa-ok", body["choices"][0]["message"]["content"])
        posts = [h for h in self.aiwa_hits if h[0] == "POST"]
        self.assertEqual(1, len(posts))
        self.assertEqual("nemotron-3.5-lightning-30b-a3b", posts[0][2]["model"])

        self.assertTrue(await self._wait_served("clerk", 1, rid))
        health = await self._health()
        served = health["served"]["clerk"]
        self.assertEqual(1, served["count"])
        self.assertEqual("nemotron-3.5-lightning-30b-a3b", served["last_model_id"])
        self.assertEqual(rid, served["last_request_id"])
        self.assertEqual(1, health["served_total"])

        # /__guardian/seats exposes the same counters
        resp = await self.client.get("/__guardian/seats")
        self.assertEqual(200, resp.status, await resp.text())
        body = await resp.json()
        self.assertEqual(1, body["served"]["clerk"]["count"])
        self.assertEqual(1, body["served_total"])

    async def test_clerk_stream_headers_and_counter(self) -> None:
        fleet_router.occupant_cache.invalidate()
        resp = await self.client.post(
            "/v1/chat/completions", json=self._completion("clerk", stream=True)
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("clerk", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual(
            "nemotron-3.5-lightning-30b-a3b", resp.headers.get("X-Guardian-Served-Model")
        )
        rid = resp.headers.get("X-Guardian-Request-Id")
        self._assert_rid(rid)
        text = await resp.text()
        self.assertIn("data:", text)
        posts = [h for h in self.aiwa_hits if h[0] == "POST"]
        self.assertEqual(1, len(posts))
        self.assertTrue(posts[0][2].get("stream") is True)
        self.assertEqual("nemotron-3.5-lightning-30b-a3b", posts[0][2]["model"])

        self.assertTrue(await self._wait_served("clerk", 1, rid))

    # ── consult gate ────────────────────────────────────────────────────────

    async def test_consult_gate_header_triggers_swap(self) -> None:
        fleet_router.occupant_cache.invalidate()
        self.aiwa_hits.clear()
        self.swap_calls.clear()

        resp = await self.client.post(
            "/v1/chat/completions",
            json=self._completion("consult"),
            headers={"X-Guardian-Gate": "operator"},
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("consult", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual("qwen3.8-27b", resp.headers.get("X-Guardian-Served-Model"))
        self.assertEqual([("consult", "operator")], self.swap_calls)
        posts = [h for h in self.aiwa_hits if h[0] == "POST"]
        self.assertEqual(1, len(posts))
        self.assertEqual("qwen3.8-27b", posts[0][2]["model"])
        self.assertTrue(
            await self._wait_served("consult", 1, resp.headers.get("X-Guardian-Request-Id"))
        )

    async def test_consult_gate_query_param_triggers_swap(self) -> None:
        fleet_router.occupant_cache.invalidate()
        self.aiwa_hits.clear()
        self.swap_calls.clear()

        resp = await self.client.post(
            "/v1/chat/completions?gate=frontier", json=self._completion("consult")
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("consult", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual("qwen3.8-27b", resp.headers.get("X-Guardian-Served-Model"))
        self.assertEqual([("consult", "frontier")], self.swap_calls)
        posts = [h for h in self.aiwa_hits if h[0] == "POST"]
        self.assertEqual(1, len(posts))
        self.assertEqual("qwen3.8-27b", posts[0][2]["model"])

    async def test_consult_without_gate_downgrades_to_clerk(self) -> None:
        fleet_router.occupant_cache.invalidate()
        self.aiwa_hits.clear()
        self.swap_calls.clear()

        resp = await self.client.post("/v1/chat/completions", json=self._completion("consult"))
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("clerk", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual(
            "nemotron-3.5-lightning-30b-a3b", resp.headers.get("X-Guardian-Served-Model")
        )
        self.assertEqual([], self.swap_calls)
        posts = [h for h in self.aiwa_hits if h[0] == "POST"]
        self.assertEqual(1, len(posts))
        self.assertEqual("nemotron-3.5-lightning-30b-a3b", posts[0][2]["model"])

    async def test_consult_invalid_gate_downgrades_to_clerk(self) -> None:
        fleet_router.occupant_cache.invalidate()
        self.swap_calls.clear()

        resp = await self.client.post(
            "/v1/chat/completions",
            json=self._completion("consult"),
            headers={"X-Guardian-Gate": "self-service"},
        )
        self.assertEqual(200, resp.status, await resp.text())
        self.assertEqual("clerk", resp.headers.get("X-Guardian-Seat"))
        self.assertEqual([], self.swap_calls)

    async def test_consult_gate_swap_failure_returns_swap_error(self) -> None:
        fleet_router.occupant_cache.invalidate()
        self.swap_calls.clear()

        async def failing_swap(to, client, gate=None):
            self.swap_calls.append((to, gate))
            return web.json_response(
                {"error": {"message": "Board owns the SSH swap.", "code": "swap_board_owns_ssh"}},
                status=409,
            )

        aiwa_swap.perform_swap = failing_swap
        try:
            resp = await self.client.post(
                "/v1/chat/completions",
                json=self._completion("consult"),
                headers={"X-Guardian-Gate": "operator"},
            )
        finally:
            aiwa_swap.perform_swap = self._orig_perform_swap
        self.assertEqual(409, resp.status)
        self.assertEqual("consult", resp.headers.get("X-Guardian-Seat"))
        self.assertIsNone(resp.headers.get("X-Guardian-Served-Model"))
        self._assert_rid(resp.headers.get("X-Guardian-Request-Id"))
        self.assertEqual([("consult", "operator")], self.swap_calls)

    # ── error responses ─────────────────────────────────────────────────────

    async def test_wrong_occupant_409_carries_seat_header_no_served_model(self) -> None:
        self.aiwa_occupant_id = "qwen3.8-27b"  # consult occupies
        fleet_router.occupant_cache.invalidate()

        resp = await self.client.post("/v1/chat/completions", json=self._completion("clerk"))
        self.assertEqual(409, resp.status)
        self.assertEqual("clerk", resp.headers.get("X-Guardian-Seat"))
        self.assertIsNone(resp.headers.get("X-Guardian-Served-Model"))
        self._assert_rid(resp.headers.get("X-Guardian-Request-Id"))
        body = await resp.json()
        self.assertEqual("aiwa_wrong_occupant", body["error"]["code"])

        # an error never counts as served
        health = await self._health()
        self.assertEqual(0, health["served_total"])


if __name__ == "__main__":
    unittest.main()
