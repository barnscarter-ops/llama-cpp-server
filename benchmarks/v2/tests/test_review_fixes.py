"""Findings from the independent review of PR #25: fail-closed status, swap put-back, spill reading, UPS, signals."""

import signal
import sys
import unittest
from pathlib import Path

from lane_fixture import make_env
from bench_v2.backend import GpuSample, SystemBackend, parse_typeperf_shared
from bench_v2.errors import WindowError
from bench_v2.fakes import FakeBackend
from bench_v2.safety import RestoreFailed
from bench_v2.telemetry import Telemetry
from bench_v2.window import LaneMutex

SWAP = {"qwen_load": True, "swap_test": True}
NO_CMDS = lambda env: [c for c in env.world.cmd_log if c and c[0] in ("pm2", "systemctl") and c[1] in ("stop", "start")]  # noqa: E731


class SwapPutBack(unittest.TestCase):
    def test_failed_to_qwen_stops_the_loop_and_leaves_nemotron(self):
        env = make_env("r9700", approvals=SWAP)
        env.world.swap_fail = {"swap-qwen-consult.sh"}
        out = env.lane.swap_test()
        self.assertIn("to_qwen failed", out["failed"])
        self.assertEqual(out["to_qwen_s"], [])
        self.assertIn("Nemotron", env.world.resident)
        self.assertFalse(env.lane.markers.restore_pending())

    def test_failed_to_nemotron_is_retried_and_otherwise_not_called_restored(self):
        env = make_env("r9700", approvals=SWAP)
        env.world.swap_fail = {"swap-nemo-clerk.sh"}
        with self.assertRaises(RestoreFailed):
            env.lane.swap_test()
        self.assertIn("Qwen", env.world.resident)
        self.assertTrue(env.lane.markers.restore_pending(), "marker must stay while Qwen is resident")

    def test_abort_mid_swap_still_puts_nemotron_back(self):
        class StopDuringSwap(FakeBackend):
            def run(self, cmd, timeout=120.0, env=None):
                r = super().run(cmd, timeout, env)
                if cmd and cmd[0].endswith("swap-qwen-consult.sh"):
                    (self.stop_dir / "r9700.STOP").write_text("")
                return r
        env = make_env("r9700", approvals=SWAP, backend_cls=StopDuringSwap)
        env.backend.stop_dir = env.root / "runs"
        env.backend.stop_dir.mkdir(exist_ok=True)
        with self.assertRaises(Exception):
            env.lane.swap_test()
        self.assertIn("Nemotron", env.world.resident)
        self.assertFalse(env.lane.markers.restore_pending())

    def test_restore_checks_the_resident_model_not_just_the_service(self):
        env = make_env("r9700")
        env.world.resident = "Qwen3.8-27B-Q6_K.gguf"
        env.lane.markers.mark_restore_required({"was_active": ["llama-server"], "ts": 0, "mode": "watch"})
        env.lane.load_window()
        sc = env.lane.make_safety(use_mutex=False)
        with self.assertRaises(RestoreFailed):
            sc.restore(wait_s=10)

    def test_journal_is_read_since_the_stop_not_last_80_lines(self):
        env = make_env("r9700", only=["nemotron-3.5-lightning-q5km"])
        env.lane.run_stages(["smoke"])
        j = [c for c in env.world.cmd_log if c and c[0] == "journalctl"]
        self.assertTrue(j)
        self.assertTrue(all("--since" in c and "-n" not in c for c in j), j)


class IdentityMatch(unittest.TestCase):
    def test_exact_name_match_ignores_the_harness_command_line(self):
        env = make_env("r9700")
        env.lane.load_window()
        sc = env.lane.make_safety(use_mutex=False)
        self.assertTrue(any(a.startswith("-") and "x" in a for a in env.lane.lane.raw["swap"]["identity_cmd"]))
        self.assertTrue(sc.identity_is("nemotron")[0])
        self.assertFalse(sc.identity_is("qwen")[0])

    def test_a_loose_pgrep_would_see_both_models(self):
        env = make_env("r9700")
        env.lane.load_window()
        env.lane.lane.raw["swap"]["identity_cmd"] = ["pgrep", "-af", "llama-server"]
        ok, why = env.lane.make_safety(use_mutex=False).identity_is("nemotron")
        self.assertFalse(ok)
        self.assertIn("both models", why)


