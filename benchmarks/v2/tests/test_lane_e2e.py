"""Whole lanes on the fake machine: stages, restore guarantees, abort paths."""

import json
import unittest
from urllib.parse import urlsplit

from lane_fixture import event_kinds, make_env
from bench_v2.fakes import FakeBackend, default_perf
from bench_v2.safety import RestoreFailed

ALL = ["smoke", "tune", "confirm", "ladder", "quality"]


def kinds(env, kind):
    return [r for r in env.lane.ledger.records() if r.get("kind") == kind]


class WorkbenchLane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = make_env("workbench")
        cls.report = cls.env.lane.run_stages(ALL)

    def test_all_stages_ran_for_the_incumbent(self):
        for st in ALL:
            self.assertIn("qwen36-35b-a3b-iq3", self.report[st], st)
        self.assertNotIn("aborted", self.report)

    def test_production_services_restored_and_marker_cleared(self):
        self.assertEqual(self.env.world.services, {"llama-guardian": True, "local-llm": True})
        self.assertFalse(self.env.lane.markers.restore_pending())
        k = event_kinds(self.env.lane)
        self.assertLess(k.index("safety.stop"), k.index("header"))
        self.assertGreater(k.index("safety.restore"), k.index("role_verdict"))

    def test_ledger_chain_verifies(self):
        self.assertGreater(self.env.lane.ledger.verify(), 100)

    def test_no_request_left_the_bench_port(self):
        ports = {urlsplit(u).port for u, _ in self.env.world.requests}
        self.assertEqual(ports, {8099})
        self.assertEqual(self.env.lane.backend.violations, [])

    def test_no_request_to_forbidden_hosts(self):
        for u, _ in self.env.world.requests:
            self.assertNotIn("192.168.1.240", u)
            self.assertNotIn(":8080", u)
            self.assertNotIn(":8081", u)

    def test_every_launch_has_full_context_and_gpu_layers(self):
        for argv in self.env.world.spawned:
            self.assertIn("131072", argv)
            self.assertIn("-ngl", argv)
            self.assertNotIn("--n-cpu-moe", argv)  # exception path is off without the window approval

    def test_forced_q8_for_the_incumbent(self):
        tuned = kinds(self.env, "tuned")[-1]
        self.assertEqual(tuned["cfg"]["kv"], "q8_0")
        self.assertEqual(tuned["cfg"]["ctx"], 131072)

    def test_ladder_and_cold_load_recorded(self):
        d = kinds(self.env, "ladder_done")[-1]
        self.assertEqual(d["max_safe_ctx"], 131072)
        self.assertIsNotNone(d["wake_seconds"])

    def test_quality_records_and_verdict_present(self):
        self.assertTrue(kinds(self.env, "role_verdict"))
        self.assertGreater(sum(1 for r in self.env.lane.ledger.records() if r.get("phase") == "quality"), 250)

    def test_no_secret_in_ledger(self):
        text = (self.env.root / "ledger" / "workbench.jsonl").read_text()
        for needle in ("Bearer", "api_key", "sk-"):
            self.assertNotIn(needle, text)


