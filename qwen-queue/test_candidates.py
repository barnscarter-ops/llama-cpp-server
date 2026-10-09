"""Step 4: egress/clearance candidate filter (security-critical invariants)."""

from __future__ import annotations

import itertools
import random
import unittest

import model_registry as mr
from candidates import (
    NARROWEST, NO_CANDIDATES, OK, TaskLimits, build_candidates, estimate_cost_usd, normalize_ceiling,
    normalize_clearance,
)

REG = mr.load_registry()
READY = mr.Readiness(mr.READY)


def state(**overrides: mr.Readiness) -> list[tuple[mr.ModelSpec, mr.Readiness]]:
    """All four seed models, ready unless overridden (by model id)."""
    return [(spec, overrides.get(spec.id.replace("-", "_"), READY)) for spec in REG]


def ids(result) -> list[str]:
    return result.ids()


class TableTests(unittest.TestCase):
    def test_pc_job_sees_only_workbench(self) -> None:
        result = build_candidates(state(), "pc", 10.0)
        self.assertEqual(["workbench-local"], ids(result))
        reasons = dict(result.excluded)
        self.assertEqual("egress_exceeds_clearance", reasons["nemotron-r9700"])
        self.assertEqual("egress_exceeds_clearance", reasons["qwen27b-r9700"])
        self.assertEqual("egress_exceeds_clearance", reasons["deepseek-flash"])

    def test_lan_job_sees_workbench_and_r9700_never_cloud(self) -> None:
        result = build_candidates(state(), "lan", 10.0)
        self.assertEqual(["workbench-local", "nemotron-r9700", "qwen27b-r9700"], ids(result))

    def test_internet_job_sees_everything_affordable(self) -> None:
        result = build_candidates(state(), "internet", 10.0)
        self.assertEqual(4, len(result.candidates))

    def test_no_candidates_is_explicit_never_a_fallthrough(self) -> None:
        down = mr.Readiness(mr.UNAVAILABLE, reason="ram_low")
        result = build_candidates(state(workbench_local=down), "pc", 10.0)
        self.assertEqual(NO_CANDIDATES, result.status)
        self.assertEqual([], ids(result))
        self.assertEqual("unavailable:ram_low", dict(result.excluded)["workbench-local"])

    def test_unavailable_models_are_dropped(self) -> None:
        gone = mr.Readiness(mr.UNAVAILABLE, reason="aiwa_unreachable")
        result = build_candidates(state(nemotron_r9700=gone), "lan", 10.0)
        self.assertNotIn("nemotron-r9700", ids(result))

    def test_needs_approval_model_is_included_and_flagged(self) -> None:
        needs = mr.Readiness(mr.NEEDS_APPROVAL, seconds=60)
        result = build_candidates(state(qwen27b_r9700=needs), "lan", 0)
        qwen = next(c for c in result.candidates if c.model_id == "qwen27b-r9700")
        self.assertTrue(qwen.needs_approval)
        self.assertEqual(60.0, qwen.wake_seconds)
        self.assertEqual(mr.NEEDS_APPROVAL, qwen.readiness.state)

    def test_wake_seconds_carried_for_wakeable(self) -> None:
        wake = mr.Readiness(mr.WAKEABLE, seconds=45)
        result = build_candidates(state(workbench_local=wake), "pc", 0)
        self.assertEqual(45.0, result.candidates[0].wake_seconds)

    def test_context_too_small_excluded(self) -> None:
        result = build_candidates(state(), "internet", 10.0, TaskLimits(input_tokens=100_000))
        self.assertEqual({"workbench-local", "nemotron-r9700", "qwen27b-r9700"}, set(ids(result)))
        self.assertEqual("context_too_small", dict(result.excluded)["deepseek-flash"])

    def test_cost_ceiling_excludes_paid_models(self) -> None:
        limits = TaskLimits(input_tokens=10_000, max_output_tokens=4_000)
        spec = REG.get("deepseek-flash")
        cost = estimate_cost_usd(spec, limits)
        self.assertAlmostEqual(0.0078, cost, places=6)
        self.assertNotIn("deepseek-flash", ids(build_candidates(state(), "internet", cost - 0.0001, limits)))
        self.assertIn("deepseek-flash", ids(build_candidates(state(), "internet", cost, limits)))

    def test_missing_ceiling_means_zero_so_paid_models_are_never_offered(self) -> None:
        for ceiling in (None, "5", True, -3, float("nan")):
            with self.subTest(ceiling=ceiling):
                result = build_candidates(state(), "internet", ceiling)
                self.assertNotIn("deepseek-flash", ids(result))
        self.assertEqual(0.0, normalize_ceiling(None))

    def test_missing_or_garbled_clearance_defaults_to_narrowest(self) -> None:
        for clearance in (None, "", "WAN", 3, ["internet"], "internet; lan"):
            with self.subTest(clearance=clearance):
                result = build_candidates(state(), clearance, 10.0)
                self.assertEqual(NARROWEST, result.clearance)
                self.assertEqual(["workbench-local"], ids(result))
                self.assertTrue(result.clearance_note)

    def test_chiefs_none_means_pc(self) -> None:
        result = build_candidates(state(), "none", 10.0)
        self.assertEqual(("pc", None), (result.clearance, result.clearance_note))
        self.assertEqual(["workbench-local"], ids(result))

    def test_clearance_is_case_and_space_tolerant(self) -> None:
        self.assertEqual(("lan", None), normalize_clearance("  LAN "))

    def test_result_serializes_without_secrets(self) -> None:
        text = str(build_candidates(state(), "internet", 10.0).as_api())
        self.assertNotIn("key_env", text)

    def test_ok_status(self) -> None:
        self.assertEqual(OK, build_candidates(state(), "pc", 0).status)


