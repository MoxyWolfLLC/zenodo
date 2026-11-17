#!/usr/bin/env python3
"""Item 3 check: the adapter against a recording stub, no network.

    python3 -I -m unittest discover -s . -p tests_adapter.py

The plan names `python3 -I -m unittest tests_adapter.py`. -I implies -P, which keeps every directory
off sys.path, so that exact form cannot import this module (or any) on CPython 3.11+; discover adds
its start directory itself, which is the one difference. Reported as a deviation in the item's review.
"""
import io
import json
import os
import re
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from decimal import Decimal
import importlib.util

# -I keeps this directory off sys.path, so load the adapter by its path
_spec = importlib.util.spec_from_file_location("providers", os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.py"))
providers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(providers)


class Stub:
    """Records every request; answers from a script keyed by URL host."""
    kind = "stub"

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def post(self, url, headers, body, timeout=120):
        self.requests.append((url, body))
        for host, answer in self.answers.items():
            if host in url:
                return answer(body) if callable(answer) else answer
        raise AssertionError(f"unexpected url {url}")


def protocol():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "protocol.json")) as f:
        return json.load(f)


OPENAI = {"choices": [{"message": {"content": '{"level": "subset_of", "quotes": ["x"], "confidence": 0.8}'}}],
          "usage": {"prompt_tokens": 500, "completion_tokens": 50}}
ANTHROPIC = {"content": [{"text": '{"vote": "subset_of", "quotes_verbatim": true, "confidence": 0.7}'}],
             "usage": {"input_tokens": 400, "output_tokens": 40}}
