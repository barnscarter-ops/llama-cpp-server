"""Test harness: a fresh llama-guardian module wired to fake llama and fake AIWA servers.

No test using this may reach 127.0.0.1:8080/8081 or 192.168.1.240:8080: the
module's LLAMA_PORT and AIWA_BASE are pointed at servers bound to random ports.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
import os
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import aiwa_swap
import fleet_router
import model_slots
from fleet_router import SERVING_IDS, OccupantCache

SCRIPTS_DIR = Path(__file__).resolve().parent
_counter = itertools.count()


def _fresh_occupant_cache() -> None:
    """New cache per test (its lock binds to one loop); aiwa_swap holds its own reference by name."""
    fleet_router.occupant_cache = aiwa_swap.occupant_cache = OccupantCache()


class FakeAiwa:
    """Fake R9700: /v1/models plus completions that record overlap."""

    def __init__(self) -> None:
        self.occupant_id = SERVING_IDS["clerk"]
        self.delay_s = 0.0
        self.delay_by_model: dict[str, float] = {}
        self.fail_models: set[str] = set()
        self.models_hits = 0
        self.chat_hits: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.events: list[str] = []
        self.models_status = 200
        self.server: TestServer | None = None

    async def models(self, request: web.Request) -> web.Response:
        self.models_hits += 1
        if self.models_status != 200:
            return web.json_response({"error": "down"}, status=self.models_status)
        return web.json_response({"data": [{"id": self.occupant_id}]})

    async def chat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.chat_hits.append(payload)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.events.append(f"start:{payload.get('model')}")
        try:
            delay = self.delay_by_model.get(payload.get("model"), self.delay_s)
            if delay:
                await asyncio.sleep(delay)
        finally:
            self.in_flight -= 1
        self.events.append(f"end:{payload.get('model')}")
        if payload.get("model") in self.fail_models:
            return web.json_response({"error": "boom"}, status=500)
        return web.json_response(
            {"id": "chatcmpl-aiwa", "choices": [{"message": {"role": "assistant", "content": "aiwa-ok"}, "finish_reason": "stop"}]}
        )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1/models", self.models)
        app.router.add_post("/v1/chat/completions", self.chat)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()

    @property
    def base(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("")).rstrip("/")

    async def close(self) -> None:
        if self.server:
            await self.server.close()


class FakeLlama:
    """Fake Workbench llama-server. Any hit is recorded so tests can assert it stayed asleep."""

    def __init__(self) -> None:
        self.hits: list[tuple[str, str]] = []
        self.healthy = True
        self.delay_s = 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        self.server: TestServer | None = None

    async def health(self, request: web.Request) -> web.Response:
        self.hits.append(("GET", request.path))
        return web.json_response({"status": "ok"}, status=200 if self.healthy else 503)

    async def chat(self, request: web.Request) -> web.Response:
        self.hits.append(("POST", request.path))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
        finally:
            self.in_flight -= 1
        return web.json_response(
            {"id": "chatcmpl-glm", "choices": [{"message": {"role": "assistant", "content": "glm-ok"}, "finish_reason": "stop"}]}
        )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/v1/health", self.health)
        app.router.add_post("/v1/chat/completions", self.chat)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()

    async def close(self) -> None:
        if self.server:
            await self.server.close()


class GuardianHarnessCase(unittest.IsolatedAsyncioTestCase):
    """Subclass and override ENV / build_routes. Provides self.module, self.client, self.aiwa, self.llama."""

    ENV: dict[str, str] = {}

    def build_routes(self, app: web.Application) -> None:
        m = self.module
        app.router.add_get("/__guardian/health", m.guardian_health)
        app.router.add_get("/__guardian/seats", m.guardian_seats)
        app.router.add_get("/__guardian/models", m.guardian_models)
        app.router.add_get("/__guardian/queues", m.guardian_queues)
        app.router.add_get("/__guardian/approvals", m.approvals_list)
        app.router.add_post("/__guardian/approvals/{approval_id}/decide", m.approvals_decide)
        app.router.add_post("/__guardian/swap", m.guardian_swap)
        app.router.add_post("/__guardian/jobs", m.queue_submit)
        app.router.add_get("/__guardian/jobs/{job_id}", m.queue_status)
        app.router.add_route("*", "/{tail:.*}", m.proxy_handler)

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        env = {
            "GUARDIAN_QUEUE_DB": str(Path(self.temp_dir.name) / "guardian.sqlite3"),
            "FLEET_ROUTER": "true",
            "AIWA_PROXY_LOOPBACK_ONLY": "true",
            # Pin every dispatcher flag off so a flag exported in the shell cannot change a default-behavior test.
            "GUARDIAN_MODEL_SLOTS": "false",
            "GUARDIAN_PER_MODEL_QUEUES": "false",
            "GUARDIAN_QWEN_APPROVAL": "false",
            **self.ENV,
        }
        self._env_backup = {k: os.environ.get(k) for k in env}
        self.aiwa = FakeAiwa()
        self.llama = FakeLlama()
        await self.aiwa.start()
        await self.llama.start()
        env["AIWA_BASE"] = self.aiwa.base
        self._env_backup.setdefault("AIWA_BASE", os.environ.get("AIWA_BASE"))
        os.environ.update(env)

        name = f"guardian_harness_{next(_counter)}"
        spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / "llama-guardian.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        self._module_name = name
        spec.loader.exec_module(self.module)
        self.module.LLAMA_HOST = "127.0.0.1"
        self.module.LLAMA_PORT = self.llama.server.port
        self.module.guardian._client = aiohttp.ClientSession()
        self.module.guardian._llama_up = False
        _fresh_occupant_cache()
        model_slots.reset_slots()

        app = web.Application()
        self.build_routes(app)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.module.guardian._client.close()
        await self.aiwa.close()
        await self.llama.close()
        self.module.guardian.job_store.close()
        _fresh_occupant_cache()
        model_slots.reset_slots()
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop(self._module_name, None)
