#!/usr/bin/env python3
"""Items 7 to 9, the fixture side: the harness operations the goal's outcome tests call. Every one of them
runs the production cards, engine, store and identities; only provider responses are fixtures.

  seed         stored cases, sets and the active revision into a fresh store.sqlite; the live ledger comes out
  replay       every live decision re-decided from stored outputs under its own revision, outbound calls refused
  policy       the predeclared r3 threshold change: expected invalidations from the stage identities before any
               call, then exactly those vote calls re-asked, rulings naming a re-asked vote re-derived at no cost
  conformance  the run graph and the policy graph against the pinned Appendix C baseline, six dimensions each
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from collections import Counter
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "baseline")
APPENDIX_COMMIT = "4c3a05ec6b038d092f42e933d8a36ccc1d95c9b2"
APPENDIX_SOURCE = "CKI/11 - Project Knowledge/Papers/models-propose-rules-decide/models-propose-rules-decide_1.1.md"
STAGES = ("extraction", "retrieval", "proposal", "vote", "council")
ROLES = ("model", "retriever", "council")
R3_FORMULA = "max(0, c_min - 0.2) if max(0, c_min - 0.2) != c_min else min(1, c_min + 0.2)"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def oid(*parts):
    return hashlib.sha256(json.dumps(list(parts), sort_keys=True).encode()).hexdigest()[:16]


def r3_threshold(c_min):
    down = max(0.0, c_min - 0.2)
    return round(down if down != c_min else min(1.0, c_min + 0.2), 6)


# ---- the store as the engine reads it ---------------------------------------------------------------------
class Store:
    def __init__(self, path, readonly=False):
        self.path = path
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True) if readonly else sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row

    def rows(self, sql, *args):
        return [dict(r) for r in self.db.execute(sql, args)]

    def put(self, table, **cols):
        with self.db:
            self.db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            list(cols.values()))

    def sets(self):
        return {r["revision"]: json.loads(r["spec"]) for r in self.rows("SELECT revision, spec FROM sets")}

    def frozen(self, key, default=None):
        r = self.rows("SELECT value FROM frozen WHERE key=?", key)
        return json.loads(r[0]["value"]) if r else default

    def write_case(self, case, engine, providers, decided_by=None):
        """A case's outputs under their identities; returns the output rows as live_outputs lists them."""
        u = case["unit"]
        rel = f"{u['id']}@{u.get('release', 'fixture')}"
        self.put("units", unit_release=rel, unit_id=u["id"], catalog_sha256=u.get("release", "fixture"), family=u.get("family", u["id"]),
                 grp=u.get("catalog_family", u["id"].split("-")[0]), kind=u.get("type", "control"), split=u.get("split", "evaluation"),
                 statement=u.get("text", ""), in_scope=1, scope_reason=None)
        cand = (case.get("candidate") or {}).get("id")
        outputs = []
        ext = case.get("extraction") or {}
        attempt = max(0, int(ext.get("attempt_number", 1)) - 1)
        q = providers.identity("extraction", unit_release=rel, attempt=attempt, prompt_version="extract-1")
        ext_id = oid(q["id"])
        self.put("extractions", output_id=ext_id, unit_release=rel, attempt=attempt, identity=q["id"], activities="[]", quotes="[]",
                 prompt_version="extract-1", created=now(), complete=int(bool(ext.get("complete"))),
                 work_object=int(bool(ext.get("work_object"))), provider="openai", model="fixture-extractor-v1")
        outputs.append({"unit_id": u["id"], "candidate_id": None, "stage": "extraction", "provider": "openai", "output_id": ext_id})
        ret = case.get("retrieval") or {}
        q = providers.identity("retrieval", unit_release=rel, retriever=ret.get("retriever_version"), index=ret.get("index_version"),
                               hub_revision=ret.get("hub_revision"), k=(ret.get("route_parameters") or {}).get("k"))
        ret_id = oid(q["id"])
        self.put("retrievals", output_id=ret_id, unit_release=rel, extraction_output_id=ext_id, retriever=ret.get("retriever_version", ""),
                 index_version=ret.get("index_version", ""), hub_revision=int(ret.get("hub_revision") or 0),
                 route_params=json.dumps(ret.get("route_parameters") or {}), identity=q["id"],
                 candidates=json.dumps(ret.get("candidate_ids") or []), created=now())
        outputs.append({"unit_id": u["id"], "candidate_id": None, "stage": "retrieval", "provider": "openai", "output_id": ret_id})
        if cand is not None:
            c = case["candidate"]
            self.put("hub_entries", hub_entry=cand, hub_revision=int(c.get("revision") or 1), text=c.get("text", ""))
            prop = case.get("proposal") or {}
            q = providers.identity("proposal", unit_release=rel, extraction_output_id=ext_id, hub_entry=cand,
                                   hub_revision=int(c.get("revision") or 1), prompt_version="propose-1")
            prop_id = oid(q["id"])
            self.put("proposals", output_id=prop_id, unit_release=rel, extraction_output_id=ext_id, hub_entry=cand,
                     hub_revision=int(c.get("revision") or 1), identity=q["id"], level=prop.get("answer") or "no_relationship",
                     quotes=json.dumps(prop.get("quotes") or []), confidence=prop.get("confidence"), prompt_version="propose-1",
                     created=now(), conflict=int(bool(prop.get("conflict"))), needs_hub=int(bool(prop.get("needs_hub"))),
                     provider=prop.get("provider", "openai"), model=prop.get("model_id", ""))
            outputs.append({"unit_id": u["id"], "candidate_id": cand, "stage": "proposal", "provider": prop.get("provider", "openai"),
                            "output_id": prop_id})
            spec = decided_by or {}
            for v in case.get("votes") or []:
                vid = self.write_vote(rel, cand, prop, prop_id, v, spec.get("c_min"), case.get("set_revision"), providers)
                outputs.append({"unit_id": u["id"], "candidate_id": cand, "stage": "vote", "provider": v.get("provider"), "output_id": vid})
            r = case.get("operational_ruling")
            if r:
                rid = oid("ruling", prop_id, r.get("ruling"), r.get("level"), sorted(r.get("packet_uses") or []), [o["output_id"] for o in outputs])
                self.put("rulings", output_id=rid, proposal_output_id=prop_id, route="REVIEW", reason_code=r.get("reason", ""),
                         packet_output_ids=json.dumps({"uses": r.get("packet_uses") or [], "outputs": [o["output_id"] for o in outputs
                                                                                                        if o["stage"] in ("proposal", "vote")]}),
                         identity=rid, decision=r.get("ruling"), card=r.get("card_id", ""), revision=case.get("set_revision", ""),
                         created=now(), level=r.get("level"), origin=r.get("origin"), unit_release=rel, hub_entry=cand, superseded_by=None)
        return outputs

    def write_vote(self, rel, cand, prop, prop_id, v, c_min, revision, providers):
        spans = hashlib.sha256(json.dumps(prop.get("quotes") or []).encode()).hexdigest()[:16]
        q = providers.identity("vote", proposal_output_id=prop_id, voter=v.get("model_id"), quoted_spans=spans, c_min=c_min,
                               prompt_version="vote-1")
        vid = oid(q["id"])
        self.put("votes", output_id=vid, proposal_output_id=prop_id, voter=v.get("model_id", ""), evidence_identity=spans,
                 quoted_spans=json.dumps(prop.get("quotes") or []), vote_params=json.dumps({"c_min": c_min}), identity=q["id"],
                 vote=v.get("answer") if v.get("answer") is not None else "no_relationship", quotes_verbatim=None, confidence=None,
                 prompt_version="vote-1", revision=revision or "", created=now(), provider=v.get("provider"), unit_release=rel,
                 hub_entry=cand, superseded_by=None)
        return vid

    def read_cases(self, c_min_of):
        """Every stored (unit, candidate) pair as a case, with the votes under the revision's own threshold."""
        units = {r["unit_release"]: r for r in self.rows("SELECT * FROM units")}
        cases = []
        for d in self.live_rows():
            u = units[d["unit_release"]]
            ext = self.rows("SELECT * FROM extractions WHERE unit_release=? ORDER BY attempt DESC", d["unit_release"])
            ret = self.rows("SELECT * FROM retrievals WHERE unit_release=?", d["unit_release"])
            e = ext[0] if ext else {}
            case = {"unit": {"id": u["unit_id"], "type": u["kind"], "text": u["statement"], "family": u["family"], "split": u["split"]},
                    "set_revision": d["revision"],
                    "extraction": {"complete": bool(e.get("complete")), "work_object": bool(e.get("work_object")),
                                   "attempt_number": int(e.get("attempt", 0)) + 1} if e else {},
                    "retrieval": {"candidate_ids": json.loads(ret[0]["candidates"]) if ret else []},
                    "candidate": {"id": d["hub_entry"]} if d["hub_entry"] else None, "operational_ruling": None, "votes": []}
            if d["hub_entry"]:
                p = self.rows("SELECT * FROM proposals WHERE unit_release=? AND hub_entry=?", d["unit_release"], d["hub_entry"])[0]
                case["proposal"] = {"answer": p["level"], "quotes": json.loads(p["quotes"]), "confidence": p["confidence"],
                                    "conflict": bool(p["conflict"]), "needs_hub": bool(p["needs_hub"]), "output_id": p["output_id"]}
                c_min = c_min_of(d["revision"])
                case["votes"] = [{"provider": v["provider"], "model_id": v["voter"], "answer": v["vote"], "output_id": v["output_id"]}
                                 for v in self.rows("SELECT * FROM votes WHERE proposal_output_id=? AND superseded_by IS NULL "
                                                    "AND json_extract(vote_params, '$.c_min')=? ORDER BY voter", p["output_id"], c_min)]
                rul = self.rows("SELECT * FROM rulings WHERE proposal_output_id=? AND superseded_by IS NULL", p["output_id"])
                if rul:
                    r = rul[0]
                    case["operational_ruling"] = {"ruling": r["decision"], "level": r["level"], "origin": r["origin"], "card_id": r["card"],
                                                  "packet_uses": json.loads(r["packet_output_ids"]).get("uses", []), "output_id": r["output_id"]}
            cases.append((d, case))
        return cases

    def live_rows(self):
        return self.rows("SELECT * FROM decisions WHERE superseded_by IS NULL ORDER BY unit_id, hub_entry, decision_id")

    def live_decisions(self):
        return [{"decision_id": d["decision_id"], "unit_id": d["unit_id"], "candidate_id": d["hub_entry"], "set_revision": d["revision"],
                 "route": d["route"], "level": d["level"], "reason": d["reason_code"], "card_id": d["card_id"]} for d in self.live_rows()]

    def live_outputs(self):
        out = []
        units = {r["unit_release"]: r["unit_id"] for r in self.rows("SELECT unit_release, unit_id FROM units")}
        for r in self.rows("SELECT * FROM extractions ORDER BY output_id"):
            out.append({"unit_id": units[r["unit_release"]], "candidate_id": None, "stage": "extraction", "provider": r["provider"], "output_id": r["output_id"]})
        for r in self.rows("SELECT * FROM retrievals ORDER BY output_id"):
            out.append({"unit_id": units[r["unit_release"]], "candidate_id": None, "stage": "retrieval", "provider": "openai", "output_id": r["output_id"]})
        for r in self.rows("SELECT * FROM proposals ORDER BY output_id"):
            out.append({"unit_id": units[r["unit_release"]], "candidate_id": r["hub_entry"], "stage": "proposal", "provider": r["provider"], "output_id": r["output_id"]})
        for r in self.rows("SELECT * FROM votes WHERE superseded_by IS NULL ORDER BY output_id"):
            out.append({"unit_id": units[r["unit_release"]], "candidate_id": r["hub_entry"], "stage": "vote", "provider": r["provider"], "output_id": r["output_id"]})
        return out

    def write_decision(self, d, supersede=None):
        if supersede:
            with self.db:
                self.db.execute("UPDATE decisions SET superseded_by=? WHERE decision_id=?", (d["decision_id"], supersede))
        self.put("decisions", decision_id=d["decision_id"], unit_id=d["unit_id"], unit_release=d.get("unit_release"), hub_entry=d["candidate_id"],
                 route=d["route"], reason_code=d["reason"], level=d["level"], card_id=d["card_id"], writer_kind=d["writer_kind"],
                 ruling_output_id=d.get("ruling_output_id"), revision=d["set_revision"], trace=json.dumps(d["trace"]), superseded_by=None,
                 created=now())


