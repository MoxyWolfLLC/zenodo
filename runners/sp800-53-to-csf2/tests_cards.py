"""Item 5: the card sets are runner 1.1.0's, and set_r1.json closes every gate.

    python3 -I -m unittest discover -s . -p tests_cards.py
"""
import importlib.util
import inspect
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER = os.path.join(os.path.dirname(HERE), "models-propose-rules-decide", "cards.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # the paper's engine_hash() looks itself up by module name
    spec.loader.exec_module(mod)
    return mod


ours = load("csf2_cards", os.path.join(HERE, "cards.py"))
paper = load("paper_cards", PAPER)
SETS = ("INTAKE", "AUDIT", "RETRIEVAL", "EVIDENCE", "CURATOR", "TRIAD", "RULING")


class CardLists(unittest.TestCase):
    def test_card_ids_revisions_and_outcomes_equal_runner_1_1_0(self):
        for name in SETS:
            with self.subTest(set=name):
                a = [(cid, rev, out) for cid, rev, _, out in getattr(ours, name)]
                b = [(cid, rev, out) for cid, rev, _, out in getattr(paper, name)]
                self.assertEqual(a, b)

    def test_card_conditions_are_the_same_source(self):
        for name in SETS:
            for (cid, _, f, _), (_, _, g, _) in zip(getattr(ours, name), getattr(paper, name)):
                with self.subTest(card=cid):
                    self.assertEqual(inspect.getsource(f).strip(), inspect.getsource(g).strip())

    def test_engine_helpers_are_the_same_source(self):
        for fn in paper.ENGINE_PARTS:
            with self.subTest(helper=fn):
                self.assertEqual(inspect.getsource(getattr(ours, fn)), inspect.getsource(getattr(paper, fn)))

    def test_set_r1_closes_every_gate_and_pins_the_engine(self):
        with open(os.path.join(HERE, "set_r1.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["gates"], {g: "closed" for g in ["no_match", *ours.LEVELS]})
        self.assertEqual(rec["engine"], ours.engine_hash())
        self.assertEqual(rec["card_ids"], ours.card_ids())
        self.assertEqual(set(rec["domain"]), {"unit", "hub", "scale", "scope", "route", "questions", "evidence"})
        self.assertEqual(rec["params"]["H_max"], 0)
        self.assertEqual(rec["domain"]["hub"]["expected"], 106)

    def test_e1_cannot_fire_with_the_retrieval_only_route(self):
        S = ours.make("r1", "openai/p", ["anthropic/a", "google/g", "openrouter/m"])
        e1 = next(c for c in S["cards"]["evidence"] if c[0] == "E1")
        for hub_att in range(4):
            self.assertFalse(e1[2]({"prop": {"needs_hub": True}, "hub_att": hub_att}, S["params"]))


if __name__ == "__main__":
    unittest.main()
