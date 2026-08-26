"""PR6: FLEET_SWAP_OWNER swap ownership — flag, WORKBOARD, SSH classify, cap 1."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import aiwa_swap

SCRIPTS_DIR = Path(__file__).resolve().parent


class EnvBackup:
    def setUp(self) -> None:
        self._env_backup: dict[str, str | None] = {}
        for key in ("FLEET_SWAP_OWNER", "WORKBOARD_PATH"):
            self._env_backup[key] = os.environ.get(key)
        super().setUp()

    def teardown_env(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class FakeSsh:
    def __init__(self, exit_code=0, combined=""):
        self.exit_code = exit_code
        self.combined = combined
        self.calls: list[str] = []

    async def __call__(self, to: str) -> tuple[int, str]:
        self.calls.append(to)
        return self.exit_code, self.combined


class FakePorts:
    def __init__(self, open_ports):
        self.open_ports = set(open_ports)
        self.calls: list[int] = []

    async def __call__(self, port: int) -> bool:
        self.calls.append(port)
        return port in self.open_ports


def make_workboard(tmp: str) -> str:
    path = Path(tmp) / "WORKBOARD.md"
    path.write_text(
        "# Workboard\n\n| Workstream | Owner agent | Where | Status (updated) |\n"
        "|---|---|---|---|\n"
        "| other line | someone | somewhere | busy |\n",
        encoding="utf-8",
    )
    return str(path)


class AiwaSwapTests(EnvBackup, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workboard = make_workboard(self.temp_dir.name)
        os.environ["WORKBOARD_PATH"] = self.workboard
        os.environ.pop("FLEET_SWAP_OWNER", None)
        aiwa_swap.swap_slot.busy = False
        self._ssh = FakeSsh()
        self._ports = FakePorts([])
        self._orig_ssh = aiwa_swap.run_swap_ssh
        self._orig_ports = aiwa_swap.console_port_open
        self._orig_wait = aiwa_swap.wait_for_occupant
        aiwa_swap.run_swap_ssh = self._ssh
        aiwa_swap.console_port_open = self._ports
        self.occupant_ok = True
        aiwa_swap.wait_for_occupant = self._fake_wait

    async def _fake_wait(self, client, to, timeout=aiwa_swap.SWAP_POLL_TIMEOUT_S):
        return self.occupant_ok

    async def asyncTearDown(self) -> None:
        aiwa_swap.run_swap_ssh = self._orig_ssh
        aiwa_swap.console_port_open = self._orig_ports
        aiwa_swap.wait_for_occupant = self._orig_wait
        self.temp_dir.cleanup()
        self.teardown_env()

    async def _body(self, to="consult", gate="operator"):
        return await aiwa_swap.perform_swap(to, client=None, gate=gate)

    async def test_consult_swap_without_gate_403(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        resp = await self._body(gate="")
        self.assertEqual(403, resp.status)
        body = __import__("json").loads(resp.text)
        self.assertEqual("consult_gate_required", body["error"]["code"])
        self.assertEqual([], self._ssh.calls)

    async def test_consult_swap_rejects_worker_gate(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        resp = await self._body(gate="worker")
        self.assertEqual(403, resp.status)
        self.assertEqual("consult_gate_required", __import__("json").loads(resp.text)["error"]["code"])
        self.assertEqual([], self._ssh.calls)

    async def test_flag_false_409_even_with_console_open(self) -> None:
        os.environ.pop("FLEET_SWAP_OWNER", None)
        self._ports.open_ports = {17890, 17891}
        resp = await self._body()
        self.assertEqual(409, resp.status)
        body = resp.text and __import__("json").loads(resp.text)
        self.assertEqual("swap_board_owns_ssh", body["error"]["code"])
        # No TCP-accept check, no SSH, no WORKBOARD write when the flag is false.
        self.assertEqual([], self._ports.calls)
        self.assertEqual([], self._ssh.calls)

    async def test_flag_true_missing_workboard_503_no_ssh(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        os.environ["WORKBOARD_PATH"] = str(Path(self.temp_dir.name) / "absent.md")
        resp = await self._body()
        self.assertEqual(503, resp.status)
        body = __import__("json").loads(resp.text)
        self.assertEqual("workboard_missing", body["error"]["code"])
        self.assertEqual([], self._ssh.calls)

    async def test_second_in_flight_swap_409_aiwa_busy(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        release = asyncio.Event()

        async def slow_ssh(to):
            await release.wait()
            return 0, ""

        aiwa_swap.run_swap_ssh = slow_ssh
        first = asyncio.create_task(self._body())
        await asyncio.sleep(0.05)
        second = await self._body()
        self.assertEqual(409, second.status)
        self.assertEqual("aiwa_busy", __import__("json").loads(second.text)["error"]["code"])
        release.set()
        self.assertEqual(200, (await first).status)

    async def test_mutex_busy_stderr_409_aiwa_busy(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        aiwa_swap.run_swap_ssh = FakeSsh(1, "swap in progress (lock /opt/llama/swap.lock)")
        resp = await self._body()
        self.assertEqual(409, resp.status)
        self.assertEqual("aiwa_busy", __import__("json").loads(resp.text)["error"]["code"])

    async def test_no_such_file_502_swap_scripts_missing(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        aiwa_swap.run_swap_ssh = FakeSsh(127, "/opt/llama/swap-qwen-consult.sh: No such file or directory")
        resp = await self._body()
        self.assertEqual(502, resp.status)
        self.assertEqual("swap_scripts_missing", __import__("json").loads(resp.text)["error"]["code"])

    async def test_ssh_connect_fail_502_swap_ssh_failed(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        aiwa_swap.run_swap_ssh = FakeSsh(255, "ssh: connect to host 192.168.1.12 port 22: Connection refused")
        resp = await self._body()
        self.assertEqual(502, resp.status)
        self.assertEqual("swap_ssh_failed", __import__("json").loads(resp.text)["error"]["code"])

    async def test_console_port_open_still_allowed_warn_only(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        self._ports.open_ports = {17890}
        resp = await self._body()
        self.assertEqual(200, resp.status)
        self.assertIn(17890, self._ports.calls)

    async def test_success_announces_workboard_row(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        resp = await self._body("clerk")
        self.assertEqual(200, resp.status)
        text = Path(self.workboard).read_text(encoding="utf-8")
        self.assertIn(aiwa_swap.WORKSTREAM, text)
        self.assertIn("consult→clerk", text)
        self.assertEqual(["clerk"], self._ssh.calls)

    async def test_occupant_timeout_502(self) -> None:
        os.environ["FLEET_SWAP_OWNER"] = "true"
        self.occupant_ok = False
        resp = await self._body()
        self.assertEqual(502, resp.status)
        self.assertEqual("aiwa_unreachable", __import__("json").loads(resp.text)["error"]["code"])


class SwapRouteOrderTests(EnvBackup, unittest.IsolatedAsyncioTestCase):
    """POST /__guardian/swap must be served by the guardian, not the proxy."""

    async def asyncSetUp(self) -> None:
        os.environ["FLEET_ROUTER"] = "false"
        os.environ["HERMES_DECIDER_ENABLED"] = "false"
        os.environ.pop("FLEET_SWAP_OWNER", None)
        spec = importlib.util.spec_from_file_location(
            "guardian_swap_route_test", SCRIPTS_DIR / "llama-guardian.py"
        )
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

        async def catchall(request):
            return web.json_response({"error": {"code": "proxied"}}, status=599)

        app = web.Application()
        app.router.add_post("/__guardian/swap", self.module.guardian_swap)
        app.router.add_route("*", "/{tail:.*}", catchall)
        self.client = TestClient(TestServer(app, host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.module.guardian.job_store.close()
        sys.modules.pop("guardian_swap_route_test", None)
        self.teardown_env()

    async def test_route_registered_before_catch_all_flag_false(self) -> None:
        response = await self.client.post("/__guardian/swap", json={"to": "consult"})
        self.assertEqual(403, response.status)
        self.assertEqual(
            "consult_gate_required", (await response.json())["error"]["code"]
        )

    async def test_route_consult_with_gate_flag_false_is_board_owns_ssh(self) -> None:
        response = await self.client.post(
            "/__guardian/swap", json={"to": "consult", "gate": "operator"}
        )
        self.assertEqual(409, response.status)
        self.assertEqual(
            "swap_board_owns_ssh", (await response.json())["error"]["code"]
        )

    async def test_remote_swap_forbidden(self) -> None:
        # Loopback auth identical to jobs: token unset + allow_remote false →
        # a non-loopback remote is rejected before any swap logic runs.
        self.assertFalse(
            self.module._queue_authorized(
                type("R", (), {"remote": "192.168.1.50", "headers": {}})()
            )
        )


if __name__ == "__main__":
    unittest.main()
