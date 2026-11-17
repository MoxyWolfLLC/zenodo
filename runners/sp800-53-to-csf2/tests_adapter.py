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
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
import importlib.util

# -I keeps this directory off sys.path, so load the adapter by its path
_spec = importlib.util.spec_from_file_location("providers", os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers.py"))
providers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(providers)


class Stub:
    """Records every request; answers from a script keyed by URL host."""

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
        self.state = os.path.join(self.tmp, "run_state.json")
        self.stub = Stub({"api.openai.com": OPENAI, "api.anthropic.com": ANTHROPIC,
                          "generativelanguage": GOOGLE, "openrouter.ai": OPENROUTER})
        self.a = providers.Adapter(protocol(), transport=self.stub, accounting=self.acc, state=self.state)
        self.q = providers.identity("vote", unit_release="ac-1@5.2.0", proposal_output="p1", quoted_spans=["x"], threshold=0.5)

    def rows(self):
        with open(self.acc) as f:
            return [json.loads(l) for l in f]

    def test_each_provider_records_identities_usage_and_evidence_class(self):
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.a.call("vote", "anthropic/claude-haiku-5.5", "vote-v1", "p", self.q, "e1")
        self.a.call("vote", "google/gemini-2.5-flash-lite", "vote-v1", "p", self.q, "e1")
        self.a.call("vote", "mistralai/mistral-small-2603", "vote-v1", "p", self.q, "e1")
        rows = self.rows()
        self.assertEqual([r["provider"] for r in rows], ["openai", "anthropic", "google", "openrouter"])
        self.assertEqual([r["evidence"] for r in rows], ["usage_priced"] * 3 + ["provider_reported"])
        self.assertEqual(rows[3]["usd"], 0.000123)
        self.assertAlmostEqual(rows[0]["usd"], (500 * 0.1 + 50 * 0.5) / 1e6)
        for r in rows:
            self.assertEqual(r["question_identity"]["id"], self.q["id"])
            self.assertEqual(r["evidence_identity"], "e1")
            self.assertFalse(r["refused"])
            self.assertIn(r["prompt_version"], providers.PROMPT_VERSIONS.values())

    def test_guard_refuses_past_the_allowance_and_records_a_zero_cost_row(self):
        with open(self.acc, "w") as f:   # spend the cap down to the reserve
            f.write(json.dumps({"usd": 50.0}) + "\n")
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(providers.BudgetRefused):
            self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertTrue(err.getvalue().startswith("refused: budget "), repr(err.getvalue()))
        last = self.rows()[-1]
        self.assertTrue(last["refused"]); self.assertEqual(last["evidence"], "refused"); self.assertEqual(last["usd"], 0.0)
        self.assertEqual(len(self.stub.requests), 0)

    def test_reserve_is_released_only_in_the_deliverables_phase(self):
        with open(self.acc, "w") as f:
            f.write(json.dumps({"usd": 50.0}) + "\n")
        self.assertLess(self.a.remaining(), 0.001)
        json.dump({"phase": "deliverables"}, open(self.state, "w"))
        self.assertAlmostEqual(self.a.remaining(), 10.0)
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertEqual(self.rows()[-1]["phase"], "deliverables")

    def test_unpriced_response_is_charged_at_the_ceiling(self):
        self.stub.answers["api.openai.com"] = {"choices": [{"message": {"content": "{}"}}]}
        self.a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        r = self.rows()[-1]
        self.assertEqual(r["evidence"], "unpriced"); self.assertAlmostEqual(r["usd"], r["max_charge"])

    def test_replay_mode_raises_and_counts_the_attempt(self):
        a = providers.Adapter(protocol(), accounting=self.acc, state=self.state, replay=True)
        with self.assertRaises(providers.ReplayViolation):
            a.call("proposal", "openai/gpt-6-luna", "propose-v1", "p", self.q, "e1")
        self.assertEqual(a.replay_counter["attempts"], 1)

    def test_council_majority_and_split(self):
        packet = {"rubric": "r", "unit_id": "ac-1", "unit_text": "t", "context": "c", "hub_id": "GV.OC-01", "hub_text": "h",
                  "levels": "subset_of, intersects_with, equal, superset_of", "null": "no_relationship"}
        out = self.a.council(packet, self.q, "e1")
        self.assertEqual(out["level"], "subset_of"); self.assertEqual(out["reason"], "majority")
        self.stub.answers["api.anthropic.com"] = {"content": [{"text": '{"level": "equal"}'}], "usage": {"input_tokens": 1, "output_tokens": 1}}
        self.stub.answers["openrouter.ai"] = {"choices": [{"message": {"content": '{"level": "superset_of"}'}}], "usage": {"cost": 0.0001}}
        out = self.a.council(packet, self.q, "e1")
        self.assertEqual(out["level"], "no_relationship"); self.assertEqual(out["reason"], "split")

    def test_identity_is_order_independent_and_stage_bound(self):
        a = providers.identity("vote", b=1, a=2); b = providers.identity("vote", a=2, b=1)
        self.assertEqual(a["id"], b["id"]); self.assertNotEqual(a["id"], providers.identity("ruling", a=2, b=1)["id"])


if __name__ == "__main__":
    unittest.main()
