"""Backend, Jev track, registry, cloud arm, adapter and CLI guards."""

import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import lane_fixture
from lane_fixture import HERE, make_env
from bench_v2 import jevtrack, registry
from bench_v2.backend import GuardedBackend, SystemBackend, check_target
from bench_v2.config import load_candidates, load_lanes
from bench_v2.cloud import run_anchor
from bench_v2.errors import AbortRun, ForbiddenTarget
from bench_v2.fakes import FakeBackend, FakeWorld
from bench_v2.hermes_adapter import HermesBenchAdapter
from bench_v2.ledger import Ledger, read_all

QUEUE = HERE.parent.parent / "qwen-queue"


class TargetGuard(unittest.TestCase):
    def test_guardian_ports_and_prod_host_refused(self):
        for url in ("http://127.0.0.1:8080/v1/chat/completions", "http://localhost:8081/health", "http://192.168.1.240:8080/x"):
            with self.assertRaises(ForbiddenTarget):
                check_target(url, {8099})

    def test_other_ports_refused_when_not_allowed(self):
        with self.assertRaises(ForbiddenTarget):
            check_target("http://127.0.0.1:9999/x", {8099})

    def test_own_port_allowed(self):
        check_target("http://127.0.0.1:8099/v1/chat/completions", {8099})

    def test_guarded_backend_records_violation_and_raises(self):
        g = GuardedBackend(FakeBackend(), {8099})
        with self.assertRaises(ForbiddenTarget):
            g.chat("http://127.0.0.1:8080/v1/chat/completions", {})
        self.assertEqual(len(g.violations), 1)


class SseParser(unittest.TestCase):
    """The real HTTP/SSE client against a loopback stdlib server on an ephemeral port."""

    def test_stream_is_assembled_with_usage_and_tool_calls(self):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = [
                    {"choices": [{"delta": {"reasoning_content": "hm"}}]},
                    {"choices": [{"delta": {"content": "Hel"}}]},
                    {"choices": [{"delta": {"content": "lo"}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_", "arguments": "{\"pa"}}]}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": "th\": \"a\"}"}}]}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                     "timings": {"prompt_per_second": 100.0, "predicted_per_second": 50.0}},
                ]
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = SystemBackend().chat(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions", {"messages": []}, timeout=10)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "Hello")
        self.assertEqual(r.reasoning_text, "hm")
        self.assertEqual(r.tool_calls[0]["name"], "read_file")
        self.assertEqual(json.loads(r.tool_calls[0]["arguments"]), {"path": "a"})
        self.assertEqual((r.prompt_tokens, r.completion_tokens, r.finish_reason), (7, 3, "stop"))
        self.assertEqual(r.predicted_tps, 50.0)

    def test_connection_refused_is_a_failed_result_not_an_exception(self):
        r = SystemBackend().chat("http://127.0.0.1:1/v1/chat/completions", {}, timeout=2)
        self.assertFalse(r.ok)

    def test_power_alarm_command(self):
        b = SystemBackend(power_cmd=[sys.executable, "-c", "print('ups.status: OB LB')"], power_alarm_regex=r"\bOB\b|\bLB\b")
        self.assertIn("OB", b.power_alarm())
        b = SystemBackend(power_cmd=[sys.executable, "-c", "print('ups.status: OL')"], power_alarm_regex=r"\bOB\b|\bLB\b")
        self.assertIsNone(b.power_alarm())
        self.assertIsNone(SystemBackend().power_alarm())

    def test_r9700_default_aborts_only_on_battery(self):
        pa = load_lanes()["r9700"].raw["power_alarm"]
        self.assertEqual(pa["command"], ["upsc", "cp1500@localhost", "ups.status"])
        for status, alarm in (("OL", False), ("OL LB", False), ("OL CHRG", False), ("OB DISCHRG", True), ("OB LB", True)):
            b = SystemBackend(power_cmd=[sys.executable, "-c", f"print({status!r})"], power_alarm_regex=pa["alarm_regex"])
            self.assertEqual(b.power_alarm() is not None, alarm, status)


class JevTrack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = jevtrack.models_from_registry(QUEUE / "models.json")
        cls.cases = jevtrack.generate_cases(cls.models)
        cls.labeled = jevtrack.label_cases(cls.cases, cls.models)
        cls.dev, cls.held = jevtrack.split_by_family(cls.labeled)

    def test_sizes_and_family_split(self):
        self.assertEqual(len(self.cases), 200)
        self.assertEqual((len(self.dev), len(self.held)), (120, 80))
        self.assertFalse({c["family"] for c in self.dev} & {c["family"] for c in self.held})

    def test_generation_is_deterministic(self):
        again = jevtrack.generate_cases(self.models)
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.cases, sort_keys=True))

    def test_oracle_and_rules_have_zero_violations(self):
        for arm in ("oracle", "rules"):
            g = jevtrack.grade(jevtrack.run_builtin(arm, self.held, self.models, self.dev), self.held)
            self.assertEqual(g["violations"], 0, arm)
            self.assertTrue(g["qualifies"], arm)

    def test_a_route_outside_the_acceptable_set_is_a_violation(self):
        case = next(c for c in self.held if c["acceptable"] and not c["abstain_expected"])
        outside = next(m.id for m in self.models if m.id not in case["acceptable"])
        g = jevtrack.grade([{"case_id": case["case_id"], "route": outside}], self.held)
        self.assertEqual(g["violations"], 1)
        self.assertFalse(g["qualifies"])

    def test_no_case_prompt_contains_a_secret_shaped_string(self):
        text = json.dumps(self.cases)
        for needle in ("sk-", "Bearer ", "AKIA"):
            self.assertNotIn(needle, text)


