#!/usr/bin/env python3
"""Item 7: score.json for a split, from the store, gold.<split>.json and audit.<split>.json.

    python3 -I score.py [--split evaluation] [--revision r2]

Per level and for no_relationship: n (predictions at that level with a council label), correct, the one-sided 95%
Wilson lower bound, the gate decision (open when n is at least 25 and the bound at least 0.90; closed_insufficient
when n is under 25; closed otherwise) and the count of families contributing more than one outcome at the row. A
prediction is the triad's plurality for a pair that reached the triad (T3 or T3-gated: the level; T2 or T2-gated:
no_relationship); a pair the evidence cards held back has no prediction and is not scored. Plus: coverage (the share
of the split's units with at least one proposal ruled a true link, read from the store and gold), the missed-link
Horvitz-Thompson estimate (retrieved pairs the run voted no_relationship at weight 1, the non-retrieved draw at the
protocol's weight), the SS-scope and SS-retrieval error rates with their n, the double-ruled alpha and the anchoring
statistic.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")
Z95 = 1.6448536269514722


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cards = load("cards")


def wilson_lower(correct, n):
    if n == 0:
        return 0.0
    p = correct / n
    return (p + Z95 * Z95 / (2 * n) - Z95 * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))) / (1 + Z95 * Z95 / n)


def predictions(db, split, rev, c_min):
    """(unit_release, unit_id, family, candidate) -> predicted answer or None, from the split's decisions and votes."""
    out = {}
    rows = db.execute("SELECT d.unit_release, d.unit_id, d.hub_entry, d.card_id, d.level, u.family FROM decisions d JOIN units u ON u.unit_release=d.unit_release "
                      "WHERE u.split=? AND d.revision=? AND d.superseded_by IS NULL AND d.hub_entry IS NOT NULL", (split, rev)).fetchall()
    for rel, uid, cand, card, level, family in rows:
        pred = None
        if card in ("T2", "T2-gated"):
            pred = cards.NULL
        elif card in ("T3", "T3-gated"):
            if level:
                pred = level
            else:
                p = db.execute("SELECT output_id FROM proposals WHERE unit_release=? AND hub_entry=? ORDER BY created DESC LIMIT 1", (rel, cand)).fetchone()
                votes = db.execute("SELECT voter, vote FROM votes WHERE proposal_output_id=? AND json_extract(vote_params,'$.c_min')=? "
                                   "ORDER BY created DESC", (p[0], c_min)).fetchall() if p else []
                seen, latest = set(), []
                for voter, vote in votes:
                    if voter not in seen:
                        seen.add(voter); latest.append({"answer": None if vote == cards.NULL else vote})
                pred = cards.plurality(latest, 3)
        out[(rel, uid, family, cand)] = pred
    return out


