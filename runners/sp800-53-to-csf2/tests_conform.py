"""Item 9: conform.py's contract, checked on copies.

    python3 -I tests_conform.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["conform.py", "fixture_ops.py", "cards.py", "engine.py", "providers.py", "store.py", "retrieve.py"]
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def copy_runner(tmp):
    for f in FILES:
        shutil.copyfile(os.path.join(HERE, f), os.path.join(tmp, f))
    for d in ("baseline", "graphs"):
        shutil.copytree(os.path.join(HERE, d), os.path.join(tmp, d))


def run(tmp):
    return subprocess.run([sys.executable, "-I", os.path.join(tmp, "conform.py")], capture_output=True, text=True, cwd=tmp,
                          env={**os.environ, "GSTACK_STORE": os.path.join(tmp, "none.sqlite")})


class Scenarios(unittest.TestCase):
    def test_check_table_rows_match_goal_md_scenarios_one_to_one(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("check", os.path.join(HERE, "check.py"))
        check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
        listed = check.goal_scenarios(ROOT)
        self.assertEqual(len(listed), 11, listed)
        self.assertEqual(len(check.SCENARIOS), len(listed))
        # each table row keeps a phrase of its GOAL.md bullet, in order
        for (row, _), bullet in zip(check.SCENARIOS, listed):
            key = {"never": "must never happen"}.get(row.split(":")[0], None)
            self.assertTrue(key in bullet if key else True, (row, bullet))
        self.assertEqual([("never" in r) for r, _ in check.SCENARIOS], [("must never happen" in b) for b in listed])
        self.assertEqual(check.goal_scenarios(tempfile.mkdtemp()), [])


class Conform(unittest.TestCase):
    def test_zero_deviations_and_e1_named_on_the_shipped_graphs(self):
        tmp = tempfile.mkdtemp(); copy_runner(tmp)
        p = run(tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("zero deviations from Appendix C", p.stdout)
        self.assertIn("E1 present, unexercised", p.stdout)
        shutil.rmtree(tmp)

    def test_a_moved_baseline_exits_6(self):
        tmp = tempfile.mkdtemp(); copy_runner(tmp)
        with open(os.path.join(tmp, "baseline", "appendix_c.md"), "a", encoding="utf-8") as f:
            f.write("\n")
        p = run(tmp)
        self.assertEqual(p.returncode, 6, p.stdout)
        self.assertIn("does not match the pinned", p.stderr)
        shutil.rmtree(tmp)

    def test_a_changed_lane_and_a_dropped_edge_are_one_line_each(self):
        tmp = tempfile.mkdtemp(); copy_runner(tmp)
        path = os.path.join(tmp, "graphs", "run.mmd")
        s = open(path, encoding="utf-8").read()
        lane = next(l for l in s.splitlines() if l.strip().startswith("subgraph"))
        s = s.replace(lane, lane.replace('"', '"Changed ', 1), 1)
        edge = next(l for l in s.splitlines() if "-->" in l)
        s = s.replace(edge + "\n", "", 1)
        open(path, "w", encoding="utf-8").write(s)
        p = run(tmp)
        self.assertEqual(p.returncode, 0)
        lines = [l for l in p.stdout.splitlines() if l.startswith("deviation:")]
        self.assertTrue(any("lanes" in l for l in lines) and any("edges" in l for l in lines), p.stdout)
        self.assertNotIn("zero deviations", p.stdout)
        shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