class FailClosed(unittest.TestCase):
    def test_unparseable_status_aborts_before_anything_is_stopped(self):
        env = make_env("workbench")
        env.world.status_garbage = True
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "status_unknown")
        self.assertEqual(env.world.spawned, [])
        self.assertEqual(NO_CMDS(env), [])
        self.assertTrue(all(env.world.services.values()))

    def test_pm2_banner_before_the_json_is_handled(self):
        class Banner(FakeBackend):
            def run(self, cmd, timeout=120.0, env=None):
                r = super().run(cmd, timeout, env)
                if "pm2 jlist" in " ".join(cmd):
                    r.stdout = "[PM2] Spawning PM2 daemon [x]\n" + r.stdout
                return r
        env = make_env("workbench", backend_cls=Banner)
        rep = env.lane.run_stages(["smoke"])
        self.assertNotIn("aborted", rep)
        self.assertEqual(env.world.services, {"llama-guardian": True, "local-llm": True})

    def test_service_missing_from_pm2_is_unknown_not_stopped(self):
        env = make_env("workbench")
        del env.world.services["local-llm"]
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "status_unknown")

    def test_nothing_running_aborts_unless_the_window_says_so(self):
        env = make_env("workbench")
        env.world.services = {"llama-guardian": False, "local-llm": False}
        self.assertEqual(env.lane.run_stages(["smoke"])["aborted"]["code"], "no_production_service")
        env2 = make_env("workbench", approvals={"allow_no_production_service": True})
        env2.world.services = {"llama-guardian": False, "local-llm": False}
        self.assertNotIn("aborted", env2.lane.run_stages(["smoke"]))

    def test_systemd_unknown_rc_is_unknown(self):
        env = make_env("r9700")
        orig = env.backend.run
        env.backend.run = lambda cmd, timeout=120.0, env_=None: (type(orig(["true"]))(4, "", "boom") if cmd[:2] == ["systemctl", "is-active"] else orig(cmd, timeout))
        self.assertEqual(env.lane.run_stages(["smoke"])["aborted"]["code"], "status_unknown")

    def test_workbench_has_a_lock_too(self):
        env = make_env("workbench")
        (env.root / "runs").mkdir(exist_ok=True)
        held = LaneMutex(env.root / "runs" / "workbench.lock")
        held.acquire()
        try:
            with self.assertRaises(WindowError):
                env.lane.run_stages(["smoke"])
        finally:
            held.release()
        self.assertEqual(env.world.spawned, [])


class SharedMemory(unittest.TestCase):
    def test_typeperf_parse(self):
        out = ('"(PDH-CSV 4.0)","\\\\H\\GPU Adapter Memory(a)\\Shared Usage","\\\\H\\GPU Adapter Memory(b)\\Shared Usage"\n'
               '"10/13/2026 12:00:00.000","1048576.000000","3145728.000000"\nExiting, user pressed CTRL+C\n')
        self.assertAlmostEqual(parse_typeperf_shared(out), 3.0)
        self.assertIsNone(parse_typeperf_shared("Error: no counters"))
        self.assertIsNone(parse_typeperf_shared(""))

    def test_unreadable_shared_memory_aborts_the_workbench_lane(self):
        env = make_env("workbench", shared_mib=None)
        rep = env.lane.run_stages(["smoke"])
        self.assertEqual(rep["aborted"]["code"], "no_spill_reading")
        self.assertEqual(env.world.spawned, [])

    def test_preflight_reports_unreadable_shared_memory(self):
        env = make_env("workbench", shared_mib=None)
        pre = env.lane.preflight()
        self.assertTrue(any("shared GPU memory" in p for p in pre["problems"]))

    def test_baseline_is_subtracted_and_growth_is_a_spill(self):
        class B:
            def __init__(self):
                self.shared = 2000.0
            def gpu_sample(self):
                return GpuSample(1000, 16384, 50, 100, 2000, self.shared)
            def power_alarm(self):
                return None
        b = B()
        t = Telemetry(b, require_shared=True)
        t.capture_baseline()
        self.assertEqual(t.observe().shared_mib, 0.0)   # desktop's own 2000 MiB is not a spill
        b.shared = 2900.0
        self.assertEqual(t.observe().shared_mib, 900.0)

    def test_lost_reading_mid_run_aborts(self):
        class B:
            shared = 0.0
            def gpu_sample(self):
                return GpuSample(1000, 16384, 50, 100, 2000, self.shared)
            def power_alarm(self):
                return None
        b = B()
        t = Telemetry(b, require_shared=True)
        t.capture_baseline()
        b.shared = None
        with self.assertRaises(Exception) as cm:
            t.observe()
        self.assertEqual(cm.exception.code, "no_spill_reading")

    def test_workbench_lane_config_reads_shared_memory(self):
        env = make_env("workbench")
        self.assertTrue(env.lane.lane.raw["require_shared_reading"])
        self.assertTrue(env.lane.lane.raw["shared_mem_cmd"])


