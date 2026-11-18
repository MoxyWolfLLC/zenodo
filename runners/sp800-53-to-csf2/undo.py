"""Item 8: the two undo drills, each on a copy of the store, never on run.sqlite itself.

    python3 -I undo.py [--store PATH]
        publication reversal: on a copy of the store taken after r2 and before r3 (the sets table holds r1 inactive and
        r2 active, no r3), the public read under r1 is taken, r2 is confirmed active, r1 is reactivated, and the public
        read under r1 is taken again: the two complete reads must be equal, and the r2 read must differ from them.
        data recovery: on a second copy, a bad write corrupts exactly five decision rows; its footprint (the rows that
        differ from the pre-write copy) must be exactly those five; the five are restored from the pre-write copy and
        every other row must be unchanged. undo_report.json carries both. Exit 0 when both hold, 1 otherwise.

The public read is every decision under the active set that is not superseded, with the live outputs it names,
ordered by decision id; a complete read is the whole of that, not a sample.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")
TARGET_ROWS = 5


def public_read(con):
    rows = con.execute("SELECT d.decision_id, d.unit_id, d.hub_entry, d.route, d.reason_code, d.level, d.card_id, d.writer_kind, d.revision "
                       "FROM decisions d JOIN sets s ON s.revision=d.revision WHERE s.active=1 AND d.superseded_by IS NULL ORDER BY d.decision_id").fetchall()
    return [list(r) for r in rows]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def activate(con, rev):
    with con:
        con.execute("UPDATE sets SET active=0")
        con.execute("UPDATE sets SET active=1 WHERE revision=?", (rev,))


def reversal(src, tmp):
    path = os.path.join(tmp, "reversal.sqlite")
    shutil.copyfile(src, path)
    con = sqlite3.connect(path)
    sets = {r[0]: r[1] for r in con.execute("SELECT revision, active FROM sets")}
    if "r3" in sets or sets.get("r2") != 1 or "r1" not in sets:
        return {"ok": False, "reason": f"the copy must hold r1 and r2 active and no r3: {sets}"}
    r2_read = public_read(con)
    activate(con, "r1")
    r1_read = public_read(con)
    activate(con, "r2")                  # publish r2 (the state the copy was taken in)
    assert public_read(con) == r2_read
    activate(con, "r1")                  # the reversal
    r1_again = public_read(con)
    con.close()
    return {"ok": r1_read == r1_again and r1_read != r2_read, "sets_before": sets,
            "r1_read": {"rows": len(r1_read), "sha256": digest(r1_read)}, "r2_read": {"rows": len(r2_read), "sha256": digest(r2_read)},
            "r1_read_after_reactivation": {"rows": len(r1_again), "sha256": digest(r1_again)},
            "reads_equal": r1_read == r1_again, "r2_differs": r1_read != r2_read,
            "note": "r1 carries no decisions in this run (calibration ran under cal-1..cal-6, the evaluation and test splits under r2): the complete read under r1 is empty before and after"}


def all_rows(con):
    return {r[0]: list(r) for r in con.execute("SELECT * FROM decisions ORDER BY decision_id")}


def recovery(src, tmp):
    before_path, after_path = os.path.join(tmp, "before.sqlite"), os.path.join(tmp, "after.sqlite")
    shutil.copyfile(src, before_path)
    shutil.copyfile(src, after_path)
    before = sqlite3.connect(before_path)
    con = sqlite3.connect(after_path)
    targets = [r[0] for r in con.execute("SELECT decision_id FROM decisions WHERE revision='r2' ORDER BY decision_id LIMIT ?", (TARGET_ROWS,))]
    if len(targets) != TARGET_ROWS:
        return {"ok": False, "reason": f"fewer than {TARGET_ROWS} r2 decisions"}
    with con:                                                   # the bad write: five rows, level and route overwritten
        con.executemany("UPDATE decisions SET level='CORRUPT', route='CORRUPT' WHERE decision_id=?", [(t,) for t in targets])
    pre, post = all_rows(before), all_rows(con)
    footprint = sorted(i for i in post if post[i] != pre.get(i)) + sorted(i for i in pre if i not in post)
    cols = [d[1] for d in before.execute("PRAGMA table_info(decisions)")]
    with con:                                                   # the restore: exactly the footprint, from the pre-write copy
        for i in footprint:
            con.execute(f"INSERT OR REPLACE INTO decisions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", pre[i])
    restored = all_rows(con)
    changed_untargeted = sorted(i for i in restored if i not in targets and restored[i] != pre.get(i))
    con.close(); before.close()
    return {"ok": footprint == sorted(targets) and restored == pre and not changed_untargeted,
            "targets": targets, "footprint": footprint, "footprint_is_exactly_the_targets": footprint == sorted(targets),
            "restored_rows": len(footprint), "untargeted_rows_changed": len(changed_untargeted), "table_equal_after_restore": restored == pre,
            "rows": len(pre)}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=STORE)
    args = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="undo-")
    report = {"store": os.path.basename(args.store), "publication_reversal": reversal(args.store, tmp), "data_recovery": recovery(args.store, tmp)}
    report["ok"] = bool(report["publication_reversal"].get("ok") and report["data_recovery"].get("ok"))
    with open(os.path.join(HERE, "undo_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")
    shutil.rmtree(tmp, ignore_errors=True)
    pr, dr = report["publication_reversal"], report["data_recovery"]
    print(f"undo: reversal reads_equal {pr.get('reads_equal')} r2_differs {pr.get('r2_differs')}; recovery footprint {len(dr.get('footprint', []))} rows, "
          f"untargeted changed {dr.get('untargeted_rows_changed')}, table equal {dr.get('table_equal_after_restore')} -> undo_report.json")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