def score(split, rev, db_path=STORE):
    with open(os.path.join(HERE, "config.frozen.json"), encoding="utf-8") as f:
        config = json.load(f)
    with open(os.path.join(HERE, f"gold.{split}.json"), encoding="utf-8") as f:
        gold = json.load(f)
    audit_path = os.path.join(HERE, f"audit.{split}.json")
    audit = json.load(open(audit_path, encoding="utf-8")) if os.path.isfile(audit_path) else None
    db = sqlite3.connect(db_path)
    preds = predictions(db, split, rev, config["c_min"])
    label = {(r["unit_release"], r["candidate_id"]): r["label"] for r in gold["primary_gold"] if r["label"] is not None}
    unresolved = sum(1 for r in gold["primary_gold"] if r["label"] is None)
    rows = []
    for level in [*cards.LEVELS, cards.NULL]:
        at = [(k, p) for k, p in preds.items() if p == level and (k[0], k[3]) in label]
        n = len(at)
        correct = sum(1 for k, p in at if label[(k[0], k[3])] == level)
        lower = wilson_lower(correct, n)
        opens = n >= 25 and lower >= 0.90
        fam = {}
        for k, _ in at:
            fam[k[2]] = fam.get(k[2], 0) + 1
        rows.append({"level": level, "n": n, "correct": correct, "wilson_lower": round(lower, 6), "gate_open": opens,
                     "gate_decision": "closed_insufficient" if n < 25 else ("open" if opens else "closed"),
                     "families_with_multiple_outcomes": sum(1 for v in fam.values() if v > 1)})
    units = {r[0] for r in db.execute("SELECT unit_release FROM units WHERE split=?", (split,))}
    linked_units = {k[0] for k in label if label[k] in cards.LEVELS}
    coverage = {"units": len(units), "with_a_true_link": len(linked_units & units), "share": round(len(linked_units & units) / len(units), 6) if units else None,
                "definition": "share of the split's units with at least one proposal the council ruled a true link (a level)"}
    weight = gold["missed_link_draw"]["weight"]
    retrieved_true = sum(1 for k, lv in label.items() if lv in cards.LEVELS)
    missed_retrieved = sum(1 for k, p in preds.items() if p == cards.NULL and label.get((k[0], k[3])) in cards.LEVELS)
    draw_rows = gold["missed_link_draw"]["rows"]
    draw_resolved = [r for r in draw_rows if r["label"] is not None]
    draw_true = sum(1 for r in draw_resolved if r["label"] in cards.LEVELS)
    # an unresolved draw is a missing observation, not a zero: the protocol weight is scaled to the resolved draws
    # (the realized inclusion probability), and the counts are disclosed
    weight_applied = weight * len(draw_rows) / len(draw_resolved) if draw_resolved else None
    total = retrieved_true + (weight_applied or 0) * draw_true
    missed = {"retrieved_voted_no_relationship_true_links": missed_retrieved, "retrieved_true_links": retrieved_true,
              "retrieved_unresolved": unresolved,
              "non_retrieved_draw_true_links": draw_true, "non_retrieved_weight": weight, "non_retrieved_weight_applied": round(weight_applied, 6) if weight_applied else None,
              "draw_n": gold["missed_link_draw"]["n"], "draw_resolved": len(draw_resolved), "draw_unresolved": len(draw_rows) - len(draw_resolved),
              "estimate": round((missed_retrieved + weight_applied * draw_true) / total, 6) if total and weight_applied else None,
              "definition": "share of ruled true links the run recorded as no_relationship or never retrieved, over ruled true links (Horvitz-Thompson; "
                            "unresolved rulings are missing observations: left out of both strata, with the draw weight scaled to the resolved draws)"}
    ss = {card: {"pool": c["pool"], "n": c["n"], "unresolved": c.get("unresolved", 0), "errors": c["errors"], "error_rate": c["error_rate"]}
          for card, c in (audit or {}).get("cards", {}).items()}
    out = {"split": split, "revision": rev, "config_sha256": config["sha256"], "c_min": config["c_min"], "rows": rows,
           "predictions": {"pairs": len(preds), "with_prediction": sum(1 for p in preds.values() if p is not None),
                           "with_label": sum(1 for k in preds if (k[0], k[3]) in label), "unresolved_gold": unresolved},
           "coverage": coverage, "missed_links": missed, "ss_audit": ss or None,
           "council_agreement": {"krippendorff_alpha": gold["double_ruled"]["krippendorff_alpha"], "double_ruled_n": gold["double_ruled"]["n"]},
           "anchoring": {k: v for k, v in gold["anchoring"].items() if k != "rows"},
           "gold_packets": gold["packets"], "scored": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    raw = json.dumps(out, indent=1, sort_keys=True).encode()
    out["sha256"] = hashlib.sha256(raw).hexdigest()
    name = "score.json" if split == "evaluation" else f"score.{split}.json"
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
        f.write("\n")
    return out, name


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="evaluation")
    ap.add_argument("--revision", default="r2")
    args = ap.parse_args(argv)
    out, name = score(args.split, args.revision)
    print(f"{name}: {args.split} under {args.revision}; {out['predictions']['pairs']} pairs, {out['predictions']['with_prediction']} with a prediction")
    for r in out["rows"]:
        print(f"  {r['level']:<16} n {r['n']:>4} correct {r['correct']:>4} wilson {r['wilson_lower']:.3f} {r['gate_decision']} families>1 {r['families_with_multiple_outcomes']}")
    print(f"  coverage {out['coverage']['share']} ({out['coverage']['with_a_true_link']}/{out['coverage']['units']}); missed-link estimate {out['missed_links']['estimate']}; "
          f"alpha {out['council_agreement']['krippendorff_alpha']}; anchoring blind-minus-assisted {out['anchoring'].get('difference')}; ss_audit {out['ss_audit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