class UpsMonitor(unittest.TestCase):
    def test_dead_monitor_becomes_an_alarm(self):
        b = SystemBackend(power_cmd=[sys.executable, "-c", "import sys; sys.exit(1)"], power_alarm_regex=r"\bOB\b")
        self.assertIsNone(b.power_alarm())
        self.assertIsNone(b.power_alarm())
        self.assertIn("unreadable", b.power_alarm())

    def test_a_good_reading_resets_the_failure_count(self):
        flag = Path(__file__).parent / "_ups_flag"
        flag.unlink(missing_ok=True)
        b = SystemBackend(power_cmd=[sys.executable, "-c", "print('OL')"], power_alarm_regex=r"\bOB\b")
        b._power_failures = 2
        self.assertIsNone(b.power_alarm())
        self.assertEqual(b._power_failures, 0)

    def test_preflight_fails_when_upsc_returns_nothing(self):
        env = make_env("r9700")  # the fake returns empty output for upsc
        pre = env.lane.preflight()
        self.assertTrue(any("UPS monitor" in p for p in pre["problems"]), pre)

    def test_header_records_a_working_monitor_only(self):
        class Ups(FakeBackend):
            def run(self, cmd, timeout=120.0, env=None):
                if cmd and cmd[0] == "upsc":
                    from bench_v2.backend import CmdResult
                    return CmdResult(0, "OL\n")
                return super().run(cmd, timeout, env)
        env = make_env("r9700", only=["nemotron-3.5-lightning-q5km"], backend_cls=Ups)
        self.assertTrue(env.lane.ups_probe()[0])
        env.lane.run_stages(["smoke"])
        hdr = next(r for r in env.lane.ledger.records() if r.get("phase") == "header")
        self.assertTrue(hdr["power_monitor"])
        env2 = make_env("r9700", only=["nemotron-3.5-lightning-q5km"])
        env2.lane.run_stages(["smoke"])
        hdr2 = next(r for r in env2.lane.ledger.records() if r.get("phase") == "header")
        self.assertFalse(hdr2["power_monitor"])


class Signals(unittest.TestCase):
    def test_second_signal_interrupts_and_production_is_restored(self):
        env = make_env("workbench")
        env.lane.load_window()
        sc = env.lane.make_safety()
        with self.assertRaises(KeyboardInterrupt):
            with sc.session():
                signal.raise_signal(signal.SIGINT)        # first: only sets the flag
                self.assertEqual(sc._signal_abort, "SIGINT")
                signal.raise_signal(signal.SIGINT)        # second: raises out of whatever is blocked
        self.assertEqual(env.world.services, {"llama-guardian": True, "local-llm": True})
        self.assertFalse(env.lane.markers.restore_pending())

    def test_first_signal_aborts_at_the_next_check(self):
        env = make_env("workbench")
        env.lane.load_window()
        sc = env.lane.make_safety()
        with sc.session():
            signal.raise_signal(signal.SIGTERM)
            with self.assertRaises(Exception) as cm:
                sc.check()
            self.assertEqual(cm.exception.code, "signal")


class WindowTemplate(unittest.TestCase):
    def test_template_lists_swap_test_and_defaults_everything_off(self):
        from bench_v2.window import WINDOW_TEMPLATE
        a = WINDOW_TEMPLATE["approvals"]
        self.assertIn("swap_test", a)
        self.assertFalse(a["swap_test"])
        self.assertFalse(a["allow_no_production_service"])


