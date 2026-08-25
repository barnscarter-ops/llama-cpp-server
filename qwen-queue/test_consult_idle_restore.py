"""PR7: consult idle restore — TTL gate, skip conditions, 409 swallowed, health no-op."""

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
from aiohttp.test_utils import TestServer
from unittest import mock

import aiwa_swap
import fleet_router

SCRIPTS_DIR = Path(__file__).resolve().parent
ENV_KEYS = ("FLEET_ROUTER", "GUARDIAN_QUEUE_DB", "AIWA_BASE", "CONSULT_IDLE_RESTORE_S")


class FakeOccupantCache:
    def __init__(self, occupant="consult", model_id="q", reachable=True):
        self._occupant = (occupant, model_id, reachable)

    async def get(self, client):
        return self._occupant


class Harness:
    """Load a fresh llama-guardian module and drive one poll iteration."""

    def __init__(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["GUARDIAN_QUEUE_DB"] = str(Path(self.temp_dir.name) / "guardian.sqlite3")
        os.environ["FLEET_ROUTER"] = "false"
        os.environ["AIWA_BASE"] = "http://127.0.0.1:1"
        spec = importlib.util.spec_from_file_location(
            f"guardian_restore_test_{id(self.temp_dir)}", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

        self.swap_calls: list[tuple] = []
        self.swap_response: object = web.json_response({"status": "ok", "occupant": "clerk"})
        self._orig_cache = fleet_router.occupant_cache
        self._orig_perform = aiwa_swap.perform_swap

    def set_state(self, *, ttl=0, occupant="consult", reachable=True,
                  idle_s=99999.0, swap_busy=False, running_jobs=0, last_known=False):
        m = self.module
        m.CONSULT_IDLE_RESTORE_S = ttl
        m.guardian._client = object()  # truthy stand-in
        fleet_router.occupant_cache = FakeOccupantCache(occupant, "model-id", reachable)
        if last_known:
            m.guardian.last_aiwa_consult_activity = time.time() - idle_s
        else:
            m.guardian.last_aiwa_consult_activity = 0.0
        aiwa_swap.swap_slot.busy = swap_busy
        m.guardian.job_store.summary = lambda: {"running": running_jobs, "queued": 0}
        aiwa_swap.perform_swap = self._fake_perform_swap
        return m

    async def _fake_perform_swap(self, to, client):
        self.swap_calls.append((to, client))
        return self.swap_response

    async def run_one_poll(self):
        """Run the restorer loop for exactly one check, then cancel via sleep."""
        sleeps = [0]

        async def fake_sleep(delay):
            sleeps[0] += 1
            if sleeps[0] > 1:
                raise asyncio.CancelledError
            return None

        with mock.patch("asyncio.sleep", fake_sleep):
            try:
                await self.module.consult_idle_restorer(object())
            except asyncio.CancelledError:
                pass

    def close(self):
        fleet_router.occupant_cache = self._orig_cache
        aiwa_swap.perform_swap = self._orig_perform
        aiwa_swap.swap_slot.busy = False
        self.module.guardian.job_store.close()
        self.temp_dir.cleanup()


class ConsultIdleRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._env_backup = {k: os.environ.get(k) for k in ENV_KEYS}
        self.h = Harness()

    async def asyncTearDown(self):
        self.h.close()
        for k, v in self._env_backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_ttl_zero_never_restores(self):
        h = self.h
        h.set_state(ttl=0, occupant="consult")
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_ttl_elapsed_consult_restores_once(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", idle_s=1500.0, last_known=True)
        await h.run_one_poll()
        self.assertEqual(len(h.swap_calls), 1)
        to, client = h.swap_calls[0]
        self.assertEqual(to, "clerk")
        self.assertIs(client, h.module.guardian._client)

    async def test_ttl_not_elapsed_no_restore(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", idle_s=10.0, last_known=True)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_unknown_activity_clock_starts_counting_not_restoring(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", last_known=False)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])
        self.assertGreater(h.module.guardian.last_aiwa_consult_activity, 0.0)

    async def test_occupant_clerk_skips(self):
        h = self.h
        h.set_state(ttl=1200, occupant="clerk", idle_s=1500.0, last_known=True)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_occupant_unreachable_skips(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", reachable=False,
                    idle_s=1500.0, last_known=True)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_swap_in_flight_skips(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", idle_s=1500.0,
                    last_known=True, swap_busy=True)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_running_job_skips(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", idle_s=1500.0,
                    last_known=True, running_jobs=1)
        await h.run_one_poll()
        self.assertEqual(h.swap_calls, [])

    async def test_flag_false_409_swallowed_task_survives(self):
        h = self.h
        h.swap_response = web.json_response(
            {"error": {"message": "FLEET_SWAP_OWNER is false", "code": "swap_board_owns_ssh"}},
            status=409,
        )
        h.set_state(ttl=1200, occupant="consult", idle_s=1500.0, last_known=True)
        # Task must complete normally (fake sleep cancels it after the check).
        await h.run_one_poll()
        self.assertEqual(len(h.swap_calls), 1)

    async def test_get_health_does_not_restore(self):
        h = self.h
        h.set_state(ttl=1200, occupant="consult", idle_s=1500.0, last_known=True)
        m = h.module
        m.guardian._client = aiohttp.ClientSession()
        try:
            app = m.make_app()
            async with TestServer(app) as server:
                async with aiohttp.ClientSession() as client:
                    async with client.get(f"{server.make_url('/__guardian/health')}") as resp:
                        self.assertEqual(resp.status, 200)
            self.assertEqual(h.swap_calls, [])
        finally:
            await m.guardian._client.close()


if __name__ == "__main__":
    unittest.main()