# ---- seed -----------------------------------------------------------------------------------------------
def seed(h, p, engine, providers, store_mod):
    path = h.path("store.sqlite")
    store_mod.init(path)
    st = Store(path)
    sets = p["sets"]
    for rev, spec in sets.items():
        st.put("sets", revision=rev, active=int(rev == p.get("active_revision")), sha256=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
               gates=json.dumps(spec.get("closed", [])), created=now(), spec=json.dumps(spec))
    for key in ("frozen_change", "frozen_config"):
        if key in p:
            st.put("frozen", key=key, value=json.dumps(p[key]))
    st.put("frozen", key="active_revision", value=json.dumps(p.get("active_revision")))
    for case in p["cases"]:
        rev = case.get("set_revision") or p.get("active_revision")
        spec = sets[rev]
        S = engine.set_from_revision(rev, spec, f"{spec['proposer_provider']}/fixture-proposer-v1", [f"{v}/fixture-{v}-v1" for v in spec["triad_providers"]])
        st.write_case(case, engine, providers, decided_by=spec)
        d = engine.run_case(case, S)
        d["unit_release"] = f"{case['unit']['id']}@{case['unit'].get('release', 'fixture')}"
        st.write_decision(d)
    return h.finish("seed", {"store": path, "active_revision": p.get("active_revision"), "live_decisions": st.live_decisions(),
                             "live_outputs": st.live_outputs()})


