#!/usr/bin/env python3
"""Item 5: the decision walk. One function runs a unit/candidate pair through the card sets in the
paper's order (intake, retrieval, evidence, triad, ruling) over stored outputs only; it never calls a
model. check.py --table, the fixture harness (gates, seed, replay, policy) and the live passes all use it,
so what the fixtures exercise is what the run decides with.

A case is stored data: unit, candidate, extraction, retrieval, proposal, votes, operational_ruling
(the shapes check.py's seed takes). A set is cards.make(...). The result is the decision row:
unit_id, candidate_id, set_revision, route, level, reason, card_id, writer_kind "card", and the trace
of every card examined in order with its hit.
"""
import hashlib
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cards = load("cards")


def pick(S, name, ctx, trace):
    for cid, rev, cond, out in S["cards"][name]:
        hit = bool(cond(ctx, S["params"]))
        trace.append([name, cid, rev, hit])
        if hit:
            return cid, out
    raise AssertionError(f"card set {name} has no otherwise")


def vote_answer(v):
    """A stored vote as the card engine reads it: the null is None, an off-scale answer is invalid."""
    if not isinstance(v, dict) or "answer" not in v:
        return {"refused": True}
    a = v["answer"]
    return {"answer": None if a == cards.NULL else a, "refused": bool(v.get("refused"))}   # a refused vote stays invalid


def panel_violations(votes, proposer_provider, panel=None):
    """Seats the triad never counts, as [(index, reason)]: a vote with no provider (missing_provider), a vote
    by the proposer's provider (proposer_seat), a provider not on the configured panel (not_seated, when a
    panel is given) and a second vote from a provider already seated (duplicate_seat). The check fails closed:
    identity is required before an answer is read. The triad operation refuses to store a violating vote;
    run_case treats a stored one as invalid, so the triad is incomplete (T1) rather than decided by it."""
    seen, out = set(), []
    for i, v in enumerate(votes):
        p = v.get("provider") if isinstance(v, dict) else None
        if not isinstance(p, str) or not p:
            out.append((i, "missing_provider"))
        elif p == proposer_provider:
            out.append((i, "proposer_seat"))
        elif panel is not None and p not in panel:
            out.append((i, "not_seated"))
        elif p in seen:
            out.append((i, "duplicate_seat"))
        seen.add(p)
    return out


def panel_of(S):
    return {m.split("/")[0] for m in S["triad"]}


def decision_id(unit_id, candidate_id, revision):
    return hashlib.sha256(json.dumps([unit_id, candidate_id, revision], sort_keys=True).encode()).hexdigest()[:16]


def row(case, S, route, reason, level, card, trace, candidate_id):
    uid = case["unit"]["id"]
    return {"decision_id": decision_id(uid, candidate_id, S["rev"]), "unit_id": uid, "candidate_id": candidate_id,
            "set_revision": S["rev"], "route": route, "level": level, "reason": reason, "card_id": card,
            "writer_kind": "card", "trace": trace}


def settle(case, S, d, trace):
    """A REVIEW outcome reads the stored operational ruling through the ruling cards; H1 carries its level."""
    if d["route"] != "REVIEW":
        return d
    r = case.get("operational_ruling")
    cid, (route, reason) = pick(S, "ruling", {"ruling": r}, trace)
    if route == "RECORD":
        return row(case, S, route, reason, r.get("level") if cid == "H1" else None, cid, trace, d["candidate_id"])
    return d


def run_case(case, S, n=None):
    """The decision for one stored case under set S. n is the panel size (default: the set's triad)."""
    u, ext = case["unit"], case.get("extraction") or {}
    n = n or len(S["triad"])
    trace = []
    gap = max(0, int(ext.get("attempt_number", 1)) - 1)
    cid, (route, reason) = pick(S, "intake", {"unit": u, "ext": ext, "gap": gap}, trace)
    if route != "NEXT":
        d = row(case, S, route, reason, None, cid, trace, None)
        if cid == "SS-scope":
            pick(S, "audit", {"unit_id": u["id"]}, trace)   # the audit draw is recorded on the row it acts on
        return settle(case, S, d, trace)
    cands = (case.get("retrieval") or {}).get("candidate_ids") or []
    cid, (route, reason) = pick(S, "retrieval", {"cands": cands}, trace)
    if route == "RECORD":
        return settle(case, S, row(case, S, route, reason, None, cid, trace, None), trace)
    cand = (case.get("candidate") or {}).get("id") or cands[0]
    prop = case.get("proposal") or {}
    cid, (route, reason) = pick(S, "evidence", {"prop": prop, "unit": u, "hub_att": 0}, trace)
    level = None
    if route == "HUB":                       # unreachable with H_max = 0; kept so the card set is the paper's
        cid, (route, reason) = pick(S, "curator", {"cur": None}, trace)
    elif route == "NEXT":
        raw = case.get("votes") or []
        bad = dict(panel_violations(raw, S["proposer"].split("/")[0], panel_of(S)))
        votes = [{"refused": True, "seat": bad[i]} if i in bad else vote_answer(v) for i, v in enumerate(raw)]
        cid, (route, reason) = pick(S, "triad", {"votes": votes, "n": n}, trace)
        level = cards.plurality(votes, n) if cid == "T3" else None
    return settle(case, S, row(case, S, route, reason, level, cid, trace, cand), trace)


def set_from_revision(rev, spec, proposer, triad):
    """A set from the harness's revision record (c_min, closed, question_versions) over the protocol's models."""
    qv = spec.get("question_versions") or {}
    return cards.make(rev, proposer, triad, c_min=spec.get("c_min", cards.BASE["c_min"]),
                      closed=spec.get("closed", cards.GATES), k=spec.get("k", cards.BASE["k"]),
                      qv={k: v for k, v in (("extract", qv.get("extraction")), ("propose", qv.get("proposal")),
                                            ("triad", qv.get("vote"))) if v})
