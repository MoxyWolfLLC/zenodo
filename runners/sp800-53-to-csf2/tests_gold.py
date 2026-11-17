"""Item 7: the reference standard's rules, checked without a model.

    python3 -I -m unittest discover -s . -p tests_gold.py
"""
import importlib.util
import json
import math
import os
import random
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gold, score = load("gold"), load("score")


class Packets(unittest.TestCase):
    def test_forbidden_fields_are_found_at_any_depth_and_nowhere_in_a_blind_packet(self):
        dirty = {"unit": {"id": "AC-1"}, "context": {"nested": [{"Proposal": "x"}]}, "vote": 1}
        self.assertEqual(sorted(gold.forbidden_paths(dirty)), ["context.nested[0].Proposal", "vote"])
        self.assertEqual(list(gold.forbidden_paths(gold.scrub(dirty))), [])
        blind = {"unit": {"id": "AC-1", "text": "t"}, "context": {"family": "ac-1", "base_title": "T"}, "candidate": {"id": "PR.AA-01", "text": "h"}, "rubric": "r"}
        self.assertEqual(list(gold.forbidden_paths(blind)), [])
        self.assertEqual(gold.digest(blind), gold.digest(json.loads(json.dumps(blind))))


class Draws(unittest.TestCase):
    def test_draws_are_seeded_sorted_and_capped(self):
        protocol = {"seed": "abc"}
        pop = [f"p{i}" for i in range(100)]
        a = gold.draw(protocol, "double_ruled", "evaluation", list(reversed(pop)), 20)
        b = gold.draw(protocol, "double_ruled", "evaluation", pop, 20)
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 20)
        self.assertNotEqual(a, gold.draw(protocol, "double_ruled", "test", pop, 20))
        self.assertEqual(len(gold.draw(protocol, "ss_audit", "evaluation", pop[:7], 30)), 7, "all of them when fewer exist")
        self.assertEqual(gold.draw(protocol, "ss_audit", "evaluation", [], 30), [])
        self.assertEqual(gold.share_count(0.2, 1280), 256)
        self.assertEqual(gold.share_count(0.2, 2), 1)
        self.assertEqual(gold.share_count(0.2, 0), 0)
        self.assertEqual(gold.draw_seed(protocol, "missed_link", "evaluation"),
                         __import__("hashlib").sha256(b"abc:missed_link:evaluation").hexdigest())


class Statistics(unittest.TestCase):
    def test_krippendorff_alpha_nominal(self):
        self.assertEqual(gold.krippendorff_nominal([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")]), 1.0)
        self.assertIsNone(gold.krippendorff_nominal([("a", "a")]))
        # Krippendorff's worked example shape: two coders, nominal; perfect disagreement is below zero
        self.assertLess(gold.krippendorff_nominal([("a", "b"), ("a", "b"), ("b", "a"), ("b", "a")]), 0)
        mixed = gold.krippendorff_nominal([("a", "a"), ("a", "b"), ("b", "b"), ("a", "a"), ("b", "b"), ("c", "c")])
        self.assertTrue(0 < mixed < 1)

    def test_anchoring_statistic(self):
        rows = [("equal", "equal", "equal"), ("equal", "subset_of", "equal"), ("no_relationship", "equal", "equal"), ("equal", "equal", "subset_of")]
        s = gold.anchoring_stat(rows)
        self.assertEqual((s["agree_blind"], s["agree_assisted"], s["difference"], s["discordant"], s["blind_wins_among_discordant"]), (0.5, 0.5, 0.0, 2, 1))
        self.assertEqual(len(s["wilson_95"]), 2)
        self.assertEqual(gold.anchoring_stat([])["difference"], None)

    def test_wilson_gate_rules(self):
        self.assertAlmostEqual(score.wilson_lower(25, 25), 0.90235, places=4)
        self.assertEqual(score.wilson_lower(0, 0), 0.0)
        self.assertGreater(score.wilson_lower(49, 50), 0.90)
        self.assertLess(score.wilson_lower(24, 25), 0.90)


class Scoring(unittest.TestCase):
    def test_score_rows_from_a_synthetic_store(self):
        import sqlite3, tempfile, shutil
        tmp = tempfile.mkdtemp()
        store = load("store")
        db_path = os.path.join(tmp, "s.sqlite"); store.init(db_path)
        con = sqlite3.connect(db_path)
        cfg = {"c_min": 0.7, "sha256": "x", "prompt_versions": {"proposal": "propose-v1"}}
        with con:
            for i in range(30):
                rel = f"ac-{i}@r"
                con.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?,?)", (rel, f"ac-{i}", "r", f"ac-{i // 2}", "ac", "base", "evaluation", "t", 1, None))
                card = "T3-gated" if i < 26 else ("T2-gated" if i < 28 else "E3")
                con.execute("INSERT INTO decisions (decision_id, unit_id, unit_release, hub_entry, route, reason_code, level, card_id, writer_kind, revision, trace, created) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"d{i}", f"ac-{i}", rel, "PR.AA-01", "REVIEW", "x", None, card, "card", "r2", "[]", "now"))
                con.execute("INSERT INTO proposals (output_id, unit_release, extraction_output_id, hub_entry, hub_revision, identity, level, quotes, confidence, prompt_version, created) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"p{i}", rel, "e", "PR.AA-01", 1, "q", "equal", "[]", 0.9, "propose-v1", "now"))
                for v in ("a", "g", "m"):
                    con.execute("INSERT INTO votes (output_id, proposal_output_id, voter, evidence_identity, quoted_spans, vote_params, identity, vote, prompt_version, revision, created) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"v{i}{v}", f"p{i}", v, "s", "[]", json.dumps({"c_min": 0.7}), "q", "equal", "vote-v1", "r2", "now"))
        gold_doc = {"primary_gold": [{"unit_release": f"ac-{i}@r", "candidate_id": "PR.AA-01", "label": "equal" if i < 26 else "subset_of"} for i in range(30)],
                    "missed_link_draw": {"weight": 19.6, "n": 4, "rows": [{"label": "equal"}, {"label": "no_relationship"}] * 2},
                    "double_ruled": {"krippendorff_alpha": 0.8, "n": 6}, "anchoring": {"difference": 0.1, "rows": []}, "packets": 30}
        here = score.HERE
        try:
            score.HERE = tmp
            json.dump(cfg, open(os.path.join(tmp, "config.frozen.json"), "w"))
            json.dump(gold_doc, open(os.path.join(tmp, "gold.evaluation.json"), "w"))
            out, name = score.score("evaluation", "r2", db_path)
        finally:
            score.HERE = here
        rows = {r["level"]: r for r in out["rows"]}
        self.assertEqual(name, "score.json")
        self.assertEqual((rows["equal"]["n"], rows["equal"]["correct"]), (26, 26))
        self.assertEqual(rows["equal"]["gate_decision"], "open")
        self.assertEqual(rows["equal"]["families_with_multiple_outcomes"], 13)
        self.assertEqual((rows["no_relationship"]["n"], rows["no_relationship"]["correct"], rows["no_relationship"]["gate_decision"]), (2, 0, "closed_insufficient"))
        self.assertEqual(rows["subset_of"]["n"], 0)
        self.assertEqual(out["predictions"], {"pairs": 30, "with_prediction": 28, "with_label": 30})
        self.assertEqual(out["coverage"]["with_a_true_link"], 30)
        self.assertEqual(out["missed_links"]["retrieved_voted_no_relationship_true_links"], 2)
        self.assertAlmostEqual(out["missed_links"]["estimate"], (2 + 19.6 * 2) / (30 + 19.6 * 2), places=5)
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