def set_of(engine, rev, spec):
    return engine.set_from_revision(rev, spec, f"{spec['proposer_provider']}/fixture-proposer-v1", [f"{v}/fixture-{v}-v1" for v in spec["triad_providers"]])


# ---- replay ---------------------------------------------------------------------------------------------
def replay(h, p, engine, providers):
    st = Store(p["store"], readonly=True)
    sets = st.sets()
    counter = {"attempts": 0}
    transport = providers.ReplayTransport(counter)       # any outbound attempt raises and is counted
    cases = st.read_cases(lambda rev: sets[rev].get("c_min"))
    replayed, defects = [], []
    for d, case in cases:
        S = set_of(engine, d["revision"], sets[d["revision"]])
        try:
            r = engine.run_case(case, S)
        except Exception as e:                           # a missing output is a defect, never a call
            defects.append({"decision_id": d["decision_id"], "error": str(e)})
            continue
        row = {"decision_id": r["decision_id"], "unit_id": r["unit_id"], "candidate_id": r["candidate_id"], "set_revision": r["set_revision"],
               "route": r["route"], "level": r["level"], "reason": r["reason"], "card_id": r["card_id"]}
        replayed.append(row)
        stored = (d["route"], d["level"], d["reason_code"], d["card_id"], d["decision_id"])
        if stored != (r["route"], r["level"], r["reason"], r["card_id"], r["decision_id"]):
            defects.append({"decision_id": d["decision_id"], "stored": stored, "replayed": (r["route"], r["level"], r["reason"], r["card_id"])})
    zero = dict.fromkeys(ROLES, 0)
    return h.finish("replay", {"replayed_decisions": replayed, "defects": defects, "live_decisions_after": st.live_decisions(),
                               "outbound_attempts": {**zero, "model": counter["attempts"]}, "call_counter_delta": dict(zero),
                               "completed_outbound_calls": dict(zero), "replay_transport": transport.kind}, 0 if not defects else 1)