class AbortPaths(unittest.TestCase):
    def assert_restored(self, env):
        self.assertEqual(env.world.services, {"llama-guardian": True, "local-llm": True})
        self.assertFalse(env.lane.markers.restore_pending())
        env.lane.ledger.verify()

    def test_double_crash_aborts_and_restores(self):
        env = make_env("workbench")
        # smoke runs at the default ub; any later launch with another ub crashes
        env.world.crash_when = lambda p: "crash" if p["ub"] != 512 else None
        rep = env.lane.run_stages(ALL)
        self.assertIn("aborted", rep)
        self.assertEqual(rep["aborted"]["code"], "double_crash")
        self.assert_restored(env)

    def test_oom_is_recorded_not_fatal_once(self):
        env = make_env("workbench", crash_when=lambda p: "oom" if p["ub"] == 1024 else None)
        rep = env.lane.run_stages(["smoke", "tune"])
        self.assertNotIn("aborted", rep)
        self.assertTrue(any(r.get("status") == "oom" for r in env.lane.ledger.records()))
        self.assert_restored(env)

    def test_power_alarm_aborts_and_restores(self):
        env = make_env("workbench", power_alarm_after_requests=30)
        rep = env.lane.run_stages(ALL)
        self.assertIn("aborted", rep)
        self.assertIn(rep["aborted"]["code"], ("power", "power_alarm"))
        self.assert_restored(env)

    def test_stop_file_aborts_and_restores(self):
        env = make_env("workbench")
        (env.root / "runs").mkdir(exist_ok=True)
        (env.root / "runs" / "workbench.STOP").write_text("")
        rep = env.lane.run_stages(ALL)
        self.assertEqual(rep["aborted"]["code"], "stop_file")
        self.assert_restored(env)

    def test_over_temperature_aborts(self):
        env = make_env("workbench", temp_c=95.0)
        rep = env.lane.run_stages(["smoke"])
        self.assertIn("aborted", rep)
        self.assert_restored(env)

    def test_service_that_will_not_stop_aborts_before_any_load(self):
        env = make_env("workbench", stop_fails=True)
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "stop_failed")
        self.assertEqual(env.world.spawned, [])
        self.assertTrue(env.world.services["llama-guardian"])

    def test_gpu_not_idle_aborts_before_any_load(self):
        env = make_env("workbench", other_gpu_mib=6000.0)
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "gpu_busy")
        self.assertEqual(env.world.spawned, [])
        self.assert_restored(env)

    def test_no_window_means_nothing_starts(self):
        env = make_env("workbench")
        env.lane.s.window_path = None
        with self.assertRaises(Exception):
            env.lane.run_stages(["smoke"])
        self.assertEqual(env.world.spawned, [])
        self.assertEqual(env.world.cmd_log, [])

    def test_restore_marker_blocks_a_second_run(self):
        env = make_env("workbench")
        env.lane.markers.mark_restore_required({"was_active": ["llama-guardian"]})
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "restore_pending")
        self.assertEqual(env.world.spawned, [])

    def test_restore_now_recovers_a_crashed_run(self):
        env = make_env("workbench")
        env.world.services = {"llama-guardian": False, "local-llm": False}
        env.lane.markers.mark_restore_required({"was_active": ["llama-guardian", "local-llm"]})
        out = env.lane.restore_now()
        self.assertTrue(out["restored"])
        self.assertEqual(env.world.services, {"llama-guardian": True, "local-llm": True})
        self.assertFalse(env.lane.markers.restore_pending())

    def test_failed_restore_leaves_the_marker(self):
        class NoStart(FakeBackend):
            def run(self, cmd, timeout=120.0, env=None):
                if "restart" in " ".join(cmd):
                    return super().run(["true"], timeout, env)
                return super().run(cmd, timeout, env)

        env = make_env("workbench", backend_cls=NoStart)
        with self.assertRaises(RestoreFailed):
            env.lane.run_stages(["smoke"])
        self.assertTrue(env.lane.markers.restore_pending())

    def test_only_previously_active_services_are_started(self):
        env = make_env("workbench")
        env.world.services["local-llm"] = False
        env.lane.run_stages(["smoke"])
        self.assertFalse(env.world.services["local-llm"])
        self.assertTrue(env.world.services["llama-guardian"])


class MoeExceptionAndAlternates(unittest.TestCase):
    def test_exception_models_not_run_without_window_approval(self):
        env = make_env("workbench")
        ids = {c.id for c in env.lane.candidates(include_exception=True)}
        env.lane.load_window()
        self.assertNotIn("qwen36-35b-a3b-q4km-offload", {c.id for c in env.lane.candidates()})
        self.assertIn("qwen36-35b-a3b-q4km-offload", ids)

    def test_exception_runs_only_after_incumbent_misses_bar(self):
        env = make_env("workbench", approvals={"moe_exception": True}, oracle=False)
        env.lane.load_window()
        self.assertFalse(env.lane._exception_triggered())
        env.lane.ledger.event("role_verdict", model="qwen36-35b-a3b-iq3", role="worker_bee", verdict={"pass": False})
        self.assertTrue(env.lane._exception_triggered())


class R9700Lane(unittest.TestCase):
    def test_qwen_reasoner_needs_window_approval(self):
        env = make_env("r9700")
        env.lane.load_window()
        self.assertNotIn("qwen3.8-27b-q6k", {c.id for c in env.lane.candidates()})
        env2 = make_env("r9700", approvals={"qwen_load": True})
        env2.lane.load_window()
        self.assertIn("qwen3.8-27b-q6k", {c.id for c in env2.lane.candidates()})

    def test_lane_runs_and_restores_nemotron_service(self):
        env = make_env("r9700", only=["nemotron-3.5-lightning-q5km"])
        rep = env.lane.run_stages(["smoke", "tune", "confirm", "ladder"])
        self.assertNotIn("aborted", rep, rep.get("aborted"))
        self.assertEqual(env.world.services, {"llama-server": True})
        self.assertFalse(env.lane.markers.restore_pending())
        self.assertEqual({urlsplit(u).port for u, _ in env.world.requests}, {8090})

    def test_swap_test_restores_nemotron_and_sends_no_request_at_all(self):
        env = make_env("r9700", approvals={"qwen_load": True, "swap_test": True})
        out = env.lane.swap_test()
        self.assertEqual(len(out["to_qwen_s"]), 3)
        self.assertEqual(len(out["to_nemotron_s"]), 3)
        self.assertIsNone(out["failed"])
        self.assertFalse(env.lane.markers.restore_pending())
        self.assertEqual(env.world.services, {"llama-server": True})
        self.assertIn("Nemotron", env.world.resident)
        self.assertEqual(env.world.requests, [])  # no HTTP of any kind, so nothing can reach 8080

    def test_swap_test_refused_without_approval(self):
        env = make_env("r9700")
        with self.assertRaises(Exception):
            env.lane.swap_test()
        self.assertEqual(env.world.cmd_log, [])


if __name__ == "__main__":
    unittest.main()
