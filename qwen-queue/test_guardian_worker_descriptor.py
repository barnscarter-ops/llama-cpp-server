"""Offline producer/handler tests. Never import or start the Guardian daemon."""
import ast
import asyncio
import copy
import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import guardian_worker_descriptor as descriptor
from guardian_workers import worker_policies


def build(work_class="mechanical_execution", **changes):
    args = dict(observed_at=1000, enabled=False, workbench_up=True,
                aiwa_cache={"fetchedAt": 0.9, "reachable": True, "modelId": "nemotron-3.5-lightning-30b-a3b"})
    args.update(changes)
    return descriptor.build_worker_descriptor(work_class, **args)


class DescriptorTests(unittest.TestCase):
    def test_existing_policy_projection_digest_and_no_private_material(self):
        policies = worker_policies()
        policies["tool_execution"] = replace(policies["tool_execution"], skill_path="SYNTHETIC_SECRET_PATH")
        with patch.object(descriptor, "worker_policies", return_value=policies):
            d = build("tool_execution")
        self.assertEqual(d["policy"]["provider"], "llamacpp")
        self.assertEqual(d["policy"]["model"], "local-llm")
        encoded = json.dumps(d["policy"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        self.assertEqual(d["policyRevision"], hashlib.sha256(encoded).hexdigest())
        self.assertNotIn("SYNTHETIC_SECRET_PATH", json.dumps(d))
        self.assertEqual(d["policy"]["scope"], "non-secret-projection")
        self.assertNotIn("route", d["policy"])

    def test_pause_and_unknown_authority_survive_enabled_true(self):
        d = build(enabled=True)
        self.assertEqual(d["policy"]["runnerStatus"], "paused")
        self.assertIsNone(d["policy"]["capabilities"])
        self.assertEqual(d["authEntitlement"], "unavailable")
        for field in ("transportReady", "capacityReady"):
            self.assertIsNone(d[field])
        for field in ("executionAuthorized", "capacityReserved"):
            self.assertIs(d[field], False)

    def test_cache_selection_follows_work_class_not_presence_of_other_cache(self):
        workbench = build("tool_execution")
        self.assertEqual(workbench["readiness"], {"source": "workbench-health-cache", "observedAt": None, "reachable": True, "modelId": None})
        with self.assertRaises(ValueError):
            build(aiwa_cache=None)
        aiwa = build(aiwa_cache={})
        self.assertEqual(aiwa["readiness"], {"source": "aiwa-occupant-cache", "observedAt": None, "reachable": None, "modelId": None})

    def test_cache_time_keeps_original_stale_future_and_fractional_measurement(self):
        for seconds, expected in ((0.01, 10), (10.0, 10000), (1234.56789, 1234567)):
            with self.subTest(seconds=seconds):
                d = build(aiwa_cache={"fetchedAt": seconds})
                self.assertEqual(d["readiness"]["observedAt"], expected)
                self.assertEqual(d["observedAt"], 1000)

    def test_actual_cache_source_uses_epoch_seconds_not_milliseconds(self):
        tree = ast.parse(Path(__file__).with_name("fleet_router.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OccupantCache")
        writes = [n.value for n in ast.walk(cls) if isinstance(n, (ast.Assign, ast.AnnAssign))
                  and any(isinstance(t, ast.Attribute) and t.attr == "_fetched_at"
                          for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
        self.assertGreaterEqual(len(writes), 4)
        for value in writes:
            if isinstance(value, ast.Constant):
                self.assertEqual(value.value, 0)
            else:
                self.assertIsInstance(value, ast.Call)
                self.assertEqual(ast.unparse(value), "time.time()")

    def test_invalid_cache_time_and_types_stay_unknown(self):
        for value in (None, True, False, "1", -1, 0, float("nan"), float("inf"), 10**400):
            with self.subTest(value=str(value)[:30]):
                d = build(aiwa_cache={"fetchedAt": value, "reachable": "true", "modelId": 123})
                self.assertIsNone(d["readiness"]["observedAt"])
                self.assertIsNone(d["readiness"]["reachable"])
                self.assertIsNone(d["readiness"]["modelId"])

    def test_unsafe_cache_identifiers_never_leave_producer(self):
        for model in ("https://secret.example/token", "http://user:password@host", "C:/private/model", "\\private\\file", "x"*257, "bad\nvalue", "nonascii-é"):
            with self.subTest(model=model):
                self.assertIsNone(build(aiwa_cache={"modelId": model})["readiness"]["modelId"])

    def test_invalid_generation_time_and_enablement_rejected(self):
        for value in (-1, True, "1000", 1.1, float("nan"), 2**53):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build(observed_at=value)
        for value in (None, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build(enabled=value)

    def test_unsupported_classes_are_not_execution_or_cloud_fallback(self):
        for value in ("planning", "deep_analysis", "frontier", "unknown", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build(value)

    def test_policy_route_runner_identity_and_model_drift_fail_closed(self):
        original = worker_policies()
        for changes in ({"route": "new-route"}, {"runner": "new-runner"}, {"work_class": "tool_execution"}, {"consult": True}, {"pi_model": "https://private/model"}, {"pi_model": None}):
            policies = dict(original)
            policies["mechanical_execution"] = replace(policies["mechanical_execution"], **changes)
            with self.subTest(changes=changes), patch.object(descriptor, "worker_policies", return_value=policies), self.assertRaises(ValueError):
                build()

    def test_input_is_not_mutated_and_public_revision_tracks_public_model(self):
        cache = {"fetchedAt": 1, "reachable": True, "modelId": "model", "endpoint": "PRIVATE"}
        before = copy.deepcopy(cache)
        first = build(aiwa_cache=cache)
        self.assertEqual(cache, before)
        self.assertNotIn("PRIVATE", json.dumps(first))
        policies = worker_policies()
        policies["mechanical_execution"] = replace(policies["mechanical_execution"], pi_model="llamacpp-690/other-model")
        with patch.object(descriptor, "worker_policies", return_value=policies):
            self.assertNotEqual(first["policyRevision"], build()["policyRevision"])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        # Compile only these actual handler definitions. Importing the daemon could open
        # its durable queue; this test deliberately supplies inert local dependencies.
        source = Path(__file__).with_name("llama-guardian.py").read_text(encoding="utf-8")
        self.tree = ast.parse(source)
        functions = [n for n in self.tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in {"worker_descriptor_v1", "worker_profiles"}]
        cache = SimpleNamespace(_fetched_at=0.9, reachable=True, model_id="nemotron-3.5-lightning-30b-a3b", get=Mock(side_effect=AssertionError("cache refresh forbidden")))
        self.guardian = SimpleNamespace(_llama_up=True)
        self.auth = Mock(return_value=True)
        self.ns = {"web": SimpleNamespace(Request=object, Response=object, json_response=lambda body, status=200: (body, status)),
                   "_queue_authorized": self.auth, "_queue_forbidden": lambda: ({"error": "forbidden"}, 403),
                   "guardian": self.guardian, "fleet_router": SimpleNamespace(occupant_cache=cache),
                   "time": SimpleNamespace(time=lambda: 1), "workers_enabled": lambda: False,
                   "profiles_as_api": lambda: [{"work_class": "existing"}], "build_worker_descriptor": descriptor.build_worker_descriptor}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "offline-actual-handler", "exec"), self.ns)

    def call(self, name="worker_descriptor_v1", work_class="mechanical_execution"):
        return asyncio.run(self.ns[name](SimpleNamespace(match_info={"work_class": work_class})))

    def test_actual_handler_uses_actual_producer_without_refresh(self):
        body, status = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(body, build())
        self.ns["fleet_router"].occupant_cache.get.assert_not_called()

    def test_existing_auth_rejects_before_policy_or_cache_access(self):
        self.auth.return_value = False
        self.ns["build_worker_descriptor"] = Mock(side_effect=AssertionError("must not inspect policy"))
        self.ns["fleet_router"] = None
        self.assertEqual(self.call()[1], 403)

    def test_unknown_class_is_rejected_without_producer(self):
        self.ns["build_worker_descriptor"] = Mock(side_effect=AssertionError("must not inspect policy"))
        self.assertEqual(self.call(work_class="planning")[1], 400)

    def test_producer_error_is_generic_and_contains_no_private_details(self):
        self.ns["build_worker_descriptor"] = Mock(side_effect=ValueError("PRIVATE_POLICY_PATH"))
        body, status = self.call()
        self.assertEqual(status, 503)
        self.assertNotIn("PRIVATE", json.dumps(body))

    def test_missing_cache_attribute_is_generic_unavailable(self):
        del self.ns["fleet_router"].occupant_cache._fetched_at
        self.assertEqual(self.call(), ({"error": {"code": "worker_descriptor_unavailable"}}, 503))

    def test_existing_profiles_api_remains_unchanged(self):
        self.assertEqual(self.call("worker_profiles"), ({"enabled": False, "profiles": [{"work_class": "existing"}]}, 200))

    def test_explicit_versioned_get_and_existing_get_post_routes_remain(self):
        routes = {(n.func.attr, n.args[0].value) for n in ast.walk(self.tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"add_get", "add_post"}
                  and n.args and isinstance(n.args[0], ast.Constant)}
        self.assertIn(("add_get", "/__guardian/workers/descriptor/v1/{work_class}"), routes)
        self.assertIn(("add_get", "/__guardian/workers"), routes)
        self.assertIn(("add_post", "/__guardian/workers"), routes)


if __name__ == "__main__":
    unittest.main()