# ---- policy ---------------------------------------------------------------------------------------------
def policy(h, p, engine, providers, fixture_adapter, fixture_protocol):
    src = p["store"]
    path = h.path("store.sqlite")
    shutil.copyfile(src, path)
    st = Store(path)
    sets = st.sets()
    rev = p.get("revision", "r3")
    change = st.frozen("frozen_change") or {}
    config = st.frozen("frozen_config") or {}
    base_rev = max(r for r in sets if r != rev) if rev not in sets else rev     # the revision r3 derives from: the newest other one
    before = float(config.get("c_min", sets[base_rev].get("c_min")))
    after = r3_threshold(before)
    if "r3_c_min" in config and abs(float(config["r3_c_min"]) - after) > 1e-9:
        print(f"refused: frozen r3_c_min {config['r3_c_min']} is not the formula's {after}", file=__import__("sys").stderr)
        return h.finish("policy", {"refused": True, "threshold_before": before, "threshold_after": after}, 1)
    spec3 = {**sets[base_rev], "c_min": after}
    if rev not in sets:
        st.put("sets", revision=rev, active=0, sha256=hashlib.sha256(json.dumps(spec3, sort_keys=True).encode()).hexdigest(),
               gates=json.dumps(spec3.get("closed", [])), created=now(), spec=json.dumps(spec3))
        sets[rev] = spec3
    S3 = set_of(engine, rev, sets[rev])
    split = change.get("split", "test")
    voters = [(f"{v}/fixture-{v}-v1", v) for v in sets[rev]["triad_providers"]]
    # 1. expected invalidations, from the identities alone, before any call
    expected, targets = [], []
    for d, case in st.read_cases(lambda r: sets[r].get("c_min")):
        if d["revision"] == rev or case["unit"].get("split") != split or not case.get("candidate"):
            continue
        probe = engine.run_case({**case, "votes": [], "operational_ruling": None}, S3)
        if probe["card_id"] == "T1":                      # the evidence cards pass under r3: its vote identities changed
            targets.append((d, case))
            for model, prov in voters:
                expected.append({"unit_id": d["unit_id"], "candidate_id": d["hub_entry"], "stage": "vote", "provider": prov, "model": model})
    proto = fixture_protocol({"cap_usd": "60.00", "reserve_usd": "10.00", "input_token_ceiling": 1000, "output_token_ceiling": 1000,
                              "input_usd_per_million": "0.001", "output_usd_per_million": "0.001"}, {m: pv for m, pv in voters}, ["vote"])
    a = fixture_adapter(proto, h.out)
    a.event("expected_invalidations", revision=rev, threshold_before=before, threshold_after=after, calls=expected)
    # 2. exactly those calls, through the adapter, each answered by the fixture with the stored vote
    observed, new_votes = [], {}
    for d, case in targets:
        prop = case["proposal"]
        old = {v["model_id"]: v for v in case["votes"]}
        for model, prov in voters:
            answer = (old.get(model) or {}).get("answer", "no_relationship")
            a.response = {"text": json.dumps({"vote": answer}), "input_tokens": 1, "output_tokens": 1}
            q = providers.identity("vote", proposal_output_id=prop["output_id"], voter=model,
                                   quoted_spans=hashlib.sha256(json.dumps(prop["quotes"]).encode()).hexdigest()[:16], c_min=after, prompt_version="vote-1")
            out = a.call("vote", model, "vote-1", json.dumps({"unit": case["unit"]["id"], "candidate": d["hub_entry"]}), q, None,
                         unit_id=d["unit_id"], candidate_id=d["hub_entry"])
            observed.append({"unit_id": d["unit_id"], "candidate_id": d["hub_entry"], "stage": "vote", "provider": prov, "model": model})
            vid = st.write_vote(d["unit_release"], d["hub_entry"], prop, prop["output_id"], {"model_id": model, "provider": prov, "answer": out.get("vote")},
                                after, rev, providers)
            new_votes.setdefault(d["decision_id"], []).append((model, prov, out.get("vote"), vid))
    # 3. re-decide the targets under r3; rulings naming a re-asked vote are re-derived at no cost
    rederived = []
    for d, case in targets:
        votes = [{"provider": prov, "model_id": model, "answer": ans, "output_id": vid} for model, prov, ans, vid in new_votes[d["decision_id"]]]
        r = engine.run_case({**case, "votes": votes}, S3)
        r["unit_release"] = d["unit_release"]
        rul = case.get("operational_ruling")
        if rul and "votes" in (rul.get("packet_uses") or []):
            old_ids = [v["output_id"] for v in case["votes"]]
            new_ids = [v["output_id"] for v in votes]
            new_rid = oid("ruling", case["proposal"]["output_id"], rul["ruling"], rul.get("level"), sorted(rul["packet_uses"]), new_ids)
            with st.db:
                st.db.execute("UPDATE rulings SET superseded_by=? WHERE output_id=?", (new_rid, rul["output_id"]))
            st.put("rulings", output_id=new_rid, proposal_output_id=case["proposal"]["output_id"], route="REVIEW", reason_code="",
                   packet_output_ids=json.dumps({"uses": rul["packet_uses"], "outputs": [case["proposal"]["output_id"], *new_ids]}),
                   identity=new_rid, decision=rul["ruling"], card=rul.get("card_id", ""), revision=rev, created=now(), level=rul.get("level"),
                   origin=rul.get("origin"), unit_release=d["unit_release"], hub_entry=d["hub_entry"], superseded_by=None)
            rederived.append({"unit_id": d["unit_id"], "candidate_id": d["hub_entry"], "old_ruling_output_id": rul["output_id"],
                              "new_ruling_output_id": new_rid, "old_vote_output_ids": old_ids, "new_vote_output_ids": new_ids,
                              "ruling": rul["ruling"], "cost_usd": "0"})
            r = engine.run_case({**case, "votes": votes, "operational_ruling": rul}, S3)
            r["unit_release"] = d["unit_release"]
        with st.db:
            for v in case["votes"]:
                st.db.execute("UPDATE votes SET superseded_by=? WHERE output_id=?", (votes[0]["output_id"], v["output_id"]))
        st.write_decision(r, supersede=d["decision_id"])
    cost = Decimal("0")
    acc = h.path("accounting.jsonl")
    if os.path.isfile(acc):
        with open(acc, encoding="utf-8") as f:
            cost = sum((Decimal(json.loads(l)["charged_usd"]) for l in f if l.strip()), Decimal("0"))
    ident = lambda c: (c["unit_id"], c["candidate_id"], c["stage"], c["provider"])
    counts = lambda calls: {s: sum(1 for c in calls if c["stage"] == s) for s in STAGES}
    report = {"revision": rev, "change": change, "formula": R3_FORMULA, "threshold_before": before, "threshold_after": after,
              "expected_calls": expected, "observed_calls": observed, "expected_counts": counts(expected), "observed_counts": counts(observed),
              "equal": Counter(map(ident, expected)) == Counter(map(ident, observed)), "rederived_rulings": rederived,
              "cost_usd": providers.money(cost), "split": split}
    h.write("policy_report.json", report)
    unplanned = [c for c in observed if ident(c) not in set(map(ident, expected))]
    return h.finish("policy", {"report": report, "unplanned_calls": unplanned, "live_outputs": st.live_outputs(),
                               "live_decisions": st.live_decisions()})


