"""Item 9: graph conformance against the paper's Appendix C.

    python3 -I conform.py
        checks baseline/appendix_c.md against the hash pinned in baseline/appendix_c.sha256 (exit 6 on a mismatch), takes
        the run graph and the policy graph out of it (C.1 and C.2, the two text blocks), reads graphs/run.mmd and
        graphs/policy.mmd back to the paper's nouns with the normalizations listed in baseline/normalizations.json, projects
        both sides into six dimensions (nodes, edges, card ids, lanes, stores, decision semantics; fixture_ops.project) and
        prints zero deviations or one line per deviation; names E1 as present and unexercised from run.sqlite; writes
        conformance.json. Exit 0 either way.
"""
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fixture_ops = load("fixture_ops")


def baseline_checked():
    """Appendix C as pinned; exit 6 when the file's hash is not the pinned one."""
    path = os.path.join(HERE, "baseline", "appendix_c.md")
    with open(path, "rb") as f:
        raw = f.read()
    with open(os.path.join(HERE, "baseline", "appendix_c.sha256"), encoding="utf-8") as f:
        pinned = f.readline().split()[0]
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pinned:
        print(f"baseline/appendix_c.md sha256 {digest} does not match the pinned {pinned}", file=sys.stderr)
        sys.exit(6)
    blocks = re.findall(r"```text\n(.*?)```", raw.decode("utf-8"), re.S)
    if len(blocks) != 2:
        print(f"baseline/appendix_c.md holds {len(blocks)} graph blocks, not 2", file=sys.stderr)
        sys.exit(6)
    return {"run": blocks[0], "policy": blocks[1]}, digest


def normalizations_checked():
    """The pairs beside the pin must be the ones the projection applies; a label may change by these and by nothing else."""
    with open(os.path.join(HERE, "baseline", "normalizations.json"), encoding="utf-8") as f:
        listed = json.load(f)
    pairs = [(e["domain"], e["paper"]) for e in listed["domain_to_paper"]]
    if pairs != list(fixture_ops.ALIASES):
        print("baseline/normalizations.json differs from the normalizations the projection applies", file=sys.stderr)
        sys.exit(6)
    return listed


def main(argv):
    baseline, digest = baseline_checked()
    norms = normalizations_checked()
    ours = {}
    for name in baseline:
        with open(os.path.join(HERE, "graphs", f"{name}.mmd"), encoding="utf-8") as f:
            ours[name] = f.read()
    report_diff = {name: fixture_ops.diff(fixture_ops.project(baseline[name]), fixture_ops.project(ours[name])) for name in baseline}
    deviations = [(name, dim, row) for name, dims in report_diff.items() for dim, rows in dims.items() for row in rows]
    exercised = False
    if os.path.isfile(STORE):
        con = sqlite3.connect(STORE)
        exercised = bool(con.execute("SELECT 1 FROM decisions WHERE card_id='E1' LIMIT 1").fetchone())
        con.close()
    report = {"baseline": {"file": "baseline/appendix_c.md", "sha256": digest, "commit": fixture_ops.APPENDIX_COMMIT, "source": fixture_ops.APPENDIX_SOURCE},
              "normalizations": norms["domain_to_paper"], "graphs": {n: f"graphs/{n}.mmd" for n in baseline},
              "dimensions": ["nodes", "edges", "card_ids", "lanes", "stores", "decision_semantics"],
              "diff": report_diff, "deviations": len(deviations), "E1": {"present": True, "exercised": exercised}}
    with open(os.path.join(HERE, "conformance.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")
    if deviations:
        for name, dim, row in deviations:
            print(f"deviation: {name} graph, {dim}: {json.dumps(row, sort_keys=True)}")
    else:
        print("zero deviations from Appendix C in nodes, edges, card_ids, lanes, stores and decision_semantics for the run graph and the policy graph")
    print(f"E1 present, {'exercised' if exercised else 'unexercised'} (route is retrieval only: H_max = 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
