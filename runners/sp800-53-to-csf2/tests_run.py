"""Item 6: calibration touched the calibration split only, and froze what it chose.

    python3 -I -m unittest discover -s . -p tests_run.py
"""
import hashlib
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def rows(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


class Calibration(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "protocol.json"), encoding="utf-8") as f:
            self.protocol = json.load(f)
        self.split = {u["id"]: u["split"] for u in self.protocol["sample"]["units"]}
        self.cal = rows("calibration.jsonl")
        with open(os.path.join(HERE, "config.frozen.json"), encoding="utf-8") as f:
            self.config = json.load(f)

    def test_no_evaluation_or_sealed_test_unit_in_calibration(self):
        self.assertTrue(self.cal)
        for r in self.cal:
            with self.subTest(revision=r["revision"]):
                self.assertEqual(r["split"], "calibration")
                self.assertEqual(sorted({self.split[u] for u in r["unit_ids"]}), ["calibration"])
                self.assertEqual(len(r["unit_ids"]), self.protocol["split"]["units_by_split"]["calibration"])

    def test_revisions_within_the_cap_and_each_carries_its_cost(self):
        self.assertLessEqual(len(self.cal), self.protocol["calibration"]["revisions_cap"])
        self.assertEqual([r["revision"] for r in self.cal], [f"cal-{i}" for i in range(1, len(self.cal) + 1)])
        for r in self.cal:
            self.assertRegex(r["cost_usd"], r"^[0-9]+(\.[0-9]+)?$")
            self.assertEqual(r["engine"], self.cal[0]["engine"])

    def test_frozen_config_is_a_calibrated_revision_with_its_hash(self):
        chosen = next(r for r in self.cal if r["revision"] == self.config["chosen_revision"])
        self.assertEqual(self.config["c_min"], chosen["c_min"])
        self.assertEqual(self.config["prompt_versions"]["proposal"], chosen["prompt_version"])
        self.assertEqual(self.config["protocol_sha256"], open(os.path.join(HERE, "protocol.sha256")).read().split()[0])
        body = {k: v for k, v in self.config.items() if k != "sha256"}
        self.assertEqual(self.config["sha256"], hashlib.sha256(json.dumps(body, indent=1, sort_keys=True).encode()).hexdigest())

    def test_calibration_decisions_in_the_store_are_calibration_units_only(self):
        import sqlite3
        con = sqlite3.connect(os.path.join(HERE, "run.sqlite"))
        units = {u for (u,) in con.execute("SELECT DISTINCT unit_id FROM decisions WHERE revision LIKE 'cal-%'")}
        con.close()
        self.assertTrue(units)
        self.assertEqual({self.split[u] for u in units}, {"calibration"})


    def test_calibrate_refuses_to_run_over_a_frozen_calibration(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "-I", "run.py", "calibrate", "--units", "1", "--revisions", "1"], cwd=HERE,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("already frozen", r.stderr)
        self.assertEqual(len(rows("calibration.jsonl")), len(self.cal), "nothing appended")


if __name__ == "__main__":
    unittest.main()
