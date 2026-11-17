#!/usr/bin/env python3
"""Item 5: card sets as ordered data, (card_id, revision, condition, outcome), copied unchanged from
runner 1.1.0 (runners/models-propose-rules-decide/cards.py). The domain enters only through LEVELS, BASE
and make(): the card lists, their ids, revisions, conditions and outcomes are the paper's.

Conditions are pure functions of stored data (ctx) and set parameters (p). They never call a model.
First match wins; the last card is always "otherwise".

Domain parameters (set_r1.json names all seven):
  unit        an active SP 800-53 control or enhancement (withdrawn out by metadata: I1)
  hub         the active CSF 2.0 subcategories sources.json counts (106 expected), hub revision 1
  scale       subset_of, intersects_with, equal, superset_of, plus the null (no_relationship)
  scope       withdrawn controls out by metadata; a control with no statement text out by content (SS-scope)
  route       retrieval only: H_max = 0, so E1 (hub queue) is present but can never fire
  questions   versioned ids per stage (extract-v1, propose-v1, vote-v1)
  evidence    quotes verbatim in the control text; a level and quotes that disagree is a conflict (E3)

    python3 -I cards.py set r1      write set_r1.json: every gate closed, the engine hash, the seven parameters
"""
import hashlib
import inspect
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = ["subset_of", "intersects_with", "equal", "superset_of"]   # null (None) is no_relationship
NULL = "no_relationship"


def valid(v):
    return isinstance(v, dict) and not v.get("refused") and "answer" in v and (v["answer"] is None or v["answer"] in LEVELS)


def quorum(n):
    """Strict majority of the panel size n: 2 of 3, 3 of 4, 3 of 5 (Section 4)."""
    return n // 2 + 1


def plurality(votes, n):
    """The one level a strict majority of n agrees on, else None. A tie is never broken."""
    c = Counter(v["answer"] for v in votes if valid(v) and v["answer"] is not None)
    top = [lvl for lvl, k in c.items() if k >= quorum(n)]
    return top[0] if len(top) == 1 else None


def quotes_ok(prop, unit):
    return all(q in unit["text"] for q in prop.get("quotes", []))


def sampled(unit_id, rate):
    return int(hashlib.sha256(unit_id.encode()).hexdigest()[:8], 16) / 2**32 < rate


OTHERWISE = lambda c, p: True

INTAKE = [
    ("I1", 1, lambda c, p: c["unit"]["type"] in p["out_of_scope_types"], ("RECORD", "out_of_scope_rule")),
    ("I3", 1, lambda c, p: not c["ext"].get("complete") and c["gap"] < p["G_max"], ("RETRY", "gap_retry")),
    ("I4", 1, lambda c, p: not c["ext"].get("complete") and c["gap"] >= p["G_max"], ("REVIEW", "gap_exhausted")),
    ("SS-scope", 1, lambda c, p: c["ext"].get("work_object") is False, ("RECORD", "out_of_scope_content")),
    ("I5-otherwise", 1, OTHERWISE, ("NEXT", "retrieve")),
]
AUDIT = [  # applied to SS-scope records only
    ("A1", 1, lambda c, p: sampled(c["unit_id"], p["a_scope"]), ("AUDIT", "audit_scope")),
    ("A2-otherwise", 1, OTHERWISE, ("STAND", "unsampled")),
]
RETRIEVAL = [
    ("SS-retrieval", 1, lambda c, p: not c["cands"], ("RECORD", "no_match_nothing_retrieved")),
    ("R2-otherwise", 1, OTHERWISE, ("NEXT", "propose")),
]
EVIDENCE = [
    ("E1", 1, lambda c, p: c["prop"].get("needs_hub") and c["hub_att"] < p["H_max"], ("HUB", "hub_queue")),
    ("E2", 1, lambda c, p: c["prop"].get("needs_hub") and c["hub_att"] >= p["H_max"], ("REVIEW", "hub_loop_exhausted")),
    ("E3", 1, lambda c, p: (not quotes_ok(c["prop"], c["unit"]) or c["prop"].get("confidence", 0) < p["c_min"]
                            or c["prop"].get("conflict")), ("REVIEW", "evidence_unclear")),
    ("E4-otherwise", 1, OTHERWISE, ("NEXT", "triad")),
]
CURATOR = [
    ("K1", 1, lambda c, p: (c["cur"] or {}).get("decision") == "approve", ("REOPEN", "hub_approved")),
    ("K2", 1, lambda c, p: (c["cur"] or {}).get("decision") == "decline", ("RECORD", "no_match_hub_declined")),
    ("K3-otherwise", 1, OTHERWISE, ("HUB", "hub_pending")),
]
def nulls(votes):
    return sum(1 for v in votes if valid(v) and v["answer"] is None)


def nonnulls(votes):
    return sum(1 for v in votes if valid(v) and v["answer"] is not None)


