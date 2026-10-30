"""Reference runner: models propose, rules decide. Python 3 stdlib only."""
import hashlib
import json
import sqlite3
import time
from collections import Counter, defaultdict

import cards

DECODING = {"temperature": 0}
SCALE_V = "sc1"
DEFAULT_EXTRACT = {"complete": True, "work_object": True, "default": True}
NO_MATCH = {"answer": None, "confidence": 1.0, "quotes": [], "default": True}

SCHEMA = """
CREATE TABLE units(id INTEGER PRIMARY KEY, unit_id, release, type, text, keywords);
CREATE TABLE hub(id INTEGER PRIMARY KEY, entry_id, revision, keyword, text);
CREATE TABLE index_versions(id INTEGER PRIMARY KEY, version, hub_revision, retriever_version, entries_hash);
CREATE TABLE outputs(id INTEGER PRIMARY KEY, role, model_id, question_version, prompt_hash, evidence_hash, payload, created_at);
CREATE TABLE candidate_sets(id INTEGER PRIMARY KEY, unit_id, release, retriever_version, index_version,
  hub_revision, hash, candidates, created_at);
CREATE TABLE decisions(id INTEGER PRIMARY KEY, unit_id, candidate, route, reason, level, card_id, set_revision,
  batch, superseded_by, basis, trace, created_at, superseded_at);
CREATE TABLE rulings(id INTEGER PRIMARY KEY, kind, unit_id, candidate, reason, shown_hash, shown, ruling, level,
  reviewer, note, created_at);
CREATE TABLE curator_decisions(id INTEGER PRIMARY KEY, unit_id, candidate, shown_hash, shown, decision, new_entry,
  new_revision, curator, created_at);
CREATE TABLE activations(id INTEGER PRIMARY KEY, set_revision, who, report_hash, created_at);
CREATE TABLE writes(id INTEGER PRIMARY KEY, op, tbl, row_id, before, after, created_at);
"""


