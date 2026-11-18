"""Item 8: the sealed-test pass's rules, the queue, the policy formula and the undo drills, checked without a model.

    python3 -I tests_item8.py
"""
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store, queue, undo, score = load("store"), load("queue"), load("undo"), load("score")


def synthetic(tmp, n=12):
    """A store with r1 (inactive, empty) and r2 (active): n REVIEW decisions on test units, one WRITE on an evaluation unit."""
    db = os.path.join(tmp, "s.sqlite"); store.init(db)
    con = sqlite3.connect(db)
    with con:
        con.execute("INSERT INTO sets (revision, active, sha256, gates, created) VALUES ('r1', 0, 'a', '{}', '2026-11-17T00:00:00Z')")
        con.execute("INSERT INTO sets (revision, active, sha256, gates, created) VALUES ('r2', 1, 'b', '{}', '2026-11-17T00:00:00Z')")
        for i in range(n):
            rel, split = f"ac-{i}@r", "test" if i else "evaluation"
            con.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?,?)", (rel, f"ac-{i}", "r", f"ac-{i // 2}", "ac", "base", split, "t", 1, None))
            con.execute("INSERT INTO decisions (decision_id, unit_id, unit_release, hub_entry, route, reason_code, level, card_id, writer_kind, revision, trace, created) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"d{i}", f"ac-{i}", rel, "PR.AA-01", "WRITE" if i == 0 else "REVIEW", "x" if i % 2 else "y",
                                                             "equal" if i == 0 else None, "T3" if i == 0 else "T3-gated", "card", "r2", "[]", f"2026-11-17T0{i % 10}:00:00Z"))
    con.close()
    return db


class Queue(unittest.TestCase):
    def test_queue_counts_review_rows_under_the_published_set_with_ages(self):
        tmp = tempfile.mkdtemp()
        db = synthetic(tmp)
        out = queue.queue(db, at=datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc))
        self.assertEqual((out["size"], out["published_revisions"], sorted(out["by_split"])), (11, ["r2"], ["test"]))
        t = out["by_split"]["test"]
        self.assertEqual((t["size"], t["age_hours"]["min"], t["age_hours"]["max"], t["reasons"]), (11, 3.0, 12.0, {"x": 6, "y": 5}))
        con = sqlite3.connect(db)
        with con:
            con.execute("UPDATE sets SET active=0")
        self.assertEqual(queue.queue(db)["size"], 0, "nothing is queued when no set is published")
        shutil.rmtree(tmp)


class Policy(unittest.TestCase):
    def test_r3_formula(self):
        run = load("run")
        self.assertEqual(run.r3_threshold(0.7), 0.5)
        self.assertEqual(run.r3_threshold(0.1), 0.0)
        self.assertEqual(run.r3_threshold(0.0), 0.2)
        self.assertEqual(run.r3_threshold(1.0), 0.8)


class Undo(unittest.TestCase):
    def test_reversal_and_recovery_on_copies_leave_the_source_untouched(self):
        tmp = tempfile.mkdtemp()
        db = synthetic(tmp)
        before = open(db, "rb").read()
        rv = undo.reversal(db, tmp)
        self.assertTrue(rv["ok"], rv)
        self.assertEqual((rv["r1_read"]["rows"], rv["r2_read"]["rows"], rv["reads_equal"], rv["r2_differs"]), (0, 12, True, True))
        rc = undo.recovery(db, tmp)
        self.assertTrue(rc["ok"], rc)
        self.assertEqual((rc["footprint"], rc["untargeted_rows_changed"], rc["table_equal_after_restore"]), (["d0", "d1", "d10", "d11", "d2"], 0, True))
        self.assertEqual(open(db, "rb").read(), before, "the drills run on copies")
        con = sqlite3.connect(db)
        with con:
            con.execute("INSERT INTO sets (revision, active, sha256, gates, created) VALUES ('r3', 0, 'c', '{}', 'x')")
        self.assertFalse(undo.reversal(db, tmp)["ok"], "a copy taken after r3 is not the reversal's input")
        shutil.rmtree(tmp)


class TestScore(unittest.TestCase):
    def test_test_split_rows_are_automatic_writes_and_a_level_with_none_is_closed_insufficient(self):
        tmp = tempfile.mkdtemp()
        db = synthetic(tmp)
        con = sqlite3.connect(db)
        with con:
            for i in range(12):
                con.execute("INSERT INTO proposals (output_id, unit_release, extraction_output_id, hub_entry, hub_revision, identity, level, quotes, confidence, prompt_version, created) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"p{i}", f"ac-{i}@r", "e", "PR.AA-01", 1, "q", "equal", "[]", 0.9, "propose-v1", "now"))
        con.close()
        gold_doc = {"primary_gold": [{"unit_release": f"ac-{i}@r", "candidate_id": "PR.AA-01", "label": "equal"} for i in range(12)],
                    "missed_link_draw": {"weight": 19.6, "n": 0, "rows": []}, "double_ruled": {"krippendorff_alpha": None, "n": 0},
                    "anchoring": {"difference": None, "rows": []}, "packets": 12}
        here = score.HERE
        try:
            score.HERE = tmp
            json.dump({"c_min": 0.7, "sha256": "x", "prompt_versions": {"proposal": "propose-v1"}}, open(os.path.join(tmp, "config.frozen.json"), "w"))
            json.dump(gold_doc, open(os.path.join(tmp, "gold.test.json"), "w"))
            out, name = score.score("test", "r2", db)
        finally:
            score.HERE = here
        self.assertEqual(name, "score.test.json")
        self.assertEqual(out["rows_basis"], "automatic writes (route WRITE)")
        self.assertEqual({r["level"]: (r["n"], r["gate_decision"]) for r in out["rows"]},
                         {lv: (0, "closed_insufficient") for lv in ("subset_of", "intersects_with", "equal", "superset_of", "no_relationship")})
        self.assertEqual(out["automatic_writes"], 0, "the WRITE row sits on an evaluation unit, not the test split")
        self.assertEqual([r["n"] for r in out["prediction_rows"] if r["level"] == "equal"], [0], "T3-gated rows with no votes under c_min carry no plurality")
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