class Registry(unittest.TestCase):
    def test_generated_registry_loads_in_guardians_own_parser(self):
        lanes = load_lanes()
        _, cands = load_candidates(None, lanes)
        d = Path(tempfile.mkdtemp())
        led = Ledger(d / "l.jsonl")
        c = next(c for c in cands if c.id == "nemotron-3.5-lightning-q5km")
        led.event("tuned", lane="r9700", model=c.id, final_ok=True, final_tg=120.0, final_pp=3000.0, changed=[], drift=0.0, needs_rerun=False,
                  cfg={"ctx": 131072, "kv": "f16", "fa": True, "b": 2048, "ub": 512, "np": 1, "n_cpu_moe": 0, "spec": "off", "mmproj": False,
                       "reasoning": None, "threads": None})
        led.event("ladder_done", model=c.id, max_safe_ctx=131072, wake_seconds=61, fails_128k=False)
        led.event("role_verdict", model=c.id, role="fast_executor", verdict={"pass": True, "tier": 1, "detail": "ok"}, overall=0.9)
        base = json.loads((QUEUE / "models.json").read_text())
        doc, notes = registry.generate_models_json(list(led.records()), cands, base, QUEUE)
        ent = next(m for m in doc["models"] if m["id"] == c.get("registry")["id"])
        self.assertEqual(ent["quality_tier"], 1)
        self.assertGreaterEqual(ent["context_tokens"], 131072)
        self.assertEqual(ent["wake_seconds"], 61)
        self.assertTrue(notes)

    def test_nothing_passed_leaves_registry_unchanged(self):
        _, cands = load_candidates(None, load_lanes())
        base = json.loads((QUEUE / "models.json").read_text())
        doc, notes = registry.generate_models_json([], cands, base, QUEUE)
        self.assertEqual(doc, base)
        self.assertTrue(all("no candidate passed" in n for n in notes))

    def test_results_markdown_renders_from_a_real_fake_run(self):
        env = make_env("workbench")
        env.lane.run_stages(["smoke", "tune", "confirm", "ladder"])
        md = registry.render_results(read_all(env.root / "ledger"), env.lane.all_cands, None, ["note"])
        self.assertIn("## worker_bee", md)
        self.assertIn("qwen36-35b-a3b-iq3", md)