TRIAD = [  # p["closed"]: outcomes whose acceptance gate (Section 9) is closed; they go to review instead
    ("T1", 1, lambda c, p: sum(map(valid, c["votes"])) < c["n"], ("REVIEW", "incomplete_triad")),
    ("T2", 1, lambda c, p: nulls(c["votes"]) >= quorum(c["n"]) and "no_match" not in p["closed"], ("RECORD", "no_match_voted")),
    ("T2-gated", 1, lambda c, p: nulls(c["votes"]) >= quorum(c["n"]), ("REVIEW", "nomatch_gate_closed")),
    ("T3", 1, lambda c, p: plurality(c["votes"], c["n"]) not in (None, *p["closed"]), ("WRITE", "auto_accept")),
    ("T3-gated", 1, lambda c, p: plurality(c["votes"], c["n"]) is not None, ("REVIEW", "level_gate_closed")),
    ("T4", 1, lambda c, p: nonnulls(c["votes"]) >= quorum(c["n"]), ("REVIEW", "level_split")),
    ("T5-otherwise", 1, OTHERWISE, ("REVIEW", "no_majority")),  # reachable only on an even panel: a tie
]
RULING = [  # reviewer rulings; a level comes only from the ruling itself
    ("H1", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "accept", ("RECORD", "human_accept")),
    ("H2", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "no_match", ("RECORD", "no_match_ruled")),
    ("H3", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "out_of_scope", ("RECORD", "out_of_scope_ruled")),
    ("H4-otherwise", 1, OTHERWISE, ("PENDING", "awaiting_ruling")),
]

SETS_IN_ORDER = dict(intake=INTAKE, audit=AUDIT, retrieval=RETRIEVAL, evidence=EVIDENCE,
                     curator=CURATOR, triad=TRIAD, ruling=RULING)
GATES = ["no_match", *LEVELS]
# ponytail: H_max = 0 is the "retrieval only" route; a_scope 0.2 is the paper's audit rate
BASE = dict(G_max=2, H_max=0, c_min=0.7, a_scope=0.2, k=8, out_of_scope_types=["withdrawn"], closed=list(GATES))
ENGINE_PARTS = ("valid", "quorum", "plurality", "nulls", "nonnulls", "quotes_ok", "sampled")
QV = dict(extract="extract-v1", propose="propose-v1", triad="vote-v1")


def engine_hash():
    """Hash of the shared helpers every card calls. A set revision pins it; replay refuses a mismatch."""
    return hashlib.sha256("".join(inspect.getsource(globals()[f]) for f in ENGINE_PARTS).encode()).hexdigest()[:16]


def gate_name(answer):
    """The gate an answer opens or closes: the null is the no_match gate."""
    return "no_match" if answer in (None, NULL) else answer


def make(rev, proposer, triad, retriever="embed-cosine-v1", bump=(), qv=None, **params):
    cards = {k: [(cid, r + (cid in bump), f, o) for cid, r, f, o in v] for k, v in SETS_IN_ORDER.items()}
    p = {**BASE, **params}
    p["closed"] = sorted({gate_name(g) for g in p["closed"]})
    s = dict(rev=rev, params=p, qv={**QV, **(qv or {})}, cards=cards, proposer=proposer, triad=list(triad),
             retriever=retriever, engine=engine_hash())
    assert s["proposer"].split("/")[0] not in [m.split("/")[0] for m in s["triad"]], "proposer sits on triad"
    return s


def card_ids():
    return {k: [c[0] for c in v] for k, v in SETS_IN_ORDER.items()}


def set_record(rev="r1"):
    """set_r1.json: every gate closed, the engine hash, the seven domain parameters."""
    with open(os.path.join(HERE, "protocol.json"), encoding="utf-8") as f:
        protocol = json.load(f)
    roles = protocol["roles"]
    return {
        "revision": rev,
        "gates": {g: "closed" for g in GATES},
        "engine": engine_hash(),
        "card_ids": card_ids(),
        "params": {k: v for k, v in BASE.items()},
        "proposer": roles["proposal"]["model"],
        "triad": roles["vote"]["models"],
        "retriever": "embed-cosine-v1",
        "domain": {
            "unit": "an active SP 800-53 Rev. 5 control or enhancement; withdrawn units out by metadata (I1)",
            "hub": {"source": "sources.json csf2.active_subcategories", "expected": 106, "hub_revision": 1},
            "scale": {"levels": LEVELS, "null": NULL, "version": protocol.get("answer_scale_version", "ir8477-set-theory-1")},
            "scope": {"metadata": "withdrawn out (I1)", "content": "no statement text out under SS-scope"},
            "route": {"kind": "retrieval only", "H_max": 0, "k": BASE["k"]},
            "questions": QV,
            "evidence": {"quotes": "verbatim spans of the control text", "conflict": "a level and quotes that disagree"},
        },
    }


if __name__ == "__main__":
    if sys.argv[1:3] == ["set", "r1"]:
        rec = set_record("r1")
        path = os.path.join(HERE, "set_r1.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1, sort_keys=True)
            f.write("\n")
        print(f"{os.path.basename(path)}: gates closed {len(rec['gates'])}, engine {rec['engine']}")
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