def H(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()[:16]


def prov(model_id):
    return model_id.split("/")[0]


class ReplayDefect(Exception):
    pass


class StubModel:
    """Canned outputs keyed by (role, question_version, evidence_hash). Every call is counted."""

    def __init__(self, canned, calls):
        self.calls, self.idx = calls, {(c["role"], c["qv"], H(c["ev"])): c["out"] for c in canned}

    def __call__(self, role, qv, ev):
        self.calls["model"] += 1
        out = self.idx.get((role, qv, H(ev)))
        return out if out is not None else (DEFAULT_EXTRACT if role == "extract" else NO_MATCH)


class StubHuman:
    """Reviewer or curator reading canned rulings by key; counted separately."""

    def __init__(self, canned, calls, name):
        self.canned, self.calls, self.name = canned, calls, name

    def __call__(self, key):
        self.calls[self.name] += 1
        return self.canned.get(key)


class Runner:
    def __init__(self, fx, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.fx, self.calls, self.batch, self.op = fx, Counter(), 0, "load"
        self.tally, self.unit_tally = Counter(), defaultdict(Counter)
        self.model = StubModel(fx["canned"], self.calls)
        self.reviewer = StubHuman(fx["rulings"], self.calls, "reviewer")
        self.curator = StubHuman(fx["curator"], self.calls, "curator")
        for u in fx["units"]:
            self.insert("units", unit_id=u["id"], release=u.get("release", 1), type=u["type"], text=u["text"],
                        keywords=json.dumps(u["keywords"]))
        for e in fx["hub"]:
            self.insert("hub", entry_id=e["id"], revision=1, keyword=e["keyword"], text=e["text"])
        self._index(1)
        self.unit_ids = [u["id"] for u in fx["units"]]

    # ---- store with write footprints (Undo 2) -------------------------------------------------
    def row(self, tbl, rid):
        r = self.db.execute(f"SELECT * FROM {tbl} WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None

    def _fp(self, tbl, rid, before, after):
        self.db.execute("INSERT INTO writes(op,tbl,row_id,before,after,created_at) VALUES(?,?,?,?,?,?)",
                        (self.op, tbl, rid, json.dumps(before), json.dumps(after), time.time()))

    def insert(self, tbl, **row):
        rid = self.db.execute(f"INSERT INTO {tbl}({','.join(row)}) VALUES({','.join('?' * len(row))})",
                              list(row.values())).lastrowid
        self._fp(tbl, rid, None, self.row(tbl, rid))
        return rid

    def update(self, tbl, rid, **chg):
        before = self.row(tbl, rid)
        self.db.execute(f"UPDATE {tbl} SET {','.join(k + '=?' for k in chg)} WHERE id=?", [*chg.values(), rid])
        self._fp(tbl, rid, before, self.row(tbl, rid))

    def recover(self, op):
        """Restore every footprint row of write `op` to its before value, newest first."""
        self.op = f"recover:{op}"
        rows = self.db.execute("SELECT * FROM writes WHERE op=? ORDER BY id DESC", (op,)).fetchall()
        for w in rows:
            before = json.loads(w["before"])
            if before is None:  # ponytail: inserts are undone by delete, footprint kept in writes
                self._fp(w["tbl"], w["row_id"], self.row(w["tbl"], w["row_id"]), None)
                self.db.execute(f"DELETE FROM {w['tbl']} WHERE id=?", (w["row_id"],))
            else:
                self.update(w["tbl"], w["row_id"], **{k: v for k, v in before.items() if k != "id"})
        return [(w["tbl"], w["row_id"]) for w in rows]

    # ---- versions -----------------------------------------------------------------------------
    def hub_rev(self):
        return self.db.execute("SELECT MAX(revision) FROM hub").fetchone()[0]

    def _index(self, rev):
        ents = [tuple(r) for r in self.db.execute(
            "SELECT entry_id, keyword FROM hub WHERE revision<=? ORDER BY id", (rev,))]
        self.insert("index_versions", version=f"ix-{rev}", hub_revision=rev, retriever_version="kw-v1",
                    entries_hash=H(ents))

    def unit(self, uid):
        r = dict(self.db.execute("SELECT * FROM units WHERE unit_id=? ORDER BY release DESC", (uid,)).fetchone())
        r["keywords"] = json.loads(r["keywords"])
        return r

    def _t(self, kind, status):
        self.tally[f"{kind}:{status}"] += 1
        self.unit_tally[self.cur][f"{kind}:{status}"] += 1

    # ---- stored outputs: reuse only on same question AND same evidence (P7, P10) ------------
    def ask(self, mode, kind, role, model_id, qv, ev, stub_ev):
        q = {"role": role, "model": model_id, "qv": qv, "decoding": DECODING, "scale": SCALE_V}
        eh, ph = H(ev), H([q, ev])
        r = self.db.execute("SELECT id, payload FROM outputs WHERE role=? AND model_id=? AND question_version=? "
                            "AND prompt_hash=? AND evidence_hash=?", (role, model_id, qv, ph, eh)).fetchone()
        if r:
            self._t(kind, "reused")
            return json.loads(r["payload"]), r["id"]
        if mode == "replay":
            raise ReplayDefect(f"missing {kind} output for {ev}")
        self._t(kind, "asked")
        out = self.model(role, qv, stub_ev)
        return out, self.insert("outputs", role=role, model_id=model_id, question_version=qv, prompt_hash=ph,
                                evidence_hash=eh, payload=json.dumps(out), created_at=time.time())

    def retrieve(self, mode, u, hub_rev, S):
        ix = f"ix-{hub_rev}"
        r = self.db.execute("SELECT * FROM candidate_sets WHERE unit_id=? AND release=? AND retriever_version=? "
                            "AND index_version=?", (u["unit_id"], u["release"], S["retriever"], ix)).fetchone()
        if r:
            self._t("retrieval", "reused")
            return {"id": r["id"], "hash": r["hash"], "cands": json.loads(r["candidates"])}
        if mode == "replay":
            raise ReplayDefect(f"missing candidate set for {u['unit_id']} {ix}")
        self._t("retrieval", "asked")
        self.calls["retriever"] += 1
        ents = self.db.execute("SELECT entry_id, keyword FROM hub WHERE revision<=?", (hub_rev,)).fetchall()
        cands = sorted((e for e, k in ents if k in u["keywords"]), key=lambda e: int(e[1:]))[:S["params"]["k"]]
        h = H([u["unit_id"], u["release"], S["retriever"], ix, hub_rev, cands])
        rid = self.insert("candidate_sets", unit_id=u["unit_id"], release=u["release"], retriever_version=S["retriever"],
                          index_version=ix, hub_revision=hub_rev, hash=h, candidates=json.dumps(cands),
                          created_at=time.time())
        return {"id": rid, "hash": h, "cands": cands}

    def ruling(self, mode, kind, uid, cand, reason, shown):
        sh = H(shown)
        r = self.db.execute("SELECT * FROM rulings WHERE kind=? AND unit_id=? AND candidate IS ? AND reason=? "
                            "AND shown_hash=?", (kind, uid, cand, reason, sh)).fetchone()
        if r:
            self._t("ruling", "reused")
            return dict(r)
        if mode == "replay" or kind == "challenge":  # challenges are external events, never solicited here
            return None
        self._t("ruling", "asked")
        got = self.reviewer(f"{uid}|{cand or '-'}|{reason}")
        if got is None:
            return None
        return self.row("rulings", self.insert(
            "rulings", kind=kind, unit_id=uid, candidate=cand, reason=reason, shown_hash=sh, shown=json.dumps(shown),
            ruling=got["ruling"], level=got.get("level"), reviewer=self.fx["reviewer_id"], note=None,
            created_at=time.time()))

    def curate(self, mode, uid, cand, hub_rev, shown):
        sh = H(shown)
        r = self.db.execute("SELECT * FROM curator_decisions WHERE unit_id=? AND candidate=? AND shown_hash=?",
                            (uid, cand, sh)).fetchone()
        if r:
            self._t("curator", "reused")
            return dict(r)
        if mode == "replay":
            return None
        self._t("curator", "asked")
        got = self.curator(f"{uid}|{cand}|{hub_rev}")
        if got is None:
            return None
        new_rev = None
        if got["decision"] == "approve":
            new_rev, e = self.hub_rev() + 1, got["entry"]
            self.insert("hub", entry_id=e["id"], revision=new_rev, keyword=e["keyword"], text=e["text"])
            self._index(new_rev)
        return self.row("curator_decisions", self.insert(
            "curator_decisions", unit_id=uid, candidate=cand, shown_hash=sh, shown=json.dumps(shown),
            decision=got["decision"], new_entry=json.dumps(got.get("entry")), new_revision=new_rev,
            curator=self.fx["curator_id"], created_at=time.time()))

    # ---- the run graph ------------------------------------------------------------------------
    def pick(self, S, name, ctx, trace):
        for cid, rev, cond, out in S["cards"][name]:
            hit = bool(cond(ctx, S["params"]))
            trace.append([name, cid, rev, hit])
            if hit:
                return cid, out
        raise AssertionError(f"card set {name} has no otherwise")  # unreachable by check 3

    @staticmethod
    def entry(cand, route, reason, level, card, basis, trace, att):
        return dict(cand=cand, route=route, reason=reason, level=level, card=card, basis=basis, trace=trace, att=att)

    @staticmethod
    def shown_of(e):
        return {"decision": [e["route"], e["reason"], e["level"], [b.get("out") for b in e["basis"]]]}

    def settle(self, mode, S, uid, chain):
        """Rulings and audits that follow a route outcome; then any stored challenge."""
        e = chain[-1]
        t = e["trace"]  # audit and ruling picks are recorded on the row they act on
        r = None
        if e["card"] == "SS-scope":
            cid, (route, _) = self.pick(S, "audit", {"unit_id": uid}, t)
            if route == "AUDIT":  # record stands; a ruling (if any) supersedes it
                r = self.ruling(mode, "audit", uid, None, "audit_scope", self.shown_of(e))
        elif e["route"] == "REVIEW":
            r = self.ruling(mode, "review", uid, e["cand"], e["reason"], self.shown_of(e))
        if r is not None or e["route"] == "REVIEW":
            cid, (route, reason) = self.pick(S, "ruling", {"ruling": r}, t)
            if route == "RECORD":
                hb = {"kind": "human", "who": r["reviewer"], "out": r["id"]}
                chain.append(self.entry(e["cand"], route, reason, r["level"] if cid == "H1" else None, cid,
                                        e["basis"] + [hb], list(t), e["att"]))
        e = chain[-1]
        c = self.ruling(mode, "challenge", uid, e["cand"], "challenge", self.shown_of(e))
        if c:
            t = list(e["trace"])
            chain.append(self.entry(e["cand"], "REVIEW", "challenge", None, "CH", e["basis"], t, e["att"]))
            cid, (route, reason) = self.pick(S, "ruling", {"ruling": c}, t)
            if route == "RECORD":
                hb = {"kind": "human", "who": c["reviewer"], "out": c["id"]}
                chain.append(self.entry(e["cand"], route, reason, c["level"] if cid == "H1" else None, cid,
                                        e["basis"] + [hb], t, e["att"]))
        return chain

    def run_unit(self, uid, S, mode):
        """mode 'live': reuse what may be reused, ask what is missing. 'replay': stored outputs only."""
        self.cur, P, qv = uid, S["params"], S["qv"]
        if cards.engine_hash() != S["engine"]:  # P9: the engine is part of the set revision
            raise RuntimeError(f"engine changed under set {S['rev']} without a new set revision")
        u, trace, gap, xs = self.unit(uid), [], 0, []
        while True:  # I0..I5; bounded by I3/I4 (G_max)
            ext, xid = self.ask(mode, "extraction", "extract", S["proposer"], qv["extract"],
                                {"unit": uid, "release": u["release"], "attempt": gap}, {"unit": uid, "attempt": gap})
            xs.append({"kind": "model", "who": prov(S["proposer"]), "out": xid})
            cid, (route, reason) = self.pick(S, "intake", {"unit": u, "ext": ext, "gap": gap}, trace)
            if route != "RETRY":
                break
            gap += 1
        if route != "NEXT":
            basis = [{"kind": "rule", "who": "metadata"}] if cid == "I1" else xs[-1:]
            return [self.settle(mode, S, uid, [self.entry(None, route, reason, None, cid, basis, trace,
                                                          {"gap": gap, "hub": 0})])]
        pinned = self.db.execute("SELECT MIN(hub_revision) FROM candidate_sets WHERE unit_id=? AND release=?",
                                 (uid, u["release"])).fetchone()[0]
        hub_rev, hub_att = pinned or self.hub_rev(), 0
        while True:  # R0..; bounded by E1/E2 (H_max)
            cs = self.retrieve(mode, u, hub_rev, S)
            cid, (route, reason) = self.pick(S, "retrieval", {"cands": cs["cands"]}, trace)
            if route == "RECORD":
                b = [{"kind": "retriever", "who": S["retriever"], "out": cs["id"]}]
                return [self.settle(mode, S, uid, [self.entry(None, route, reason, None, cid, b, trace,
                                                              {"gap": gap, "hub": hub_att})])]
            chains, reopen = [], False
            for c in cs["cands"]:
                t = list(trace)
                pev = {"unit": uid, "release": u["release"], "cset": cs["hash"], "hub_rev": hub_rev, "cand": c}
                sev = {"unit": uid, "cand": c, "hub_rev": hub_rev}
                prop, pid = self.ask(mode, "proposal", "propose", S["proposer"], qv["propose"], pev, sev)
                basis = [{"kind": "model", "who": prov(S["proposer"]), "out": pid}]
                cid, (route, reason) = self.pick(S, "evidence", {"prop": prop, "unit": u, "hub_att": hub_att}, t)
                level = None
                if route == "HUB":
                    cur = self.curate(mode, uid, c, hub_rev, {"proposal": pid, "hub_rev": hub_rev})
                    cid, (route, reason) = self.pick(S, "curator", {"cur": cur}, t)
                    if route == "REOPEN":
                        hub_rev, hub_att, trace, reopen = cur["new_revision"], hub_att + 1, t, True
                        break
                    if cur:
                        basis.append({"kind": "human", "who": cur["curator"], "out": cur["id"]})
                elif route == "NEXT":  # triad sees unit, candidate, evidence; never the proposer's answer
                    votes = []
                    for m in S["triad"]:
                        v, vid = self.ask(mode, "triad", f"triad/{prov(m)}", m, qv["triad"],
                                          dict(pev, quotes=prop.get("quotes", [])), sev)
                        votes.append(v)
                        basis.append({"kind": "model", "who": prov(m), "out": vid})
                    cid, (route, reason) = self.pick(S, "triad", {"votes": votes, "n": len(S["triad"])}, t)
                    level = cards.plurality(votes) if cid == "T3" else None
                chains.append(self.settle(mode, S, uid, [self.entry(c, route, reason, level, cid, basis, t,
                                                                    {"gap": gap, "hub": hub_att})]))
            if not reopen:
                return chains

    # ---- ledger -------------------------------------------------------------------------------
    def live(self):
        return {(r["unit_id"], r["candidate"]): dict(r) for r in
                self.db.execute("SELECT * FROM decisions WHERE superseded_by IS NULL")}

    def record(self, uid, chains, rev, partial=False):
        now = time.time()
        old = {r["candidate"]: r["id"] for r in self.db.execute(
            "SELECT id, candidate FROM decisions WHERE unit_id=? AND superseded_by IS NULL", (uid,))}
        first = None
        for ch in chains:
            prev = old.pop(ch[0]["cand"], None)
            for e in ch:
                rid = self.insert("decisions", unit_id=uid, candidate=e["cand"], route=e["route"], reason=e["reason"],
                                  level=e["level"], card_id=e["card"], set_revision=rev, batch=self.batch,
                                  superseded_by=None, basis=json.dumps(e["basis"]),
                                  trace=json.dumps({"cards": e["trace"], "attempts": e["att"]}),
                                  created_at=now, superseded_at=None)
                first = first or rid
                if prev:
                    self.update("decisions", prev, superseded_by=rid, superseded_at=now)
                prev = rid
        for rid in [] if partial else old.values():  # candidates no longer in the set
            self.update("decisions", rid, superseded_by=first, superseded_at=now)

    def activate(self, rev, who, report_hash):
        self.batch += 1
        self.op = f"activate:{rev}:{self.batch}"
        self.tally = Counter()
        self.insert("activations", set_revision=rev, who=who, report_hash=report_hash, created_at=time.time())
        for uid in self.unit_ids:
            self.record(uid, self.run_unit(uid, cards.SETS[rev], "live"), rev)
        self.db.commit()
        return Counter(self.tally)

    def challenge(self, uid, cand, code, by):
        self.op = f"challenge:{uid}:{cand}"
        live = self.live()[(uid, cand)]
        tr = json.loads(live["trace"])
        e = self.entry(cand, live["route"], live["reason"], live["level"], live["card_id"],
                       json.loads(live["basis"]), tr["cards"], tr["attempts"])
        got = self.reviewer(f"{uid}|{cand}|challenge")
        self.insert("rulings", kind="challenge", unit_id=uid, candidate=cand, reason="challenge",
                    shown_hash=H(self.shown_of(e)), shown=json.dumps(self.shown_of(e)), ruling=got["ruling"],
                    level=got.get("level"), reviewer=self.fx["reviewer_id"], note=f"{code} by {by}",
                    created_at=time.time())
        self.cur = uid
        chain = self.settle("replay", cards.SETS[live["set_revision"]], uid, [e])
        self.record(uid, [chain[1:]], live["set_revision"], partial=True)  # new ruling supersedes; both rows stay

    def evaluate(self, rev):
        """Evaluate a set over stored outputs, asking only what is missing; the ledger is untouched."""
        self.op, self.tally, self.unit_tally = f"evaluate:{rev}", Counter(), defaultdict(Counter)
        c0, live, outcomes, changed = Counter(self.calls), self.live(), {}, 0
        for uid in self.unit_ids:
            for ch in self.run_unit(uid, cards.SETS[rev], "live"):
                f = ch[-1]
                outcomes[f"{uid}|{f['cand'] or '-'}"] = [f["route"], f["reason"], f["level"]]
                lv = live.get((uid, f["cand"]))
                changed += not lv or [lv["route"], lv["reason"], lv["level"]] != outcomes[f"{uid}|{f['cand'] or '-'}"]
        self.db.commit()
        rep = {"set": rev, "tally": dict(sorted(self.tally.items())), "changed": changed, "outcomes": outcomes,
               "calls": {k: self.calls[k] - c0[k] for k in ("model", "reviewer", "curator", "retriever")},
               "per_unit": {u: dict(t) for u, t in self.unit_tally.items()}}
        rep["hash"] = H(rep)
        return rep