if __name__ == "__main__":
    unittest.main()


class CodexFindings(unittest.TestCase):
    def test_newer_sweep_invalidates_the_old_tuned_config_and_is_reconfirmed(self):
        env = make_env("workbench")
        env.lane.run_stages(["smoke", "tune", "confirm"])
        cid = "qwen36-35b-a3b-iq3"
        self.assertIsNotNone(env.lane.tuned_cfg(cid))
        resweep(env, cid)
        self.assertIsNone(env.lane.tuned_cfg(cid), "a newer sweep must not be shadowed by the old tuned record")
        env2 = make_env("workbench")
        env2.lane.run_stages(["smoke", "tune", "confirm"])
        n_before = sum(1 for r in env2.lane.ledger.records() if r.get("kind") == "tuned")
        resweep(env2, cid)
        env2.lane.run_stages(["confirm"])
        self.assertGreater(sum(1 for r in env2.lane.ledger.records() if r.get("kind") == "tuned"), n_before)
        self.assertIsNotNone(env2.lane.tuned_cfg(cid))

    def test_cloud_arm_reaches_https_but_never_the_local_ports(self):
        import run_lane, argparse, os
        env = make_env("workbench", oracle=False, approvals={"cloud_spend_cap_usd": 5})
        ns = argparse.Namespace(lane="workbench", window=str(env.window_path), ledger_dir=str(env.root / "l"), runs_dir=str(env.root / "r"),
                                lanes=None, candidates=None, frozen_dir=str(lane_fixture_dir()), maverick=None, humaneval=None, only="",
                                endpoint="http://127.0.0.1:8080/v1/chat/completions", model="m", key_env="K", price_in=1.0, price_out=1.0)
        run_lane._real_backend = lambda a: env.backend
        os.environ["K"] = "x"
        with self.assertRaises(Exception) as cm:
            run_lane.cmd_cloud(ns)
        self.assertIn("public https", str(cm.exception))
        ns.endpoint = "https://api.example.invalid/v1/chat/completions"
        res = run_lane.cmd_cloud(ns)
        self.assertEqual(res, 0)
        urls = [u for u, _ in env.world.requests]
        self.assertTrue(urls and all(u.startswith("https://api.example.invalid") for u in urls))
        del os.environ["K"]

    def test_failed_swap_exits_nonzero(self):
        import run_lane, argparse
        env = make_env("r9700", approvals=SWAP)
        env.world.swap_fail = {"swap-qwen-consult.sh"}
        ns = argparse.Namespace(lane="r9700", window=str(env.window_path), ledger_dir=str(env.root / "l"), runs_dir=str(env.root / "r"),
                                lanes=None, candidates=None, frozen_dir=None, maverick=None, humaneval=None, only="")
        run_lane._real_backend = lambda a: env.backend
        self.assertEqual(run_lane.cmd_swap(ns), 3)

    def test_frozen_and_ledger_jsonl_are_not_swallowed_by_the_repo_gitignore(self):
        text = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
        self.assertIn("!frozen/*.jsonl", text)
        self.assertIn("!ledger/*.jsonl", text)

    def test_model_code_cannot_outlive_its_timeout_or_eat_memory(self):
        from bench_v2.suites import scoring
        self.assertEqual(scoring.score_python("```python\nwhile True: pass\n```", {"tests": "assert True"}), (False, "timeout"))
        ok, why = scoring.score_python("```python\nx = bytearray(6 * 1024**3)\n```", {"tests": "assert True"})
        self.assertFalse(ok)
        ok, why = scoring.score_python("```python\nimport os\nos.environ['X']\n```", {"tests": "assert True"})
        self.assertFalse(ok)  # the child environment is scrubbed


def lane_fixture_dir():
    import lane_fixture
    return lane_fixture.frozen_dir()


def resweep(env, cid):
    """Append a levers_done identical to the last one, as a rerun after drift would."""
    last = env.lane._last_event("levers_done", cid)
    skip = {"schema", "seq", "ts", "phase", "prev", "hash", "kind"}
    env.lane.ledger.event("levers_done", **{k: v for k, v in last.items() if k not in skip})