GOOGLE = {"candidates": [{"content": {"parts": [{"text": '{"level": "subset_of", "reason": "r"}'}]}}],
          "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 30}}
OPENROUTER = {"choices": [{"message": {"content": '{"level": "subset_of", "reason": "r"}'}}],
              "usage": {"prompt_tokens": 600, "completion_tokens": 60, "cost": 0.000123}}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.acc = os.path.join(self.tmp, "accounting.jsonl")
        self.ev = os.path.join(self.tmp, "events.jsonl")
        self.state = os.path.join(self.tmp, "run_state.json")
        self.stub = Stub({"api.openai.com": OPENAI, "api.anthropic.com": ANTHROPIC,
                          "generativelanguage": GOOGLE, "openrouter.ai": OPENROUTER})
        self.a = providers.Adapter(protocol(), transport=self.stub, accounting=self.acc, state=self.state, events=self.ev,
                                   protocol_sha256="f" * 64)
        self.q = providers.identity("vote", unit_release="ac-1@5.2.0", proposal_output="p1", quoted_spans=["x"], threshold=0.5)

    def rows(self, path=None):
        with open(path or self.acc) as f:
            return [json.loads(l) for l in f]

    def spend(self, total, provider=None, usd=None):
        with open(self.acc, "w") as f:
            f.write(json.dumps({"charged_usd": str(total), "provider": provider or "other"}) + "\n")

    def test_each_provider_records_identities_usage_and_evidence_class(self):
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.a.call("vote", "anthropic/claude-haiku-5.5", "vote-v1", "p", self.q, "e1")
        self.a.call("vote", "google/gemini-2.5-flash-lite", "vote-v1", "p", self.q, "e1")
        self.a.call("vote", "mistralai/mistral-small-2603", "vote-v1", "p", self.q, "e1")
        rows = self.rows()
        # Gemini is reached through OpenRouter (2026-11-17): it bills as openrouter and seats as google
        self.assertEqual([r["provider"] for r in rows], ["openai", "anthropic", "openrouter", "openrouter"])
        self.assertEqual([r["family"] for r in rows], ["openai", "anthropic", "google", "mistralai"])
        self.assertEqual([r["evidence"] for r in rows], ["usage_priced"] * 2 + ["provider_reported"] * 2)
        self.assertEqual(Decimal(rows[3]["charged_usd"]), Decimal("0.000123"))
        self.assertEqual(Decimal(rows[0]["charged_usd"]), (Decimal(500) * Decimal("0.1") + Decimal(50) * Decimal("0.5")) / Decimal(1_000_000))
        for r in rows:
            self.assertEqual(r["question_identity"]["id"], self.q["id"])
            self.assertEqual(r["evidence_identity"], "e1")
            self.assertFalse(r["refused"])
            self.assertIn(r["prompt_version"], providers.PROMPT_VERSIONS.values())
            self.assertEqual(Decimal(r["max_expected_charge_usd"]), self.a.max_charge(r["model"], *self.a.ceilings(r["stage"])))
        starts = [e for e in self.rows(self.ev) if e["event"] == "call_started"]
        self.assertEqual([e["provider"] for e in starts], ["openai", "anthropic", "openrouter", "openrouter"])
        self.assertEqual({e["transport"] for e in starts}, {"stub"})
        self.assertEqual({e["protocol_sha256"] for e in starts}, {"f" * 64})

    def test_guard_refuses_past_the_allowance_before_any_request(self):
        self.spend("50.0")   # the global allowance is now exactly the reserve: nothing fits
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(providers.BudgetRefused):
            self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertRegex(err.getvalue(), r"^refused: budget [0-9.]+\n$")
        self.assertEqual(Decimal(err.getvalue().split()[2]), Decimal(0))
        last = self.rows()[-1]
        self.assertTrue(last["refused"]); self.assertEqual(last["evidence"], "refused")
        self.assertEqual(Decimal(last["charged_usd"]), Decimal(0)); self.assertEqual(Decimal(last["remaining_usd"]), Decimal(0))
        self.assertEqual(len(self.stub.requests), 0)
        self.assertFalse(os.path.exists(self.ev), "no call_started for a refused call")

    def test_provider_allowance_binds_when_smaller_than_the_global_one(self):
        caps = self.a.provider_caps
        self.spend(str(caps["openrouter"] - Decimal("0.0000001")), provider="openrouter")   # openrouter spent to its cap; global has 40 left
        self.assertEqual(self.a.remaining("openrouter"), Decimal("0.0000001"))
        self.assertEqual(self.a.remaining("openai"), caps["openai"])   # its own cap, below the global 40
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(providers.BudgetRefused):
            self.a.call("vote", "google/gemini-2.5-flash-lite", "vote-v1", "p", self.q, "e1")   # bills as openrouter: refused
        self.assertEqual(Decimal(err.getvalue().split()[2]), Decimal("0.0000001"))
        self.a.call("vote", "anthropic/claude-haiku-5.5", "vote-v1", "p", self.q, "e1")   # another provider still goes
        self.assertEqual(len(self.stub.requests), 1)

    def test_reserve_is_released_only_in_the_deliverables_phase(self):
        self.spend("50.0")
        self.assertEqual(self.a.remaining(), Decimal(0))
        with open(self.state, "w") as f:
            json.dump({"phase": "deliverables"}, f)
        self.assertEqual(self.a.remaining(), Decimal(10))
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertEqual(self.rows()[-1]["phase"], "deliverables")

    def test_money_is_exact_so_a_one_token_charge_still_reduces_the_allowance(self):
        self.stub.answers["api.openai.com"] = {"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 1}}
        before = self.a.remaining("openai")
        self.a.embed("openai/text-embedding-3-small", "x", self.q)
        after = self.a.remaining("openai")
        self.assertEqual(before - after, Decimal("0.02") / Decimal(1_000_000))
        self.assertNotIn("e-", self.rows()[-1]["charged_usd"])   # plain decimal text, exact

    def test_a_ledger_row_without_charged_usd_is_refused_not_read_as_zero(self):
        with open(self.acc, "w") as f:
            f.write(json.dumps({"usd": 0.0, "provider": "openai"}) + "\n")
        with self.assertRaises(ValueError):
            self.a.remaining("openai")

    def test_unpriced_response_is_charged_at_the_ceiling(self):
        self.stub.answers["api.openai.com"] = {"choices": [{"message": {"content": "{}"}}]}
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        r = self.rows()[-1]
        self.assertEqual(r["evidence"], "unpriced"); self.assertEqual(r["charged_usd"], r["max_expected_charge_usd"])

    def test_a_completed_request_is_on_the_ledger_even_when_its_content_is_unusable(self):
        self.stub.answers["openrouter.ai"] = {"choices": [], "usage": {"prompt_tokens": 600, "completion_tokens": 60, "cost": 0.0005}}
        with self.assertRaises(IndexError):
            self.a.call("vote", "mistralai/mistral-small-2603", "vote-v1", "p", self.q, "e1")
        r = self.rows()[-1]
        self.assertFalse(r["refused"]); self.assertEqual(r["evidence"], "provider_reported")
        self.assertEqual(Decimal(r["charged_usd"]), Decimal("0.0005"))
        self.stub.answers["api.openai.com"] = {"data": [], "usage": {"prompt_tokens": 7}}
        with self.assertRaises(IndexError):
            self.a.embed("openai/text-embedding-3-small", "x", self.q)
        self.assertEqual(self.rows()[-1]["tokens_in"], 7)

    def test_replay_mode_raises_counts_and_logs_the_attempt(self):
        a = providers.Adapter(protocol(), accounting=self.acc, state=self.state, events=self.ev, replay=True)
        with self.assertRaises(providers.ReplayViolation):
            a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertEqual(a.replay_counter["attempts"], 1)
        self.assertEqual([e["event"] for e in self.rows(self.ev)], ["call_started", "outbound_attempt"])
        self.assertFalse(os.path.exists(self.acc), "a refused outbound attempt charges nothing")

    def test_council_majority_and_split(self):
        packet = {"rubric": "r", "unit_id": "ac-1", "unit_text": "t", "context": "c", "hub_id": "GV.OC-01", "hub_text": "h",
                  "levels": "subset_of, intersects_with, equal, superset_of", "null": "no_relationship"}
        out = self.a.council(packet, self.q, "e1")
        self.assertEqual(out["level"], "subset_of"); self.assertEqual(out["reason"], "majority")
        self.assertEqual([e["role"] for e in self.rows(self.ev)], ["council"] * 3)
        self.stub.answers["api.anthropic.com"] = {"content": [{"text": '{"level": "equal"}'}], "usage": {"input_tokens": 1, "output_tokens": 1}}
        by_model = {"google/gemini-2.5-flash-lite": "intersects_with", "mistralai/mistral-large-4-0": "superset_of"}
        self.stub.answers["openrouter.ai"] = lambda body: {"choices": [{"message": {"content": json.dumps({"level": by_model[body["model"]]})}}],
                                                           "usage": {"cost": 0.0001}}
        out = self.a.council(packet, self.q, "e1")
        self.assertEqual(out["level"], "no_relationship"); self.assertEqual(out["reason"], "split")

    def test_every_template_renders_and_an_oversized_input_is_refused_before_dispatch(self):
        for stage, fields in (("extraction", dict(unit_id="ac-1", unit_text="t")),
                              ("proposal", dict(unit_id="ac-1", unit_text="t", activities="a", hub_id="GV.OC-01", hub_text="h", levels="l", null="n")),
                              ("vote", dict(unit_id="ac-1", hub_id="GV.OC-01", level="equal", quotes="q", unit_text="t", hub_text="h", levels="l", null="n")),
                              ("council", dict(rubric="r", unit_id="ac-1", unit_text="t", context="c", hub_id="GV.OC-01", hub_text="h", levels="l", null="n"))):
            self.assertIn("ac-1", providers.render(stage, **fields))
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(providers.BudgetRefused):
            self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "x" * 4000, self.q, "e1")
        self.assertRegex(err.getvalue(), r"^refused: budget [0-9.]+\n$"); self.assertIn("over ceiling", self.rows()[-1]["refusal"])
        self.assertEqual(len(self.stub.requests), 0)
        with redirect_stderr(io.StringIO()), self.assertRaises(providers.BudgetRefused):
            self.a.embed("openai/text-embedding-3-small", "y" * 2100, self.q)
        self.assertEqual(len(self.stub.requests), 0)
        self.assertGreaterEqual(providers.token_bound("a b c d e f"), 6 + providers.REQUEST_OVERHEAD)
        self.assertGreaterEqual(providers.token_bound("日本語"), 9 + providers.REQUEST_OVERHEAD)

    def test_run_pass_maps_refusals_to_exit_codes(self):
        def refused():
            raise providers.BudgetRefused("x")

        def replay():
            raise providers.ReplayViolation("y")
        self.assertEqual(providers.run_pass(refused), 4); self.assertEqual(providers.run_pass(replay), 5)
        self.assertEqual(providers.run_pass(lambda: None), 0)

    def test_identity_is_order_independent_and_stage_bound(self):
        a = providers.identity("vote", b=1, a=2); b = providers.identity("vote", a=2, b=1)
        self.assertEqual(a["id"], b["id"]); self.assertNotEqual(a["id"], providers.identity("ruling", a=2, b=1)["id"])


    def test_provider_allowance_holds_under_concurrent_calls(self):
        """F1 (review 20261117-084733): two calls that each fit the provider allowance alone must not both pass."""
        import threading
        caps = self.a.provider_caps
        one = self.a.max_charge("google/gemini-2.5-flash-lite", *self.a.ceilings("vote"))
        self.spend(str(caps["openrouter"] - one - Decimal("0.0000001")), provider="openrouter")   # room for exactly one
        in_flight, released = threading.Event(), threading.Event()
        slow = self.stub.answers["openrouter.ai"]

        def held(body):
            in_flight.set()               # the first request is out and not yet settled
            released.wait(5)
            return slow
        self.stub.answers["openrouter.ai"] = held
        results, errs = [], []

        def first():
            results.append(self.a.call("vote", "google/gemini-2.5-flash-lite", "vote-v1", "p", self.q, "e1"))

        def second():
            in_flight.wait(5)
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    results.append(self.a.call("vote", "google/gemini-2.5-flash-lite", "vote-v1", "p", self.q, "e1"))
            except providers.BudgetRefused:
                errs.append(err.getvalue())
            released.set()
        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(10)
        self.assertEqual((len(results), len(errs)), (1, 1))
        self.assertEqual(len(self.stub.requests), 1)

    def test_every_attempt_is_accounted(self):
        """F2 (review 20261117-084733): an answered error is logged and retried on its own guard; an unknown outcome
        is charged at the ceiling and not retried."""
        providers.BACKOFF = (0, 0, 0, 0, 0, 0)
        calls = {"n": 0}
        good = OPENAI

        def flaky(body):
            calls["n"] += 1
            if calls["n"] == 1:
                raise providers.TransportError("HTTP 429", answered=True, retryable=True, status=429)
            return good
        self.stub.answers["api.openai.com"] = flaky
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        events = self.rows(self.ev)
        self.assertEqual([e["event"] for e in events], ["call_started", "call_failed", "call_started"])
        self.assertEqual([e.get("attempt") for e in events], [1, 1, 2])
        rows = self.rows()
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["evidence"], "usage_priced")
        self.stub.answers["api.openai.com"] = lambda body: (_ for _ in ()).throw(
            providers.TransportError("timed out", answered=False, retryable=False))
        with self.assertRaises(providers.TransportError):
            self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        last = self.rows()[-1]
        self.assertEqual(last["evidence"], "unpriced"); self.assertIn("timed out", last["failure"])
        self.assertEqual(Decimal(last["charged_usd"]), self.a.max_charge("openai/gpt-6-luna", *self.a.ceilings("proposal")))
        self.assertEqual(self.a._inflight.get("*", Decimal(0)), Decimal(0))
        self.stub.answers["api.openai.com"] = lambda body: (_ for _ in ()).throw(
            providers.TransportError("HTTP 400", answered=True, retryable=False, status=400))
        with self.assertRaises(providers.TransportError):
            self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertEqual(len(self.rows()), 2, "an answered 400 bills nothing and adds no row")
        self.assertEqual(self.rows(self.ev)[-1]["event"], "call_failed")


if __name__ == "__main__":
    unittest.main()
