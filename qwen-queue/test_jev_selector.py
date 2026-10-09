"""Step 7: Jev as the selector. Fake transport only; no real Jev call is ever made."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import jev_client as jc
import jev_secret as js
import jev_spend as jsp
import model_registry as mr
from candidates import Candidate
from jev_selector import JevSelector, UnavailableSelector, profile_for
from task_dispatch import TaskSpec
from test_task_api import TaskHttpBase, payload

SECRET = "sk-test-SECRET-key-123"


def probs(choice: str, ids: list[str], high: float = 0.7) -> dict:
    rest = (1 - high) / (len(ids) - 1)
    return {i: (high if i == choice else rest) for i in ids}


def jev_body(choice: str, ids: list[str], confidence: float = 0.9) -> dict:
    return {
        "model": jc.JEV_MODEL,
        "answers": {"route": {"type": "choice", "choice": choice, "probabilities": probs(choice, ids), "confidence": confidence}},
        "usage": {"input_tokens": 100, "output_tokens": 5},
    }


class FakeJev:
    """Scripted transport. `answer` maps the offered ids (from the request body) to a response."""

    def __init__(self, pick: str | None = None, *, status: int = 200, raw=None, delay: float = 0.0, error: Exception | None = None):
        self.pick, self.status, self.raw, self.delay, self.error = pick, status, raw, delay, error
        self.calls: list[tuple[str, dict, dict]] = []

    async def __call__(self, url: str, headers: dict, body: bytes):
        sent = json.loads(body)
        self.calls.append((url, dict(headers), sent))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        ids = sorted(sent["questions"]["route"]["criteria"])
        raw = self.raw if self.raw is not None else jev_body(self.pick or "abstain", ids)
        return self.status, raw

    @property
    def offered(self) -> list[str]:
        return sorted(self.calls[-1][2]["questions"]["route"]["criteria"])


async def key() -> str:
    return SECRET


def candidate(model_id: str, **over) -> Candidate:
    base = dict(model_id=model_id, egress="lan", readiness=mr.Readiness(mr.READY), wake_seconds=0.0,
                cost_estimate_usd=0.0, quality_tier=2, needs_approval=False, context_tokens=8000)
    base.update(over)
    return Candidate(**base)


def task(summary: str = "write a unit test") -> TaskSpec:
    return TaskSpec("t1", "k1", summary, [{"role": "user", "content": "PRIVATE MESSAGE BODY"}], "lan", 0, 1, 5, {})


def ledger(cap: int | None = None) -> jsp.JevSpendLedger:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    return jsp.JevSpendLedger(conn, cap_microusd=cap)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def req(self) -> jc.JevChoiceRequest:
        return jc.JevChoiceRequest("t", "sum", "pick", {"abstain": "a", "m1": "x", "m2": "y"})

    async def ask(self, transport, **kw):
        return await jc.request_jev_choice(key, self.req(), transport=transport, **kw)

    async def test_valid_answer_parses(self) -> None:
        answer = await self.ask(FakeJev("m1"))
        self.assertEqual((answer.choice, answer.input_tokens, answer.output_tokens), ("m1", 100, 5))

    async def test_request_carries_contract_shape_and_one_post(self) -> None:
        fake = FakeJev("m1")
        await self.ask(fake)
        url, headers, body = fake.calls[0]
        self.assertEqual(url, jc.JEV_URL)
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(body["questions"]["route"]["type"], "choice")
        self.assertEqual(headers["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(len(fake.calls), 1)

    async def assertInvalid(self, raw, code="response_invalid") -> None:
        with self.assertRaises(jc.JevChoiceUnavailable) as ctx:
            await self.ask(FakeJev(raw=raw))
        self.assertEqual(ctx.exception.code, code)

    async def test_strict_parse_rejections(self) -> None:
        ids = ["abstain", "m1", "m2"]
        good = jev_body("m1", ids)
        cases = {
            "not_object": "nope",
            "extra_top_key": {**good, "extra": 1},
            "wrong_model": {**good, "model": "jev-9"},
            "choice_not_offered": jev_body("m3", ids),
            "prob_keys_mismatch": {**good, "answers": {"route": {**good["answers"]["route"], "probabilities": {"m1": 1.0}}}},
            "probs_not_sum_one": {**good, "answers": {"route": {**good["answers"]["route"], "probabilities": {"abstain": 0.1, "m1": 0.5, "m2": 0.1}}}},
            "choice_not_max": {**good, "answers": {"route": {**good["answers"]["route"], "choice": "m2"}}},
            "confidence_out_of_range": jev_body("m1", ids, confidence=1.5),
            "bool_usage": {**good, "usage": {"input_tokens": True, "output_tokens": 1}},
            "negative_usage": {**good, "usage": {"input_tokens": -1, "output_tokens": 1}},
            "usage_extra": {**good, "usage": {"input_tokens": 1, "output_tokens": 1, "x": 1}},
            "nan_prob": {**good, "answers": {"route": {**good["answers"]["route"], "probabilities": {"abstain": float("nan"), "m1": 0.5, "m2": 0.5}}}},
        }
        for name, raw in cases.items():
            with self.subTest(name):
                await self.assertInvalid(raw)

    async def test_timeout_and_http_errors_map_to_codes(self) -> None:
        with self.assertRaises(jc.JevChoiceUnavailable) as ctx:
            await self.ask(FakeJev("m1", delay=1.0), timeout_s=0.05)
        self.assertEqual(ctx.exception.code, "timeout")
        for status in (500, 401, 429):
            with self.subTest(status), self.assertRaises(jc.JevChoiceUnavailable) as ctx:
                await self.ask(FakeJev("m1", status=status))
            self.assertEqual(ctx.exception.code, "http_error")
        with self.assertRaises(jc.JevChoiceUnavailable) as ctx:
            await self.ask(FakeJev(error=ConnectionError(f"boom {SECRET}")))
        self.assertEqual(ctx.exception.code, "http_error")
        self.assertNotIn(SECRET, str(ctx.exception))

    async def test_bad_key_and_bad_request_never_reach_the_network(self) -> None:
        fake = FakeJev("m1")

        async def broken() -> str:
            raise RuntimeError(f"gcloud said {SECRET}")

        async def newline() -> str:
            return "ab\ncd"

        for loader in (broken, newline):
            with self.assertRaises(jc.JevChoiceUnavailable) as ctx:
                await jc.request_jev_choice(loader, self.req(), transport=fake)
            self.assertEqual(ctx.exception.code, "credential_unavailable")
            self.assertNotIn(SECRET, str(ctx.exception))
        no_abstain = jc.JevChoiceRequest("t", "s", "i", {"m1": "x", "m2": "y"})
        with self.assertRaises(jc.JevChoiceUnavailable):
            await jc.request_jev_choice(key, no_abstain, transport=fake)
        for t in (0, -1, 31):
            with self.assertRaises(jc.JevChoiceUnavailable):
                await self.ask(fake, timeout_s=t)
        huge = jc.JevChoiceRequest("t", "s" * 20_000, "i", {"abstain": "a", "m1": "x"})
        with self.assertRaises(jc.JevChoiceUnavailable):
            await jc.request_jev_choice(key, huge, transport=fake)
        self.assertEqual(fake.calls, [])


class SpendTests(unittest.TestCase):
    def test_cap_stops_reservations_and_defaults_to_two_dollars(self) -> None:
        led = ledger()
        self.assertEqual(led.cap, 2_000_000)
        small = ledger(cap=8_000)
        self.assertEqual([small.reserve(100.0) for _ in range(3)], [True, True, False])
        self.assertEqual(small.reserved_for(100.0), {"reserved_microusd": 8_000, "decisions": 2})

    def test_new_utc_day_has_a_fresh_cap(self) -> None:
        small = ledger(cap=4_000)
        self.assertTrue(small.reserve(0.0))
        self.assertFalse(small.reserve(10.0))
        self.assertTrue(small.reserve(86_400.0))

    def test_cap_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "jev.sqlite3")
            first = jsp.JevSpendLedger.open(path, cap_microusd=8_000)
            self.assertTrue(first.reserve(5.0) and first.reserve(5.0))
            second = jsp.JevSpendLedger.open(path, cap_microusd=8_000)  # a restarted Guardian
            self.assertFalse(second.reserve(5.0))

    def test_env_cap_falls_back_on_bad_values(self) -> None:
        for raw, want in (("0.5", 500_000), ("abc", 2_000_000), ("0", 2_000_000), ("-3", 2_000_000), ("inf", 2_000_000), ("nan", 2_000_000)):
            with self.subTest(raw):
                os.environ["GUARDIAN_JEV_DAILY_CAP_USD"] = raw
                self.addCleanup(os.environ.pop, "GUARDIAN_JEV_DAILY_CAP_USD", None)
                self.assertEqual(jsp.cap_microusd_from_env(), want)


class SecretTests(unittest.IsolatedAsyncioTestCase):
    REF = "projects/carter-personal-secrets/secrets/jev-key/versions/latest"
    WHO = "carter@example.com"

    async def test_gsm_loader_checks_principal_and_payload(self) -> None:
        async def ok(ref):
            return self.WHO, SECRET

        self.assertEqual(await js.gsm_key_loader(self.REF, self.WHO, ok)(), SECRET)

        async def other(ref):
            return "someone@else.com", SECRET

        async def blank(ref):
            return self.WHO, ""

        async def boom(ref):
            raise RuntimeError(SECRET)

        for accessor, code in ((other, "binding_mismatch"), (blank, "payload_invalid"), (boom, "access_unavailable")):
            with self.subTest(code), self.assertRaises(js.JevCredentialUnavailable) as ctx:
                await js.gsm_key_loader(self.REF, self.WHO, accessor)()
            self.assertEqual(ctx.exception.code, code)
            self.assertNotIn(SECRET, str(ctx.exception))

    def test_bad_or_missing_reference_is_refused_up_front(self) -> None:
        with self.assertRaises(js.JevCredentialUnavailable) as ctx:
            js.gsm_key_loader(None, None)
        self.assertEqual(ctx.exception.code, "reference_missing")
        for ref, who in (("projects/p/secrets/s", self.WHO), (self.REF, "not an email"), ("projects/p/secrets/s/versions/0", self.WHO)):
            with self.assertRaises(js.JevCredentialUnavailable) as ctx:
                js.gsm_key_loader(ref, who)
            self.assertEqual(ctx.exception.code, "reference_invalid")

    async def test_env_override_wins_and_is_stripped(self) -> None:
        os.environ["GUARDIAN_JEV_API_KEY"] = f"  {SECRET}\n"
        self.addCleanup(os.environ.pop, "GUARDIAN_JEV_API_KEY", None)
        self.assertEqual(await js.build_key_loader()(), SECRET)

    def test_no_source_configured_raises(self) -> None:
        for var in ("GUARDIAN_JEV_API_KEY", "GUARDIAN_JEV_SECRET_REF", "GUARDIAN_JEV_SECRET_PRINCIPAL"):
            os.environ.pop(var, None)
        with self.assertRaises(js.JevCredentialUnavailable):
            js.build_key_loader()


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    CANDS = [candidate("workbench-local", egress="pc"), candidate("nemotron-r9700")]

    def selector(self, fake, led=None, **kw) -> JevSelector:
        return JevSelector(led or ledger(), key, transport=fake, **kw)

    async def test_valid_pick_is_returned(self) -> None:
        fake = FakeJev("nemotron-r9700")
        answer = await self.selector(fake).choose(task(), self.CANDS)
        self.assertEqual((answer.model_id, answer.reason), ("nemotron-r9700", "jev_pick"))

    async def test_request_contains_only_offered_candidates_and_the_summary(self) -> None:
        fake = FakeJev("workbench-local")
        await self.selector(fake).choose(task("short summary"), self.CANDS)
        self.assertEqual(fake.offered, ["abstain", "nemotron-r9700", "workbench-local"])
        sent = json.dumps(fake.calls[0][2])
        self.assertIn("short summary", sent)
        self.assertNotIn("PRIVATE MESSAGE BODY", sent)
        self.assertNotIn("qwen27b-r9700", sent)
        self.assertNotIn("deepseek-flash", sent)

    async def test_long_summary_is_truncated(self) -> None:
        fake = FakeJev("workbench-local")
        await self.selector(fake).choose(task("x" * 9000), self.CANDS)
        self.assertEqual(len(fake.calls[0][2]["state"]["summary"]), 2000)

    async def test_every_failure_is_a_reasoned_non_pick(self) -> None:
        cases = {
            "jev_abstain": FakeJev("abstain"),
            "jev_timeout": FakeJev("workbench-local", delay=1.0),
            "jev_error": FakeJev("workbench-local", status=503),
            "jev_invalid_response": FakeJev(raw={"model": "x"}),
        }
        for reason, fake in cases.items():
            with self.subTest(reason):
                answer = await self.selector(fake, timeout_s=0.05).choose(task(), self.CANDS)
                self.assertEqual((answer.model_id, answer.reason), (None, reason))

    async def test_pick_outside_offered_ids_is_rejected(self) -> None:
        ids = ["abstain", "nemotron-r9700", "workbench-local"]
        fake = FakeJev(raw=jev_body("deepseek-flash", ids + ["deepseek-flash"]))
        answer = await self.selector(fake).choose(task(), self.CANDS)
        self.assertEqual((answer.model_id, answer.reason), (None, "jev_invalid_response"))

    async def test_low_confidence_is_a_failure_when_a_threshold_is_set(self) -> None:
        fake = FakeJev(raw=jev_body("workbench-local", ["abstain", "nemotron-r9700", "workbench-local"], confidence=0.2))
        answer = await self.selector(fake, confidence_threshold=0.5).choose(task(), self.CANDS)
        self.assertEqual(answer.reason, "jev_low_confidence")

    async def test_cap_reached_never_calls_jev(self) -> None:
        fake = FakeJev("workbench-local")
        sel = self.selector(fake, ledger(cap=4_000))
        self.assertEqual((await sel.choose(task(), self.CANDS)).reason, "jev_pick")
        answer = await sel.choose(task(), self.CANDS)
        self.assertEqual((answer.model_id, answer.reason), (None, "jev_cap_reached"))
        self.assertEqual(len(fake.calls), 1)

    async def test_spend_is_recorded_even_when_the_call_fails(self) -> None:
        led = ledger()
        await self.selector(FakeJev(status=500), led).choose(task(), self.CANDS)
        self.assertEqual(led.reserved_for()["decisions"], 1)

    async def test_bad_inputs_never_reach_jev(self) -> None:
        fake = FakeJev("workbench-local")
        sel = self.selector(fake)
        too_many = [candidate(f"m{i}") for i in range(9)]
        for t, cands in ((task(), []), (task("   "), self.CANDS), (task(), too_many), (task(), [candidate("abstain")])):
            self.assertEqual((await sel.choose(t, cands)).reason, "jev_request_invalid")
        self.assertEqual(fake.calls, [])

    async def test_key_never_appears_in_logs_or_answers(self) -> None:
        with self.assertLogs("guardian.jev", level="WARNING") as logs:
            answer = await self.selector(FakeJev(error=ConnectionError(SECRET))).choose(task(), self.CANDS)
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertNotIn(SECRET, repr(answer))

    async def test_profile_exposes_no_free_text(self) -> None:
        self.assertEqual(set(json.loads(profile_for(self.CANDS[0]))),
                         {"readiness", "wake_seconds", "cost_estimate_usd", "quality_tier", "needs_approval", "egress", "context_tokens"})

    async def test_unavailable_selector_reports_its_reason(self) -> None:
        answer = await UnavailableSelector("jev_reference_missing").choose(task(), self.CANDS)
        self.assertEqual((answer.model_id, answer.reason), (None, "jev_reference_missing"))


class JevTaskApiTests(TaskHttpBase):
    ENV = {"GUARDIAN_TASK_API": "true", "GUARDIAN_SELECTOR": "jev", "GUARDIAN_JEV_API_KEY": SECRET}

    def use(self, fake: FakeJev) -> FakeJev:
        self.module.JEV_TRANSPORT = fake
        return fake

    async def test_jev_pick_is_dispatched_and_recorded_as_evidence(self) -> None:
        fake = self.use(FakeJev("nemotron-r9700"))
        await self.start_worker()
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="lan"))
        self.assertEqual(response.status, 202, await response.text())
        body = await self.wait_status("t1", "succeeded")
        evidence = body["evidence"]
        self.assertEqual((evidence["selector"], evidence["chosen_model"]), ("jev", "nemotron-r9700"))
        self.assertEqual(evidence["selector_answer"]["reason"], "jev_pick")
        self.assertEqual(fake.offered, ["abstain", "nemotron-r9700", "workbench-local"])

    async def test_pc_clearance_never_offers_the_lan_model(self) -> None:
        fake = self.use(FakeJev("workbench-local"))
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="pc"))
        self.assertEqual(response.status, 202, await response.text())
        self.assertEqual(fake.offered, ["abstain", "workbench-local"])

    async def test_jev_failure_is_selection_failed_with_no_rules_fallback(self) -> None:
        self.use(FakeJev(status=503))
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="lan"))
        body = await response.json()
        self.assertEqual((response.status, body["status"], body["reason"]), (422, "selection_failed", "jev_error"))
        self.assertEqual(self.llama.hits, [])

    async def test_missing_key_source_fails_tasks_with_a_reason(self) -> None:
        os.environ.pop("GUARDIAN_JEV_API_KEY")
        self.use(FakeJev("workbench-local"))
        body = await (await self.client.post("/__guardian/tasks", json=payload())).json()
        self.assertEqual(body["reason"], "jev_reference_missing")


class RulesStaysDefaultTests(TaskHttpBase):
    async def test_selector_flag_off_uses_rules_and_never_calls_jev(self) -> None:
        fake = FakeJev("workbench-local")
        self.module.JEV_TRANSPORT = fake
        response = await self.client.post("/__guardian/tasks", json=payload(clearance="lan"))
        self.assertEqual(response.status, 202)
        self.assertEqual(fake.calls, [])
        self.assertEqual((await response.json())["evidence"]["selector"], "rules")

    async def test_unknown_selector_value_stays_on_rules(self) -> None:
        os.environ["GUARDIAN_SELECTOR"] = "jve"
        response = await self.client.post("/__guardian/tasks", json=payload())
        self.assertEqual((await response.json())["evidence"]["selector"], "rules")


if __name__ == "__main__":
    unittest.main()