# ---- conformance ----------------------------------------------------------------------------------------
ALIASES = (("SP 800-53 work items", "Work items"), ("SP 800-53 controls", "units"), ("SP 800-53 control", "unit"),
           ("CSF 2.0 subcategories", "hub entries"), ("CSF 2.0 subcategory", "hub entry"), ("CSF 2.0 catalog", "hub"),
           ("Council reviewer", "Reviewer"), ("council reviewer", "reviewer"), ("no_relationship", "no match"))


def normalized_label(label):
    for domain, original in ALIASES:
        label = label.replace(domain, original)
    return " ".join(label.replace("<br/>", " ").replace("<br>", " ").split())


def domain_graph(baseline):
    """The run's own graph: the paper's graph in this domain's nouns (whole words, longest first)."""
    text = baseline
    for original, domain in (("Work items", "SP 800-53 work items"), ("hub entries", "CSF 2.0 subcategories"),
                             ("hub entry", "CSF 2.0 subcategory"), ("units", "SP 800-53 controls"), ("unit", "SP 800-53 control"),
                             ("hub", "CSF 2.0 catalog"), ("reviewer", "council reviewer"), ("Reviewer", "Council reviewer"),
                             ("no match", "no_relationship")):
        text = re.sub(r"(?<![A-Za-z_.])" + re.escape(original) + r"(?![A-Za-z_])", domain, text)
    return text


