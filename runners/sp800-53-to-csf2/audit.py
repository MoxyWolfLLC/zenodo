#!/usr/bin/env python3
"""Item 7: the SS audit. The two single-source cards (SS-scope, SS-retrieval) record a negative on one source's
word; the council audits a draw of what they recorded.

    python3 -I audit.py [--split evaluation] [--workers W]
        per card, up to 30 outputs the card recorded in the split (all of them when fewer exist; the pool size is
        recorded), drawn by protocol.json selection_rules (draw name ss_audit), each ruled by the three council voices
        as correct or error (majority; a split is an error); the error rate per card with its n goes to
        audit.<split>.json, which score.py carries into score.json. Rulings are stored in council_rulings keyed by
        (packet id, voice, session) and reused on a rerun. Exit 0; 4 on a budget refusal.
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")
CARDS = {"SS-scope": "out of scope by content: the statement describes no required activity",
         "SS-retrieval": "no match: nothing was retrieved for the unit"}


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


providers, gold = load("providers"), load("gold")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Audit:
    def __init__(self, protocol, adapter, db_path=STORE):
        self.protocol, self.adapter = protocol, adapter
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.voices = protocol["roles"]["council"]["models"]
        self.lock = __import__("threading").Lock()
        self.tally = {"asked": 0, "reused": 0}

    def pool(self, split, rev, card):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT d.decision_id, d.unit_id, d.reason_code, u.statement, u.family FROM decisions d JOIN units u ON u.unit_release=d.unit_release "
                "WHERE u.split=? AND d.revision=? AND d.card_id=? AND d.superseded_by IS NULL ORDER BY d.decision_id", (split, rev, card))]

    def rule(self, row, card, split):
        packet = {"unit": {"id": row["unit_id"], "text": row["statement"]}, "context": {"family": row["family"]},
                  "record": {"card": card, "meaning": CARDS[card]}, "rubric": self.protocol["rubric"]}
        pid = gold.digest(packet)
        votes = []
        for m in self.voices:
            with self.lock:
                stored = self.db.execute("SELECT level, reason FROM council_rulings WHERE packet_id=? AND voice=? AND session=?",
                                         (pid, m, "audit")).fetchone()
            if stored and (stored["level"] != "invalid" or (stored["reason"] or "").endswith("#final")):
                with self.lock:
                    self.tally["reused"] += 1   # a ruling, or an invalid already asked twice
                votes.append({"model": m, "ruling": stored["level"]})
                continue
            ruling = "invalid"
            for attempt in range(1 if stored else 2):   # a reply that is not a ruling is asked again once; a stored invalid was the first try
                with self.lock:
                    self.tally["asked"] += 1
                prompt = providers.render("audit", rubric=packet["rubric"], unit_id=row["unit_id"], unit_text=row["statement"],
                                          family=row["family"], card=card, meaning=CARDS[card])
                q = providers.identity("council", packet=pid, voice=m, session="audit", prompt_version="audit-v1")
                out = self.adapter.call("council", m, "audit-v1", prompt, q, providers.identity("evidence", packet=pid),
                                        packet_sha256=pid, packet_class=f"ss_audit:{card}", split=split, session="audit")
                ruling = out.get("ruling") if isinstance(out, dict) else None
                ruling = ruling if ruling in ("correct", "error") else "invalid"
                reason = str((out.get("reason") if isinstance(out, dict) else None) or "")[:290]
                if ruling == "invalid" and attempt + (1 if stored else 0) >= 1:
                    reason += "#final"   # asked twice: a rerun reuses the unresolved vote
                with self.lock, self.db:
                    self.db.execute("INSERT OR REPLACE INTO council_rulings (packet_id, voice, packet_class, split, blind, level, reason, session, created) "
                                    "VALUES (?,?,?,?,?,?,?,?,?)", (pid, m, f"ss_audit:{card}", split, 1, ruling, reason, "audit", now()))
                if ruling != "invalid":
                    break
            votes.append({"model": m, "ruling": ruling})
        valid = [v["ruling"] for v in votes if v["ruling"] != "invalid"]
        if len(valid) < len(self.voices):
            verdict = "unresolved"
        else:
            verdict = "correct" if valid.count("correct") >= 2 else "error"
        return {"decision_id": row["decision_id"], "unit_id": row["unit_id"], "packet_sha256": pid, "ruling": verdict, "votes": votes}

    def run(self, split, rev, workers):
        out = {"split": split, "revision": rev, "per_card": self.protocol["ss_audit"]["per_card"], "cards": {}}
        for card in self.protocol["ss_audit"]["cards"]:
            pool = self.pool(split, rev, card)
            ids = gold.draw(self.protocol, "ss_audit", split, [r["decision_id"] for r in pool], self.protocol["ss_audit"]["per_card"])
            rows = {r["decision_id"]: r for r in pool}
            with ThreadPoolExecutor(max_workers=workers) as ex:
                ruled = list(ex.map(lambda i: self.rule(rows[i], card, split), ids))
            resolved = [r for r in ruled if r["ruling"] != "unresolved"]
            errors = sum(1 for r in resolved if r["ruling"] == "error")
            out["cards"][card] = {"pool": len(pool), "drawn": len(ruled), "n": len(resolved), "unresolved": len(ruled) - len(resolved),
                                  "errors": errors, "error_rate": (errors / len(resolved)) if resolved else None,
                                  "seed": gold.draw_seed(self.protocol, "ss_audit", split), "rows": ruled}
        out["tally"] = dict(self.tally)
        return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="evaluation")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    def body():
        protocol = gold.retrieve.protocol_checked()
        adapter = providers.Adapter(protocol)
        a = Audit(protocol, adapter)
        before, _ = adapter.spend()
        out = a.run(args.split, "r2", args.workers)
        after, _ = adapter.spend()
        out["cost_usd"] = providers.money(after - before)
        out["audited"] = now()
        path = os.path.join(HERE, f"audit.{args.split}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
        for card, c in out["cards"].items():
            print(f"audit {args.split} {card}: pool {c['pool']}, drawn {c['n']}, errors {c['errors']}, error rate {c['error_rate']}")
        print(f"asked {out['tally']['asked']} reused {out['tally']['reused']}; cost {out['cost_usd']} USD -> {os.path.basename(path)}")
    return providers.run_pass(body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
