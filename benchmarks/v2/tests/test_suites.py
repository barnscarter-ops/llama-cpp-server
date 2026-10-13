import json
import tempfile
import unittest
from pathlib import Path

import lane_fixture
from hard_code_references import SOLUTIONS, solution_for
from bench_v2 import toolschemas
from bench_v2.errors import FrozenSuiteMismatch
from bench_v2.suites import builder, generators, scoring

SNAP = lane_fixture.SNAP
RUNNER = lane_fixture.enable_code_runner()


class GeneratorTests(unittest.TestCase):
    def test_generators_are_deterministic(self):
        for fn in (generators.gen_ifeval, generators.gen_clerk, generators.gen_reasoning, generators.gen_logs,
                   generators.gen_code_easy, generators.gen_code_fix, generators.gen_code_hard, generators.gen_longctx):
            a, b = fn(12), fn(12)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), fn.__name__)

    def test_item_ids_are_unique(self):
        pool = builder.pools(SNAP)
        for cat, items in pool.items():
            ids = [i["id"] for i in items]
            self.assertEqual(len(ids), len(set(ids)), cat)

    def test_code_easy_references_pass_their_tests(self):
        for it in generators.gen_code_easy(30):
            ok, why = scoring.score_python("```python\n" + it["reference"] + "\n```", it["scorer"])
            self.assertTrue(ok, f"{it['id']}: {why}")

    def test_code_fix_buggy_fails_and_reference_passes(self):
        for it in generators.gen_code_fix(30):
            ok_ref, why = scoring.score_python("```python\n" + it["reference"] + "\n```", it["scorer"])
            self.assertTrue(ok_ref, f"{it['id']}: {why}")
            ok_bug, _ = scoring.score_python("```python\n" + it["buggy"] + "\n```", it["scorer"])
            self.assertFalse(ok_bug, f"{it['id']}: the buggy code passed")

    def test_every_hard_template_is_satisfiable(self):
        items = generators.gen_code_hard(28)
        self.assertEqual({i["template"] for i in items}, set(SOLUTIONS))
        for it in items:
            ok, why = scoring.score_python(solution_for(it), it["scorer"])
            self.assertTrue(ok, f"{it['id']} {it['template']}: {why}")

    def test_hard_tests_reject_a_stub(self):
        for it in generators.gen_code_hard(7):
            ok, _ = scoring.score_python("```python\npass\n```", it["scorer"])
            self.assertFalse(ok, it["template"])

    def test_longctx_materializes_to_a_scoreable_item(self):
        it = generators.gen_longctx(2)[0]
        msgs, scorer = generators.materialize_longctx(it["longctx"])
        text = "\n".join(scorer["needles"])
        ok, _ = scoring.score_needle(text, scorer)
        self.assertTrue(ok)
        self.assertIn(scorer["needles"][0], msgs[0]["content"])


class ScorerTests(unittest.TestCase):
    def test_exact_numeric(self):
        self.assertTrue(scoring.score_exact("work...\nANSWER: 1,204", {"answers": ["1204"], "numeric": True})[0])
        self.assertFalse(scoring.score_exact("ANSWER: 12", {"answers": ["1204"], "numeric": True})[0])

    def test_think_block_is_ignored(self):
        self.assertTrue(scoring.score_exact("<think>ANSWER: 1</think>\nANSWER: 2", {"answers": ["2"]})[0])

    def test_python_timeout_scores_false(self):
        ok, why = scoring.score_python("```python\nwhile True:\n    pass\n```", {"type": "python", "tests": "assert True"})
        self.assertFalse(ok)
        self.assertEqual(why, "timeout")

    def test_ifeval_constraints(self):
        self.assertTrue(scoring.score_ifeval("hello world", {"constraints": [{"type": "no_commas"}, {"type": "max_words", "n": 5}]})[0])
        self.assertFalse(scoring.score_ifeval("hello, world", {"constraints": [{"type": "no_commas"}]})[0])


class ToolScorerTests(unittest.TestCase):
    def test_oracle_answers_every_generated_item(self):
        items = toolschemas.gen_tool_items(60, SNAP)
        from bench_v2.fakes import oracle_hooks
        resp, tool = oracle_hooks(items)
        for it in items:
            body = {"messages": it["messages"]}
            calls = tool(body, {})
            ok, why, extra = toolschemas.score_tool(calls, resp(body, {}), it["scorer"], it["tools"])
            self.assertTrue(ok, f"{it['id']} {it['kind']}: {why}")

    def test_wrong_tool_and_invalid_arguments_fail(self):
        it = next(i for i in toolschemas.gen_tool_items(20, SNAP) if i["kind"] == "single")
        want = it["scorer"]["calls"][0]
        wrong = [{"name": "nonexistent", "arguments": "{}"}]
        self.assertFalse(toolschemas.score_tool(wrong, "", it["scorer"], it["tools"])[0])
        bad_json = [{"name": want["name"], "arguments": "{not json"}]
        ok, _, extra = toolschemas.score_tool(bad_json, "", it["scorer"], it["tools"])
        self.assertFalse(ok)
        self.assertFalse(extra["valid"])

    def test_schemas_come_from_real_files(self):
        self.assertGreaterEqual(len(SNAP["tools"]), 10)
        self.assertIn("read_file", SNAP["tools"])


class FreezeTests(unittest.TestCase):
    def test_freeze_roundtrip_and_tamper(self):
        d = Path(tempfile.mkdtemp())
        builder.freeze(SNAP, None, d)
        items = builder.load_frozen("worker_bee", d, SNAP)
        self.assertGreaterEqual(len(items), 240)
        self.assertEqual(builder.verify_rebuild(SNAP, None, d), [])
        path = d / "worker_bee.jsonl"
        lines = path.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["max_tokens"] = rec.get("max_tokens", 0) + 1
        lines[0] = json.dumps(rec, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(FrozenSuiteMismatch):
            builder.load_frozen("worker_bee", d, SNAP)

    def test_load_without_freeze_refused(self):
        with self.assertRaises(FrozenSuiteMismatch):
            builder.load_frozen("worker_bee", Path(tempfile.mkdtemp()), SNAP)

    def test_tool_schema_change_refused(self):
        d = Path(tempfile.mkdtemp())
        builder.freeze(SNAP, None, d)
        with self.assertRaises(FrozenSuiteMismatch):
            builder.load_frozen("worker_bee", d, dict(SNAP, sha256="0" * 64))

    def test_each_role_has_300_or_a_reported_shortfall(self):
        d = Path(tempfile.mkdtemp())
        m = builder.freeze(SNAP, None, d)
        for role, info in m["roles"].items():
            short = sum(s["wanted"] - s["have"] for s in info["shortfall"].values())
            self.assertEqual(info["n"] + short, 300, role)


if __name__ == "__main__":
    unittest.main()
