"""Item 8: the review queue as the store holds it.

    python3 -I queue.py [--store PATH]
        queue.json: every live decision routed REVIEW under the published set (sets.active), its size and age by split,
        read from runners/sp800-53-to-csf2/run.sqlite (decisions, units, sets); nothing is asked of a model. Exit 0.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def queue(db_path=STORE, at=None):
    at = at or datetime.now(timezone.utc)
    con = sqlite3.connect(db_path)
    active = [r[0] for r in con.execute("SELECT revision FROM sets WHERE active=1")]
    rows = con.execute("SELECT d.decision_id, d.unit_id, d.hub_entry, d.reason_code, d.created, u.split, d.revision FROM decisions d "
                       "JOIN units u ON u.unit_release=d.unit_release JOIN sets s ON s.revision=d.revision "
                       "WHERE s.active=1 AND d.route='REVIEW' AND d.superseded_by IS NULL ORDER BY d.created, d.decision_id").fetchall()
    con.close()
    by_split = {}
    for decision_id, unit_id, hub_entry, reason, created, split, rev in rows:
        age = (at - parse(created)).total_seconds() / 3600
        s = by_split.setdefault(split, {"size": 0, "ages_hours": [], "reasons": {}})
        s["size"] += 1
        s["ages_hours"].append(age)
        s["reasons"][reason] = s["reasons"].get(reason, 0) + 1
    out = {"store": os.path.basename(db_path), "published_revisions": active, "read_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "size": len(rows), "by_split": {}}
    for split, s in sorted(by_split.items()):
        ages = s["ages_hours"]
        out["by_split"][split] = {"size": s["size"], "age_hours": {"min": round(min(ages), 3), "max": round(max(ages), 3), "mean": round(sum(ages) / len(ages), 3)},
                                  "reasons": dict(sorted(s["reasons"].items()))}
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=STORE)
    args = ap.parse_args(argv)
    out = queue(args.store)
    with open(os.path.join(HERE, "queue.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"queue: {out['size']} under {out['published_revisions']}; " + "; ".join(f"{k} {v['size']} (age {v['age_hours']['min']}..{v['age_hours']['max']} h)" for k, v in out["by_split"].items())
          + " -> queue.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