_NODE = re.compile(r'([A-Za-z][A-Za-z0-9_]*)\s*(\[\("([^"]*)"\)\]|\(\["([^"]*)"\]\)|\["([^"]*)"\]|\{"([^"]*)"\})?')
_ARROW = re.compile(r'\s*(-->|--\s*"([^"]*)"\s*-->|-\.\s*"([^"]*)"\s*\.->)\s*')


def project(source):
    """Six dimensions of a Mermaid flowchart in the pinned dialect: nodes, edges, card ids, lanes, stores, decision semantics."""
    nodes, edges, lanes, lane = {}, [], {}, None

    def node_at(line, offset):
        m = _NODE.match(line, offset)
        if not m:
            raise ValueError(f"unsupported graph syntax: {line[offset:]}")
        name = m.group(1)
        if m.group(2):
            labels = m.groups()[2:]
            i = next(k for k, v in enumerate(labels) if v is not None)
            value = (("store", "terminal", "process", "decision")[i], normalized_label(labels[i]), lane)
            if name in nodes and nodes[name] != value:
                raise ValueError(f"node {name} redefined")
            nodes[name] = value
        return name, m.end()

    for original in source.splitlines():
        line = original.strip()
        if not line or line.startswith("%%") or line.startswith("flowchart "):
            continue
        sg = re.fullmatch(r'subgraph\s+(\w+)\["([^"]*)"\]', line)
        if sg:
            lane = sg.group(1)
            lanes[lane] = normalized_label(sg.group(2))
            continue
        if line == "end":
            lane = None
            continue
        src, off = node_at(line, 0)
        while off < len(line):
            arrow = _ARROW.match(line, off)
            if not arrow:
                raise ValueError(f"unsupported graph edge: {line[off:]}")
            dst, off = node_at(line, arrow.end())
            label = normalized_label(arrow.group(2) or arrow.group(3) or "")
            edges.append((src, dst, "dotted" if arrow.group(1).startswith("-.") else "solid", label))
            src = dst
    decisions = {n: v[1] for n, v in nodes.items() if v[0] == "decision"}
    card_ids = []
    for label in decisions.values():
        m = re.match(r"(SS-scope|SS-retrieval|[IERT]\d+(?:-gated)?)\b", label)
        if m:
            card_ids.append(m.group(1))
    return {"nodes": nodes, "edges": Counter(edges), "card_ids": sorted(card_ids), "lanes": lanes,
            "stores": sorted(n for n, v in nodes.items() if v[0] == "store"), "decision_semantics": decisions}


