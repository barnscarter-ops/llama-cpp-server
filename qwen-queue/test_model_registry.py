"""Step 1: model registry, readiness classification, read-only /__guardian/models."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fleet_router
import model_registry as mr
from guardian_http_harness import GuardianHarnessCase


def doc() -> dict:
    return json.loads((Path(mr.__file__).parent / "models.json").read_text(encoding="utf-8"))


class RegistryLoadTests(unittest.TestCase):
    def test_default_registry_loads_with_seed_models(self) -> None:
        reg = mr.load_registry()
        self.assertEqual(
            ["workbench-local", "nemotron-r9700", "qwen27b-r9700", "deepseek-flash"], reg.ids()
        )
        self.assertTrue(reg.get("qwen27b-r9700").needs_approval)
        self.assertEqual("pc", reg.get("workbench-local").egress)
        self.assertEqual("lan", reg.get("nemotron-r9700").egress)
        self.assertEqual("internet", reg.get("deepseek-flash").egress)

    def test_seats_map_to_the_models_fleet_router_already_resolves(self) -> None:
        reg = mr.load_registry()
        for alias, expected in (
            ("local-llm", "workbench-local"),
            ("clerk", "nemotron-r9700"),
            ("nemotron-3.5-lightning-30b-a3b", "nemotron-r9700"),
            ("consult", "qwen27b-r9700"),
            ("qwen3.8-27b", "qwen27b-r9700"),
        ):
            self.assertEqual(expected, reg.for_seat(fleet_router.seat_for_model(alias)).id, alias)
        self.assertEqual("cloud", fleet_router.seat_for_model("deepseek-flash"))
        self.assertIsNone(reg.for_seat("cloud"))

    def test_env_override_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(json.dumps(doc()), encoding="utf-8")
            with mock.patch.dict(os.environ, {"GUARDIAN_MODELS_FILE": str(path)}):
                self.assertEqual(4, len(mr.load_registry()))

    def test_missing_or_corrupt_file_is_registry_error(self) -> None:
        with self.assertRaises(mr.RegistryError):
            mr.load_registry("/nonexistent/models.json")
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{nope", encoding="utf-8")
            with self.assertRaises(mr.RegistryError):
                mr.load_registry(bad)


class RegistryValidationTests(unittest.TestCase):
    def mutate(self, index: int, **changes):
        d = copy.deepcopy(doc())
        d["models"][index].update(changes)
        return d

    def test_rejections(self) -> None:
        d = doc()
        dup = copy.deepcopy(d)
        dup["models"][1]["id"] = dup["models"][0]["id"]
        seat_dup = copy.deepcopy(d)
        seat_dup["models"][1]["seat"] = "glm"
        missing = copy.deepcopy(d)
        del missing["models"][0]["egress"]
        cases = {
            "duplicate id": dup,
            "duplicate seat": seat_dup,
            "missing field": missing,
            "bad egress": self.mutate(0, egress="wan"),
            "bad locality": self.mutate(0, locality="edge"),
            "bad host": self.mutate(0, host="moon"),
            "bad tier": self.mutate(0, quality_tier=4),
            "local concurrency 2": self.mutate(0, max_concurrent=2),
            "local with cost": self.mutate(0, cost_per_1k_in=0.1),
            "local without seat": self.mutate(0, seat=None),
            "negative wake": self.mutate(0, wake_seconds=-1),
            "bool as number": self.mutate(0, context_tokens=True),
            "zero context": self.mutate(0, context_tokens=0),
            "cloud on lan": self.mutate(3, egress="lan"),
            "not an object": {"models": ["x"]},
            "no models array": {"models": {}},
        }
        for name, document in cases.items():
            with self.subTest(name), self.assertRaises(mr.RegistryError):
                mr.parse_registry(document)

    def test_egress_rank_is_narrowest_to_widest(self) -> None:
        self.assertLess(mr.egress_rank("pc"), mr.egress_rank("lan"))
        self.assertLess(mr.egress_rank("lan"), mr.egress_rank("internet"))
        with self.assertRaises(mr.RegistryError):
            mr.egress_rank("none")


def probe(*, wb=None, aiwa=("clerk", "m", True), cloud=None) -> mr.ReadinessProbe:
    async def aiwa_provider():
        return aiwa

    return mr.ReadinessProbe(
        (lambda: wb or mr.WorkbenchState(llama_up=False)),
        aiwa_provider,
        (lambda spec: cloud or mr.CloudState(enabled=True, key_present=True)),
    )


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.reg = mr.load_registry()

    async def state(self, model_id: str, **kw) -> mr.Readiness:
        return await probe(**kw).readiness(self.reg.get(model_id))

    async def test_workbench_states(self) -> None:
        up = await self.state("workbench-local", wb=mr.WorkbenchState(llama_up=True))
        self.assertEqual(mr.READY, up.state)
        asleep = await self.state("workbench-local", wb=mr.WorkbenchState(llama_up=False))
        self.assertEqual((mr.WAKEABLE, 45.0), (asleep.state, asleep.seconds))
        cooling = await self.state(
            "workbench-local", wb=mr.WorkbenchState(llama_up=False, cooldown_remaining_s=20)
        )
        self.assertEqual(65.0, cooling.seconds)
        ram = await self.state("workbench-local", wb=mr.WorkbenchState(llama_up=False, ram_ok=False))
        self.assertEqual((mr.UNAVAILABLE, "ram_low"), (ram.state, ram.reason))
        gpu = await self.state(
            "workbench-local", wb=mr.WorkbenchState(llama_up=False, last_start_failure="gpu_placement")
        )
        self.assertEqual("gpu_placement", gpu.reason)

    async def test_r9700_states(self) -> None:
        self.assertEqual(mr.READY, (await self.state("nemotron-r9700", aiwa=("clerk", "n", True))).state)
        self.assertEqual(mr.READY, (await self.state("qwen27b-r9700", aiwa=("consult", "q", True))).state)
        qwen = await self.state("qwen27b-r9700", aiwa=("clerk", "n", True))
        self.assertEqual((mr.NEEDS_APPROVAL, 60.0), (qwen.state, qwen.seconds))
        nemo = await self.state("nemotron-r9700", aiwa=("consult", "q", True))
        self.assertEqual((mr.WAKEABLE, 60.0), (nemo.state, nemo.seconds))
        for model in ("nemotron-r9700", "qwen27b-r9700"):
            down = await self.state(model, aiwa=("unknown", None, False))
            self.assertEqual((mr.UNAVAILABLE, "aiwa_unreachable"), (down.state, down.reason))
            odd = await self.state(model, aiwa=("unknown", "mystery", True))
            self.assertEqual("aiwa_occupant_unknown", odd.reason)

    async def test_cloud_states(self) -> None:
        ok = await self.state("deepseek-flash")
        self.assertEqual(mr.READY, ok.state)
        for kw, reason in (
            (dict(enabled=False, key_present=True), "cloud_disabled"),
            (dict(enabled=True, key_present=False), "no_key"),
            (dict(enabled=True, key_present=True, cap_left=False), "spend_cap"),
            (dict(enabled=True, key_present=True, slot_free=False), "no_slot"),
        ):
            got = await self.state("deepseek-flash", cloud=mr.CloudState(**kw))
            self.assertEqual((mr.UNAVAILABLE, reason), (got.state, got.reason), reason)

    def test_default_cloud_state_needs_flag_and_key(self) -> None:
        spec = self.reg.get("deepseek-flash")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GUARDIAN_CLOUD", None)
            os.environ.pop("GUARDIAN_DEEPSEEK_API_KEY", None)
            self.assertFalse(mr.env_cloud_state(spec).enabled)
        with mock.patch.dict(os.environ, {"GUARDIAN_CLOUD": "true", "GUARDIAN_DEEPSEEK_API_KEY": "k"}):
            state = mr.env_cloud_state(spec)
            self.assertTrue(state.enabled and state.key_present)

    async def test_snapshot_shape_never_includes_key_env_or_secrets(self) -> None:
        with mock.patch.dict(os.environ, {"GUARDIAN_DEEPSEEK_API_KEY": "sk-secret-value"}):
            snap = await probe().snapshot(self.reg)
        text = json.dumps(snap)
        self.assertNotIn("sk-secret-value", text)
        self.assertNotIn("key_env", text)
        self.assertEqual(4, len(snap))
        self.assertTrue(all("readiness" in e for e in snap))


class ModelsEndpointTests(GuardianHarnessCase):
    async def test_endpoint_returns_registry_and_never_touches_llama(self) -> None:
        self.aiwa.occupant_id = "nemotron-3.5-lightning-30b-a3b"
        resp = await self.client.get("/__guardian/models")
        self.assertEqual(200, resp.status, await resp.text())
        body = await resp.json()
        by_id = {m["id"]: m for m in body["models"]}
        self.assertEqual(["workbench-local", "nemotron-r9700", "qwen27b-r9700", "deepseek-flash"], list(by_id))
        self.assertEqual("wakeable", by_id["workbench-local"]["readiness"]["state"])
        self.assertEqual("ready", by_id["nemotron-r9700"]["readiness"]["state"])
        self.assertEqual("needs_approval", by_id["qwen27b-r9700"]["readiness"]["state"])
        self.assertEqual("cloud_disabled", by_id["deepseek-flash"]["readiness"]["reason"])
        self.assertEqual([], self.llama.hits, "Workbench readiness must not call llama-server (it wakes it)")

    async def test_asleep_workbench_stays_asleep_across_repeated_reads(self) -> None:
        for _ in range(3):
            resp = await self.client.get("/__guardian/models")
            self.assertEqual(200, resp.status)
        self.assertEqual([], self.llama.hits)

    async def test_workbench_ready_when_llama_flagged_up(self) -> None:
        self.module.guardian._llama_up = True
        body = await (await self.client.get("/__guardian/models")).json()
        wb = next(m for m in body["models"] if m["id"] == "workbench-local")
        self.assertEqual("ready", wb["readiness"]["state"])
        self.assertEqual([], self.llama.hits)

    async def test_remote_caller_without_token_is_refused(self) -> None:
        original = self.module._queue_authorized
        self.module._queue_authorized = lambda request: False
        try:
            resp = await self.client.get("/__guardian/models")
        finally:
            self.module._queue_authorized = original
        self.assertEqual(403, resp.status)

    async def test_corrupt_registry_is_500_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GUARDIAN_MODELS_FILE": str(bad)}):
                resp = await self.client.get("/__guardian/models")
        self.assertEqual(500, resp.status)
        self.assertEqual("model_registry_invalid", (await resp.json())["error"]["code"])


if __name__ == "__main__":
    unittest.main()
