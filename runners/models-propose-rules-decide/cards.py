"""Card sets as ordered data: (card_id, revision, condition, outcome).

Conditions are pure functions of stored data (ctx) and set parameters (p).
They never call a model. First match wins; the last card is always "otherwise".
"""
import hashlib
import inspect
import sys
from collections import Counter

LEVELS = ["informs", "supports", "augments", "replaces"]  # no match (null) < informs < ... < replaces


def valid(v):
    return isinstance(v, dict) and not v.get("refused") and "answer" in v and (v["answer"] is None or v["answer"] in LEVELS)


def plurality(votes):
    n = Counter(v["answer"] for v in votes if valid(v) and v["answer"] is not None)
    top = [lvl for lvl, c in n.items() if c >= 2]
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


TRIAD = [  # p["closed"]: outcomes whose acceptance gate (Section 9) is closed; they go to review instead
    ("T1", 1, lambda c, p: sum(map(valid, c["votes"])) < c["n"], ("REVIEW", "incomplete_triad")),
    ("T2", 1, lambda c, p: nulls(c["votes"]) >= 2 and "no_match" not in p["closed"], ("RECORD", "no_match_voted")),
    ("T2-gated", 1, lambda c, p: nulls(c["votes"]) >= 2, ("REVIEW", "nomatch_gate_closed")),
    ("T3", 1, lambda c, p: plurality(c["votes"]) not in (None, *p["closed"]), ("WRITE", "auto_accept")),
    ("T3-gated", 1, lambda c, p: plurality(c["votes"]) is not None, ("REVIEW", "level_gate_closed")),
    ("T4", 1, lambda c, p: sum(1 for v in c["votes"] if valid(v) and v["answer"] is not None) >= 2, ("REVIEW", "level_split")),
    ("T5-otherwise", 1, OTHERWISE, ("REVIEW", "no_majority")),
]
RULING = [  # reviewer rulings; a level comes only from the ruling itself
    ("H1", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "accept", ("RECORD", "human_accept")),
    ("H2", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "no_match", ("RECORD", "no_match_ruled")),
    ("H3", 1, lambda c, p: (c["ruling"] or {}).get("ruling") == "out_of_scope", ("RECORD", "out_of_scope_ruled")),
    ("H4-otherwise", 1, OTHERWISE, ("PENDING", "awaiting_ruling")),
]

BASE = dict(G_max=2, H_max=2, c_min=0.7, a_scope=0.2, k=3, out_of_scope_types=["heading"], closed=[])
ENGINE_PARTS = ("valid", "plurality", "nulls", "quotes_ok", "sampled")


def engine_hash():
    """Hash of the shared helpers every card calls. A set revision pins it; replay refuses a mismatch."""
    mod = sys.modules[__name__]
    return hashlib.sha256("".join(inspect.getsource(getattr(mod, f)) for f in ENGINE_PARTS).encode()).hexdigest()[:16]
QV = dict(extract="xq1", propose="pq1", triad="tq1")


def make(rev, bump=(), qv=None, **params):
    sets = dict(intake=INTAKE, audit=AUDIT, retrieval=RETRIEVAL, evidence=EVIDENCE,
                curator=CURATOR, triad=TRIAD, ruling=RULING)
    cards = {k: [(cid, r + (cid in bump), f, o) for cid, r, f, o in v] for k, v in sets.items()}
    s = dict(rev=rev, params={**BASE, **params}, qv={**QV, **(qv or {})}, cards=cards,
             proposer="prov-A/m1", triad=["prov-B/m1", "prov-C/m1", "prov-D/m1"], retriever="kw-v1",
             engine=engine_hash())
    assert s["proposer"].split("/")[0] not in [m.split("/")[0] for m in s["triad"]], "proposer sits on triad"
    return s


SETS = {
    "r1": make("r1"),
    "r2": make("r2", bump=("E3",), c_min=0.5),          # change (a): deterministic loosening
    "r3": make("r3", qv=dict(triad="tq2")),             # change (b): reworded triad question
    "r4": make("r4", closed=["no_match", "augments"]),   # change (c): two acceptance gates closed
}
