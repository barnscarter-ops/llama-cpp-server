"""Step 10: when Jev cannot pick, the rules selector picks from the same filtered candidates."""

from __future__ import annotations

import os
import unittest

import model_registry as mr
from jev_selector import FallbackSelector, JevSelector, UnavailableSelector, fallback_mode
from task_dispatch import RulesSelector
from test_cloud_backend import FakeProvider, KEY as CLOUD_KEY
from test_jev_selector import FakeJev, SECRET, candidate, key, ledger, task
from test_task_api import TaskHttpBase, payload

CANDS = [candidate("workbench-local", egress="pc", quality_tier=2), candidate("nemotron-r9700", quality_tier=2)]


class FallbackUnitTests(unittest.IsolatedAsyncioTestCase):
    def selector(self, fake, **kw):
        alerts: list[tuple[str, str]] = []
        primary = JevSelector(kw.pop("led", None) or ledger(), key, transport=fake, timeout_s=0.05, **kw)
        return FallbackSelector(primary, RulesSelector(), lambda t, r: alerts.append((t.task_id, r))), alerts

    async def test_every_jev_failure_yields_a_rules_pick_inside_the_candidate_set(self) -> None:
        cases = {
            "jev_abstain": FakeJev("abstain"),
            "jev_timeout": FakeJev("workbench-local", delay=1.0),
            "jev_error": FakeJev("workbench-local", status=503),
            "jev_invalid_response": FakeJev(raw={"model": "x"}),
        }
        for reason, fake in cases.items():
            with self.subTest(reason):
                selector, alerts = self.selector(fake)
                answer = await selector.choose(task(), CANDS)
                self.assertIn(answer.model_id, {c.model_id for c in CANDS})
                self.assertEqual((answer.selector, answer.fallback_used, answer.note), ("rules", True, reason))
                self.assertEqual(alerts, [("t1", reason)])

    async def test_cap_reached_and_missing_key_also_fall_back(self) -> None:
        full = ledger(cap=4_000)
        full.reserve()
        selector, alerts = self.selector(FakeJev("workbench-local"), led=full)
        answer = await selector.choose(task(), CANDS)
        self.assertEqual((answer.fallback_used, answer.note), (True, "jev_cap_reached"))

        gone = FallbackSelector(UnavailableSelector("jev_reference_missing"), RulesSelector())
        answer = await gone.choose(task(), CANDS)
        self.assertEqual((answer.fallback_used, answer.note), (True, "jev_reference_missing"))

    async def test_a_jev_pick_is_not_a_fallback(self) -> None:
        selector, alerts = self.selector(FakeJev("nemotron-r9700"))
        answer = await selector.choose(task(), CANDS)
        self.assertEqual((answer.model_id, answer.fallback_used, answer.selector), ("nemotron-r9700", False, None))
        self.assertEqual(alerts, [])

    async def test_the_fallback_cannot_pick_outside_the_filtered_candidates(self) -> None:
        only_lan = [candidate("nemotron-r9700")]
        selector, _ = self.selector(FakeJev(status=500))
        self.assertEqual((await selector.choose(task(), only_lan)).model_id, "nemotron-r9700")

    async def test_rules_with_no_qualifying_candidate_is_still_a_failure(self) -> None:
        low = [candidate("workbench-local", egress="pc", quality_tier=1)]
        high_floor = task()
        high_floor = type(high_floor)(**{**high_floor.__dict__, "quality_floor": 3})
        selector, _ = self.selector(FakeJev(status=500))
        answer = await selector.choose(high_floor, low)
        self.assertEqual((answer.model_id, answer.reason, answer.fallback_used), (None, "no_candidate_meets_quality_floor", True))

    async def test_a_failing_alert_does_not_block_dispatch(self) -> None:
        def boom(task, reason):
            raise RuntimeError("alert sink down")

        primary = JevSelector(ledger(), key, transport=FakeJev(status=500))
        answer = await FallbackSelector(primary, RulesSelector(), boom).choose(task(), CANDS)
        self.assertIsNotNone(answer.model_id)

    def test_only_the_exact_value_turns_it_on(self) -> None:
        for raw, want in (("rules", "rules"), (" RULES ", "rules"), ("", "off"), ("ruless", "off"), ("cloud", "off"), ("true", "off")):
            with self.subTest(raw):
                os.environ["GUARDIAN_JEV_FALLBACK"] = raw
                self.addCleanup(os.environ.pop, "GUARDIAN_JEV_FALLBACK", None)
                self.assertEqual(fallback_mode(), want)


class FallbackNotClearedUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncleared_task_is_routed_by_rules_with_no_alert_and_no_jev_call(self) -> None:
        alerts = []
        fake = FakeJev("nemotron-r9700")
        primary = JevSelector(ledger(), key, transport=fake)
        selector = FallbackSelector(primary, RulesSelector(), lambda t, r: alerts.append(r))
        answer = await selector.choose(task(cleared=False), CANDS)
        self.assertIsNotNone(answer.model_id)
        self.assertEqual((answer.selector, answer.fallback_used, answer.note), ("rules", True, "jev_summary_not_cleared"))
        self.assertEqual((alerts, fake.calls), ([], []))


class FallbackLedgerUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_unopenable_ledger_falls_back_with_an_alert_and_is_not_cached(self) -> None:
        import tempfile
        from jev_selector import selector_for_env

        for name, value in (("GUARDIAN_SELECTOR", "jev"), ("GUARDIAN_JEV_FALLBACK", "rules"), ("GUARDIAN_JEV_API_KEY", SECRET)):
            old = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(lambda n=name, o=old: os.environ.pop(n, None) if o is None else os.environ.__setitem__(n, o))
        alerts = []
        with tempfile.TemporaryDirectory() as directory:  # sqlite cannot open a directory as a database
            selector = selector_for_env(directory, on_fallback=lambda t, r: alerts.append(r))
        self.assertFalse(selector.cacheable)
        answer = await selector.choose(task(), CANDS)
        self.assertEqual((answer.fallback_used, answer.note, alerts), (True, "jev_ledger_unavailable", ["jev_ledger_unavailable"]))