class CloudArm(unittest.TestCase):
    def setUp(self):
        self.env = make_env("workbench", oracle=False)
        self.items = [{"id": f"x{i}", "category": "reasoning", "messages": [{"role": "user", "content": "q " * 50}],
                       "scorer": {"type": "exact", "answers": ["1"]}, "max_tokens": 100} for i in range(5)]

    def test_key_must_exist(self):
        with self.assertRaises(AbortRun) as cm:
            run_anchor(self.env.backend, self.env.lane.ledger, self.items, "https://example.invalid/v1/chat/completions", "m",
                       "NO_SUCH_KEY", 1, 1, 1.0, "r", environ={})
        self.assertEqual(cm.exception.code, "no_key")

    def test_zero_cap_refused(self):
        with self.assertRaises(AbortRun):
            run_anchor(self.env.backend, self.env.lane.ledger, self.items, "x", "m", "K", 1, 1, 0.0, "r", environ={"K": "v"})

    def test_spend_cap_stops_the_run_and_key_is_never_written(self):
        w = self.env.world
        w.models["cloud"] = None
        class Cloud(FakeBackend):
            def chat(self, url, body, timeout=600.0, headers=None):
                from bench_v2.backend import ChatResult
                self.w.requests.append((url, "chat"))
                return ChatResult(True, text="ANSWER: 1", prompt_tokens=1000, completion_tokens=1000, total_s=1.0, ttft_s=0.1)
        b = Cloud(w)
        with self.assertRaises(AbortRun) as cm:
            run_anchor(b, self.env.lane.ledger, self.items, "https://example.invalid/v1/chat/completions", "m", "K", 1.0, 1.0, 3.0, "r",
                       environ={"K": "super-secret-value"})
        self.assertEqual(cm.exception.code, "spend_cap")
        self.assertNotIn("super-secret-value", (self.env.root / "ledger" / "workbench.jsonl").read_text() if (self.env.root / "ledger" / "workbench.jsonl").exists() else "")


class Adapter(unittest.TestCase):
    def test_unconfigured_by_default(self):
        a = HermesBenchAdapter(FakeBackend(), None)
        self.assertFalse(a.configured)
        self.assertEqual(a.run("m", "http://127.0.0.1:8099", "r")["tasks"], {})

    def test_refuses_guardian_ports(self):
        a = HermesBenchAdapter(FakeBackend(), {"cmd": ["bench", "{base_url}"]})
        with self.assertRaises(ValueError):
            a.run("m", "http://127.0.0.1:8080", "r")


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        p = subprocess.run([sys.executable, str(HERE / "run_lane.py"), *args], capture_output=True, text=True, cwd=str(HERE))
        return p.returncode, p.stdout, p.stderr

    def test_run_without_window_is_refused_and_touches_nothing(self):
        rc, out, err = self.run_cli("--runs-dir", tempfile.mkdtemp(), "--ledger-dir", tempfile.mkdtemp(), "run", "--lane", "workbench")
        self.assertEqual(rc, 2)
        self.assertIn("--window is required", err)

    def test_plan_prints_fit_table(self):
        rc, out, _ = self.run_cli("plan", "--lane", "r9700")
        self.assertEqual(rc, 0)
        self.assertIn("qwen3.8-27b-q6k", out)

    def test_window_template_is_valid_json_with_no_approvals_by_default(self):
        rc, out, _ = self.run_cli("window-template", "--lane", "r9700")
        doc = json.loads(out)
        self.assertEqual(doc["lane"], "r9700")
        self.assertFalse(doc["approvals"]["qwen_load"])
        self.assertEqual(doc["approvals"]["cloud_spend_cap_usd"], 0)

    def test_dry_run_uses_only_the_fake_machine(self):
        rc, out, err = self.run_cli("--frozen-dir", str(lane_fixture.frozen_dir()), "dry-run", "--lane", "workbench", "--stages", "smoke")
        self.assertEqual(rc, 0, err)
        self.assertIn('"llama-guardian": true', out)


if __name__ == "__main__":
    unittest.main()
