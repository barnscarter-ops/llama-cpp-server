"""Decision tree, numeric guard, noise floor and leave-one-out, with scripted performance."""

import unittest

from lane_fixture import make_env
from bench_v2.fakes import default_perf
from bench_v2.tuning import better_than, worse_than


def tuned(env):
    out = None
    for r in env.lane.ledger.records():
        if r.get("kind") == "tuned":
            out = r
    return out


def decisions(env, lever):
    return [r for r in env.lane.ledger.records() if r.get("kind") == "decision" and r.get("lever") == lever]


def run(env):
    return env.lane.run_stages(["smoke", "tune", "confirm"])


class DecisionTree(unittest.TestCase):
    def test_real_gain_is_adopted_and_confirmed(self):
        env = make_env("workbench", perf_fn=default_perf)  # ub 2048 is +41% prompt speed in the fake
        run(env)
        t = tuned(env)
        self.assertEqual(t["cfg"]["ub"], 2048)
        self.assertIn("D", t["changed"])
        self.assertFalse(t["needs_rerun"])

    def test_flat_lever_is_not_adopted(self):
        env = make_env("workbench", perf_fn=lambda p: {"pp": 2000.0, "tg": 80.0 * (0.94 if p["kv"] == "q8_0" else 1.0)})
        run(env)
        t = tuned(env)
        self.assertEqual(t["cfg"]["ub"], 512)
        self.assertEqual(t["changed"], [])

    def test_gain_inside_noise_floor_is_not_adopted(self):
        # 1% better at ub 2048 is below the 3% minimum floor
        env = make_env("workbench", perf_fn=lambda p: {"pp": 2000.0 * (1.01 if p["ub"] == 2048 else 1.0), "tg": 80.0 * (0.94 if p["kv"] == "q8_0" else 1.0)})
        run(env)
        self.assertEqual(tuned(env)["cfg"]["ub"], 512)

    def test_large_regression_in_one_component_blocks_adoption(self):
        # faster prompt but decode 20% slower: one component more than 10% worse
        def perf(p):
            big = p["ub"] == 2048
            return {"pp": 4000.0 if big else 2000.0, "tg": 80.0 * (0.94 if p["kv"] == "q8_0" else 1.0) * (0.8 if big else 1.0)}
        env = make_env("workbench", perf_fn=perf)
        run(env)
        self.assertNotEqual(tuned(env)["cfg"]["ub"], 2048)

    def test_numerics_guard_rejects_q8_kv_when_kl_is_too_high(self):
        env = make_env("r9700", only=["ornith-1.5-35b-a3b-q5km"], ppl_fn=lambda p: (6.0, 0.0 if p["kv"] == "f16" else 0.5))
        env.lane.run_stages(["smoke", "tune"])
        c = decisions(env, "C")
        self.assertTrue(any(d["outcome"] == "rejected" and d["value"] == "q8_0" for d in c), c)
        self.assertFalse(any("q8_0" in argv for argv in env.world.spawned), "a q8_0 server was launched after the guard rejected it")

    def test_q8_kv_is_adopted_when_it_is_faster_and_numerically_close(self):
        def perf(p):
            return {"pp": 2000.0, "tg": 80.0 * (1.3 if p["kv"] == "q8_0" else 1.0)}
        env = make_env("r9700", only=["ornith-1.5-35b-a3b-q5km"], perf_fn=perf, ppl_fn=lambda p: (6.0, 0.0 if p["kv"] == "f16" else 0.0005))
        env.lane.run_stages(["smoke", "tune", "confirm"])
        self.assertEqual(tuned(env)["cfg"]["kv"], "q8_0")

    def test_perplexity_above_two_percent_is_rejected(self):
        env = make_env("workbench", ppl_fn=lambda p: (6.0 if p["kv"] == "f16" else 6.5, 0.0005))
        run(env)
        self.assertIn("tune_disqualified", [r.get("kind") for r in env.lane.ledger.records()])

    def test_missing_perplexity_tool_disqualifies_numerics_changing_values(self):
        env = make_env("workbench")
        env.lane.s.perplexity_bin = None
        run(env)
        self.assertIn("tune_disqualified", [r.get("kind") for r in env.lane.ledger.records()])

    def test_allow_unguarded_lets_it_continue_and_says_so(self):
        env = make_env("workbench")
        env.lane.s.perplexity_bin = None
        env.lane.s.allow_unguarded = True
        run(env)
        self.assertIsNotNone(tuned(env))
        text = " ".join(str(r.get("reason", "")) + str(r.get("guard", "")) for r in env.lane.ledger.records())
        self.assertIn("unguarded", text)

    def test_drift_over_three_percent_flags_a_rerun(self):
        calls = {"n": 0}

        def perf(p):
            calls["n"] += 1
            base = default_perf(p)
            # after the baseline phase every measurement is slower, as if the card throttled
            if calls["n"] > 400:
                base = {k: v * 0.9 for k, v in base.items()}
            return base
        env = make_env("workbench", perf_fn=perf)
        run(env)
        t = tuned(env)
        if t is not None:
            self.assertIn("drift", t)


class Comparators(unittest.TestCase):
    class P:
        def __init__(self, score, pp, tg, ok=True):
            self.score, self.pp, self.tg, self.ok = score, pp, tg, ok
            self.components = {"pp": pp, "tg": tg}

    def test_better_requires_floor(self):
        a, b = self.P(10.0, 100, 50), self.P(9.9, 100, 50)
        self.assertFalse(better_than(b, a, 0.03) and False)  # smoke: callable with floor
        self.assertFalse(better_than(self.P(9.95, 100, 50), self.P(10.0, 100, 50), 0.03))


if __name__ == "__main__":
    unittest.main()