class FallbackHttpTests(TaskHttpBase):
    ENV = {
        "GUARDIAN_TASK_API": "true", "GUARDIAN_SELECTOR": "jev", "GUARDIAN_JEV_API_KEY": SECRET,
        "GUARDIAN_JEV_FALLBACK": "rules", "GUARDIAN_CLOUD": "true", "GUARDIAN_DEEPSEEK_API_KEY": CLOUD_KEY,
    }

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.jev = FakeJev(status=503)
        self.module.JEV_TRANSPORT = self.jev
        self.provider = FakeProvider()
        self.module.CLOUD_TRANSPORT = self.provider

    def only_cloud_is_ready(self) -> None:
        self.module.guardian._llama_up = False
        self.module.ram_snapshot = lambda: {"cold_start_allowed": False}
        self.aiwa.models_status = 503

    async def alerts(self, after: int = 0) -> list[dict]:
        return (await (await self.client.get(f"/__guardian/alerts?after={after}")).json())["alerts"]

    async def test_fallback_dispatches_records_evidence_and_emits_one_alert(self) -> None:
        await self.start_worker()
        response = await self.submit(clearance="lan", summary_cleared_for_jev=True)
        self.assertEqual(response.status, 202, await response.text())
        body = await self.wait_status("t1", "succeeded")
        evidence = body["evidence"]
        self.assertEqual((evidence["selector"], evidence["fallback_used"], evidence["fallback_reason"]), ("rules", True, "jev_error"))
        self.assertEqual(evidence["chosen_model"], "workbench-local")
        alerts = await self.alerts()
        self.assertEqual([(a["kind"], a["task_id"], a["detail"]["reason"]) for a in alerts], [("jev_fallback", "t1", "jev_error")])
        self.assertEqual((await self.alerts(after=alerts[0]["id"])), [])

    async def test_an_uncleared_summary_goes_to_rules_without_calling_jev_or_alerting(self) -> None:
        await self.start_worker()
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="lan"))
        self.assertEqual(response.status, 202, await response.text())
        evidence = (await self.wait_status("t1", "succeeded"))["evidence"]
        self.assertEqual((evidence["selector"], evidence["fallback_used"], evidence["fallback_reason"]),
                         ("rules", True, "jev_summary_not_cleared"))
        self.assertEqual(self.jev.calls, [])
        self.assertEqual(await self.alerts(), [])

    async def test_a_pc_job_never_falls_back_to_cloud(self) -> None:
        self.only_cloud_is_ready()
        body = await (await self.submit(clearance="pc", cost_ceiling_usd=5, summary_cleared_for_jev=True)).json()
        self.assertEqual((body["status"], body["reason"]), ("selection_failed", "no_candidates"))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.jev.calls, [])  # nothing to choose from, so Jev is not even asked

    async def test_a_lan_job_never_falls_back_to_cloud(self) -> None:
        self.only_cloud_is_ready()
        body = await (await self.submit(clearance="lan", cost_ceiling_usd=5, summary_cleared_for_jev=True)).json()
        self.assertEqual(body["reason"], "no_candidates")
        self.assertEqual(self.provider.calls, [])

    async def test_an_internet_job_may_fall_back_to_cloud(self) -> None:
        self.only_cloud_is_ready()
        await self.start_worker()
        response = await self.submit(clearance="internet", cost_ceiling_usd=5, summary_cleared_for_jev=True)
        self.assertEqual(response.status, 202, await response.text())
        body = await self.wait_status("t1", "succeeded")
        self.assertEqual((body["evidence"]["chosen_model"], body["evidence"]["fallback_used"]), ("deepseek-flash", True))
        self.assertEqual(len(self.provider.calls), 1)

    async def test_the_fallback_respects_the_cost_ceiling(self) -> None:
        self.only_cloud_is_ready()
        body = await (await self.submit(clearance="internet", cost_ceiling_usd=0, summary_cleared_for_jev=True)).json()
        self.assertEqual(body["reason"], "no_candidates")
        self.assertEqual(self.provider.calls, [])


class FallbackOffTests(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true", "GUARDIAN_SELECTOR": "jev", "GUARDIAN_JEV_API_KEY": SECRET}

    async def test_without_the_flag_a_jev_failure_is_selection_failed_and_raises_no_alert(self) -> None:
        self.module.JEV_TRANSPORT = FakeJev(status=503)
        body = await (await self.submit(clearance="lan", summary_cleared_for_jev=True)).json()
        self.assertEqual((body["status"], body["reason"]), ("selection_failed", "jev_error"))
        self.assertEqual(body["evidence"]["fallback_used"], False)
        self.assertEqual((await (await self.client.get("/__guardian/alerts")).json())["alerts"], [])

    async def test_without_the_flag_an_uncleared_task_is_still_routed_by_rules(self) -> None:
        fake = FakeJev("workbench-local")
        self.module.JEV_TRANSPORT = fake
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="lan"))
        body = await response.json()
        self.assertEqual(response.status, 202, body)
        self.assertEqual(body["evidence"]["selector"], "rules")
        self.assertEqual(body["evidence"]["fallback_reason"], "jev_summary_not_cleared")
        self.assertEqual((fake.calls, (await (await self.client.get("/__guardian/alerts")).json())["alerts"]), ([], []))

    async def test_alerts_endpoint_rejects_a_bad_cursor(self) -> None:
        self.assertEqual((await self.client.get("/__guardian/alerts?after=x")).status, 400)


if __name__ == "__main__":
    unittest.main()