def diff(expected, actual):
    out = {}
    for dim in expected:
        e, a = expected[dim], actual.get(dim)
        if isinstance(e, Counter):
            out[dim] = [{"missing": list(k), "count": v} for k, v in (e - a).items()] + [{"extra": list(k), "count": v} for k, v in (a - e).items()]
        elif isinstance(e, dict):
            out[dim] = [{"key": k, "expected": e.get(k), "actual": a.get(k)} for k in sorted(set(e) | set(a)) if e.get(k) != a.get(k)]
        else:
            out[dim] = [{"expected": e, "actual": a}] if e != a else []
    return out


def conformance(h, p, card_table, cards):
    baseline = {}
    for name in ("run", "policy"):
        with open(os.path.join(BASELINE, f"{name}.mmd"), "rb") as f:
            baseline[name] = f.read()
    if p.get("baseline_override"):
        baseline = {k: v.encode("utf-8") for k, v in p["baseline_override"].items()}
    pinned = {"run": "b81e841e771fca94b3a2c9699849084391d199df87df2a16d96d1058521ef70e",
              "policy": "2e63659d61bca9d9d73c6e7fa451181363adee42b6f566e95c113c6bf447a38c"}
    bad = [n for n in pinned if hashlib.sha256(baseline[n]).hexdigest() != pinned[n]]
    if bad:
        print(f"refused: baseline hash mismatch for {', '.join(bad)} (pinned at {APPENDIX_COMMIT[:9]})", file=__import__("sys").stderr)
        return h.finish("conformance", {"refused": True, "hash_mismatch": bad}, 6)
    os.makedirs(h.path("baseline"), exist_ok=True)
    os.makedirs(h.path("graphs"), exist_ok=True)
    ours = {}
    for name, raw in baseline.items():
        with open(h.path(f"baseline/{name}.mmd"), "wb") as f:
            f.write(raw)
        ours[name] = (p.get("graphs") or {}).get(name) or domain_graph(raw.decode("utf-8"))
        with open(h.path(f"graphs/{name}.mmd"), "w", encoding="utf-8") as f:
            f.write(ours[name])
    report_diff = {name: diff(project(baseline[name].decode("utf-8")), project(ours[name])) for name in baseline}
    forks = [(name, dim) for name, dims in report_diff.items() for dim, rows in dims.items() if rows]
    table = card_table()
    exercised = False
    if p.get("store") and os.path.isfile(p["store"]):
        st = Store(p["store"], readonly=True)
        exercised = bool(st.rows("SELECT 1 FROM decisions WHERE card_id='E1' LIMIT 1"))
    report = {"baseline_commit": APPENDIX_COMMIT, "baseline_source": APPENDIX_SOURCE, "card_lists": cards.card_ids(),
              "E1": {"present": True, "exercised": exercised}, "diff": report_diff, "card_table": table, "forks": [list(f) for f in forks]}
    h.write("conformance.json", report)
    if forks:
        for name, dim in forks:
            print(f"fork: {name} graph differs from Appendix C in {dim}: {json.dumps(report_diff[name][dim][:3])[:300]}")
    else:
        print("conformance: zero deviations from Appendix C in nodes, edges, card_ids, lanes, stores and decision_semantics "
              "for the run graph and the policy graph")
    print(f"card E1 present, unexercised (route is retrieval only: H_max = 0); {sum(v == 'PASS' for v in table.values())} cards PASS, "
          f"{sum(v == 'present_unreachable' for v in table.values())} present_unreachable")
    return h.finish("conformance", report)
