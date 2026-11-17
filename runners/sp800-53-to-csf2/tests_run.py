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
        with open(os.path.join(HERE, "protocol.sha256"), encoding="utf-8") as f:
            current = f.read().split()[0]
        if self.config["protocol_sha256"] != current:
            # calibration ran under the protocol this one supersedes; nothing calibration reads may have changed
            sup = self.protocol.get("supersedes") or {}
            self.assertEqual(self.config["protocol_sha256"], sup.get("sha256"))
            read_by_calibration = {"seed", "sources", "sample", "split", "rubric", "calibration", "levels", "null",
                                   "roles.extraction", "roles.embedding", "roles.proposal", "roles.vote"}
            self.assertFalse(set(sup.get("changed", [])) & read_by_calibration, sup)
        body = {k: v for k, v in self.config.items() if k != "sha256"}
        self.assertEqual(self.config["sha256"], hashlib.sha256(json.dumps(body, indent=1, sort_keys=True).encode()).hexdigest())

    def test_no_unit_holds_a_terminal_row_beside_candidate_rows(self):
        import sqlite3
        con = sqlite3.connect(os.path.join(HERE, "run.sqlite"))
        both = con.execute("SELECT d.unit_id, d.revision FROM decisions d WHERE d.hub_entry IS NULL AND EXISTS (SELECT 1 FROM decisions o "
                           "WHERE o.unit_id=d.unit_id AND o.revision=d.revision AND o.hub_entry IS NOT NULL)").fetchall()
        con.close()
        self.assertEqual(both, [])

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


    def test_reference_semantics_is_the_pinned_source_with_the_fields_the_weak_labels_read(self):
        with open(os.path.join(HERE, "reference_semantics.json"), encoding="utf-8") as f:
            ref = json.load(f)
        with open(os.path.join(HERE, "sources.json"), encoding="utf-8") as f:
            sources = json.load(f)
        with open(os.path.join(HERE, "index.json"), encoding="utf-8") as f:
            hub = {r["id"] for r in json.load(f)["rows"]}
        self.assertEqual(set(ref["policy"]), {"five_class", "association", "absent"})
        control = [e for e in ref["entries"] if e.get("granularity") == "control"]
        self.assertEqual(len(control), sources["counts"]["references"]["workbook_control_level_links"])
        self.assertTrue(all(e["source"] in sources["files"] for e in control), "every entry names a pinned source file")
        for e in control:
            self.assertEqual(set(e) >= {"csf_element", "csf_element_status", "target", "target_oscal_id", "granularity", "tag"}, True)
            self.assertEqual(e["tag"], "association")
            self.assertRegex(e["target_oscal_id"], r"^[a-z]{2}-\d+(\.\d+)?$")
        active = [e for e in control if e["csf_element_status"] == "active" and e["csf_kind"] == "subcategory"]
        self.assertEqual(len(active), 1468)
        self.assertTrue(all(e["csf_element"] in hub for e in active), "every active subcategory reference names a hub entry in the index")
        self.assertEqual(len([e for e in control if e["csf_kind"] == "category"]), 12, "the category-level links the weak labels skip")
        self.assertEqual(sorted({e["source"] for e in ref["entries"]}), sorted(set(sources["files"]) - {"sp800-53r5_catalog.json"}))

    def test_an_interrupted_revision_keeps_its_baseline(self):
        import subprocess, sys, tempfile, shutil
        tmp = tempfile.mkdtemp()
        store = os.path.join(tmp, "store.sqlite"); shutil.copyfile(os.path.join(HERE, "run.sqlite"), store)
        cal, cfg, acc, ev = (os.path.join(tmp, n) for n in ("cal.jsonl", "cfg.json", "acc.jsonl", "ev.jsonl"))
        with open(acc, "w") as f:
            f.write(json.dumps({"charged_usd": "1.5", "provider": "openai", "stage": "prior"}) + "\n")
        with open(cal + ".progress", "w") as f:    # cal-1 was interrupted after 0.5 USD of its own calls were paid
            json.dump({"revision": "cal-1", "prompt_version": "propose-v1", "c_min": 0.7, "spend_before": "1.0",
                       "by_provider_before": {"openai": "1.0"}, "started": "x"}, f)
        env = {**os.environ, "GSTACK_STORE": store, "GSTACK_CALIBRATION": cal, "GSTACK_CONFIG": cfg, "GSTACK_ACCOUNTING": acc, "GSTACK_EVENTS": ev}
        r = subprocess.run([sys.executable, "-I", "run.py", "calibrate", "--units", "2", "--revisions", "1", "--workers", "1"], cwd=HERE,
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("resuming an interrupted revision", r.stdout)
        row = rows(os.path.relpath(cal, HERE))
        self.assertEqual(len(row), 1)
        self.assertGreaterEqual(float(row[0]["cost_usd"]), 0.5, "the interrupted calls' 0.5 USD is in the revision's cost")
        self.assertFalse(os.path.exists(cal + ".progress"))
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