class InvariantFuzzTests(unittest.TestCase):
    """For every clearance x egress x readiness x cost, nothing wider than the clearance is ever offered."""

    def synthetic_spec(self, idx: int, egress: str, cost: float, context: int) -> mr.ModelSpec:
        local = egress != "internet"
        return mr.ModelSpec(
            id=f"m{idx}", locality="local" if local else "cloud",
            host="workbench" if egress == "pc" else ("r9700" if egress == "lan" else "provider"),
            egress=egress, cost_per_1k_in=0.0 if local else cost, cost_per_1k_out=0.0 if local else cost,
            context_tokens=context, quality_tier=1 + idx % 3, max_concurrent=1 if local else 2,
            queue_key=f"m{idx}", readiness_source="cloud_state", needs_approval=idx % 5 == 0, wake_seconds=1.0,
        )

    def test_exhaustive_clearance_by_egress(self) -> None:
        states = [
            mr.Readiness(mr.READY), mr.Readiness(mr.WAKEABLE, seconds=3),
            mr.Readiness(mr.NEEDS_APPROVAL, seconds=3), mr.Readiness(mr.UNAVAILABLE, reason="x"),
        ]
        clearances = list(mr.EGRESS_ORDER) + [None, "none", "", "all", 0]
        for clearance, egress, readiness in itertools.product(clearances, mr.EGRESS_ORDER, states):
            spec = self.synthetic_spec(1, egress, 0.001, 1000)
            result = build_candidates([(spec, readiness)], clearance, 1e9)
            effective = result.clearance
            offered = [c for c in result.candidates]
            if mr.egress_rank(egress) > mr.egress_rank(effective):
                self.assertEqual([], offered, (clearance, egress, readiness))
            for c in offered:
                self.assertLessEqual(mr.egress_rank(c.egress), mr.egress_rank(effective))

    def test_random_registries_never_leak_past_clearance(self) -> None:
        rng = random.Random(1234)
        for _ in range(500):
            specs = [
                self.synthetic_spec(i, rng.choice(mr.EGRESS_ORDER), rng.random(), rng.choice([100, 5000, 200_000]))
                for i in range(rng.randint(1, 8))
            ]
            pairs = [
                (s, rng.choice([
                    mr.Readiness(mr.READY), mr.Readiness(mr.WAKEABLE, seconds=1),
                    mr.Readiness(mr.NEEDS_APPROVAL, seconds=1), mr.Readiness(mr.UNAVAILABLE, reason="r"),
                ]))
                for s in specs
            ]
            clearance = rng.choice(list(mr.EGRESS_ORDER) + [None, "junk"])
            ceiling = rng.choice([None, 0, 0.01, 1.0, 100.0])
            limits = TaskLimits(rng.randint(0, 50_000), rng.randint(1, 8_000))
            result = build_candidates(pairs, clearance, ceiling, limits)
            by_id = {s.id: s for s in specs}
            for c in result.candidates:
                spec = by_id[c.model_id]
                self.assertLessEqual(mr.egress_rank(spec.egress), mr.egress_rank(result.clearance))
                self.assertLessEqual(c.cost_estimate_usd, result.cost_ceiling_usd)
                self.assertLessEqual(limits.context_needed, spec.context_tokens)
            offered_or_excluded = set(result.ids()) | {m for m, _ in result.excluded}
            self.assertEqual(set(by_id), offered_or_excluded, "every model is accounted for")


if __name__ == "__main__":
    unittest.main()
