"""RAM safety net: the guardian must not cold-start llama on a starved box."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

SCRIPTS_DIR = Path(__file__).resolve().parent
GIB = 1024 ** 3
ENV_KEYS = ("FLEET_ROUTER", "GUARDIAN_QUEUE_DB", "GUARDIAN_MIN_FREE_RAM_GB", "AIWA_BASE")


class RamGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self._env_backup = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["GUARDIAN_QUEUE_DB"] = str(Path(self.temp_dir.name) / "guardian.sqlite3")
        os.environ["FLEET_ROUTER"] = "false"
        os.environ["GUARDIAN_MIN_FREE_RAM_GB"] = "8"
        # Nothing listens on port 1: AIWA lookups and llama probes fail fast.
        os.environ["AIWA_BASE"] = "http://127.0.0.1:1"

        spec = importlib.util.spec_from_file_location(
            "guardian_ram_gate_test", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.real_available = self.module._available_ram_bytes

        m = self.module
        m.LLAMA_HOST = "127.0.0.1"
        m.LLAMA_PORT = 1
        m.guardian._client = aiohttp.ClientSession()
        m.guardian._llama_up = False

        self.pm2_calls: list[str] = []

        async def fake_pm2(action: str):
            self.pm2_calls.append(action)
            return True, ""

        async def no_cooldown(reason: str) -> None:
            return None

        m.pm2 = fake_pm2
        m._pm2_get_llama_pid = lambda: ("stopped", 0)
        m._gpu_cooldown = no_cooldown

        self.fake_available: int | None = 4 * GIB
        m._available_ram_bytes = lambda: self.fake_available

        app = web.Application()
        app.router.add_get("/__guardian/health", m.guardian_health)
        app.router.add_route("*", "/{tail:.*}", m.proxy_handler)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.module.guardian._client.close()
        self.module.guardian.job_store.close()
        self.temp_dir.cleanup()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.modules.pop("guardian_ram_gate_test", None)

    def _completion(self) -> dict:
        return {"model": "local-llm", "stream": False, "messages": [{"role": "user", "content": "hi"}]}

    # ── reading ────────────────────────────────────────────────────────────
    def test_real_reading_is_a_positive_byte_count(self) -> None:
        value = self.real_available()
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)

    # ── gate semantics ─────────────────────────────────────────────────────
    def test_disabled_gate_always_allows(self) -> None:
        self.module.MIN_FREE_RAM_GB = 0
        self.fake_available = 1 * GIB
        self.assertEqual((True, None), self.module.ram_ok_for_cold_start("test"))
        self.assertEqual(0, self.module.guardian.ram_refusals)

    def test_unknown_reading_allows(self) -> None:
        self.fake_available = None
        self.assertEqual((True, None), self.module.ram_ok_for_cold_start("test"))
        self.assertEqual(0, self.module.guardian.ram_refusals)

    def test_refusal_is_counted_and_recorded(self) -> None:
        allowed, available = self.module.ram_ok_for_cold_start("prewarm:3000")
        self.assertFalse(allowed)
        self.assertEqual(4 * GIB, available)
        g = self.module.guardian
        self.assertEqual(1, g.ram_refusals)
        self.assertEqual("prewarm:3000", g.last_ram_refusal["reason"])
        self.assertEqual(4.0, g.last_ram_refusal["available_gb"])
        self.assertEqual(8.0, g.last_ram_refusal["min_free_gb"])

    # ── ensure_llama_started ───────────────────────────────────────────────
    async def test_ensure_refuses_before_touching_pm2(self) -> None:
        self.assertFalse(await self.module.ensure_llama_started(reason="queue:j1"))
        self.assertEqual([], self.pm2_calls)
        self.assertEqual("ram_low", self.module.guardian.last_start_failure)
        self.assertIn("4.0 GB RAM free", self.module.start_failure_message())

    async def test_ensure_starts_when_ram_is_fine(self) -> None:
        self.fake_available = 16 * GIB

        async def never_up(timeout: float = 0.0) -> bool:
            return False

        self.module.guardian.wait_until_llama_up = never_up
        self.assertFalse(await self.module.ensure_llama_started(reason="request"))
        # Start was issued; the health-window failure then stops the orphan.
        self.assertEqual(["start", "stop"], self.pm2_calls)
        self.assertEqual("health_timeout", self.module.guardian.last_start_failure)
        self.assertEqual(0, self.module.guardian.ram_refusals)

    # ── HTTP surface ───────────────────────────────────────────────────────
    async def test_cold_request_gets_ram_low_503_and_no_start(self) -> None:
        ensure_calls: list[str] = []

        async def recording_ensure(*, reason: str = "") -> bool:
            ensure_calls.append(reason)
            return False

        self.module.ensure_llama_started = recording_ensure
        resp = await self.client.post("/v1/chat/completions", json=self._completion())
        body = await resp.json()
        self.assertEqual(503, resp.status)
        self.assertEqual("llama_ram_low", body["error"]["code"])
        self.assertEqual(4.0, body["error"]["available_gb"])
        self.assertEqual("300", resp.headers["Retry-After"])
        await asyncio.sleep(0)
        self.assertEqual([], ensure_calls)
        self.assertEqual([], self.pm2_calls)

    async def test_cold_request_still_warms_when_ram_is_fine(self) -> None:
        self.fake_available = 16 * GIB
        ensure_calls: list[str] = []

        async def recording_ensure(*, reason: str = "") -> bool:
            ensure_calls.append(reason)
            return False

        self.module.ensure_llama_started = recording_ensure
        resp = await self.client.post("/v1/chat/completions", json=self._completion())
        body = await resp.json()
        self.assertEqual(503, resp.status)
        self.assertEqual("llama_warming", body["error"]["code"])
        await asyncio.sleep(0)
        self.assertEqual(["request"], ensure_calls)

    async def test_health_reports_ram_gate(self) -> None:
        self.module.ram_ok_for_cold_start("request")  # one refusal on record
        resp = await self.client.get("/__guardian/health")
        body = await resp.json()
        self.assertEqual(200, resp.status)
        ram = body["ram"]
        self.assertEqual(4.0, ram["available_gb"])
        self.assertEqual(8.0, ram["min_free_gb"])
        self.assertFalse(ram["cold_start_allowed"])
        self.assertEqual(1, ram["refusals"])
        self.assertEqual("request", ram["last_refusal"]["reason"])


if __name__ == "__main__":
    unittest.main()
