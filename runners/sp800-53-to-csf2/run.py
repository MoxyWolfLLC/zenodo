#!/usr/bin/env python3
"""Item 6: the live passes. Models propose, the cards decide; every model output is stored under its
stage identity and reused whenever the identity recurs, so a revision re-asks exactly what its change
touches and nothing else.

    python3 -I run.py calibrate [--units N] [--revisions N] [--workers W]
    python3 -I run.py reconcile      drop a unit's terminal row under a revision that also holds its candidate rows
    python3 -I run.py evaluate [--workers W]
        the evaluation split under set r2: the frozen configuration (config.frozen.json) with every gate closed; the
        decisions are the predictions items 7 scores; set_r2.json is written with its hash. Exit 0; 4 on a budget refusal.
        the calibration split (protocol.json sample, split "calibration"), one revision per entry of the
        schedule below, each scored against the pinned NIST informative references for the split as weak
        labels (link versus no link only; absence is never a negative, so a unit with no control-level
        reference of its own or of its base control is unscored). Every revision goes to calibration.jsonl
        with its cost from the adapter's rows; the chosen configuration goes to config.frozen.json with its
        hash. stdout prints per-revision cost and the chosen configuration. Exit 0; 4 on a budget refusal.

A finished calibration (config.frozen.json present) is never re-run over itself: calibrate refuses with exit 2 and
says what to remove. An unfinished one (calibration.jsonl without config.frozen.json) resumes at the next revision
of the schedule with the recorded rows kept, so revision ids stay unique and within the cap.

What it reads: runners/sp800-53-to-csf2/protocol.json (the sample and its splits, roles, budget), index.json (the hub
vectors), data/sp800-53r5_catalog.json (statements), runners/sp800-53-to-csf2/reference_semantics.json (the pinned
NIST informative references from item 1: control-level entries with csf_element, target_oscal_id, csf_element_status,
granularity; the weak labels), runners/sp800-53-to-csf2/config.frozen.json (what calibrate froze: c_min, the prompt
versions, the chosen revision, its sha256 and the protocol hash it ran under; evaluate reads it), runners/sp800-53-to-csf2/providers.py,
engine.py, store.py, retrieve.py.

Stage identities (what a stored output is keyed by):
  extraction  unit release, attempt, prompt version
  retrieval   unit release, retriever, index hash, hub revision, k
  proposal    unit release, extraction output, hub entry and revision, prompt version
  vote        proposal output, voter model, quoted spans, confidence threshold, prompt version
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")
CALIBRATION = os.environ.get("GSTACK_CALIBRATION") or os.path.join(HERE, "calibration.jsonl")
CONFIG = os.environ.get("GSTACK_CONFIG") or os.path.join(HERE, "config.frozen.json")
PROGRESS = CALIBRATION + ".progress"      # the revision in flight and its ledger position before its first call


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


engine, providers, retrieve, store = load("engine"), load("providers"), load("retrieve"), load("store")
cards = engine.cards

# The calibration schedule: the paper's threshold first, then the sweep, then the second wording at the
# threshold the sweep chose. Six entries, the protocol's cap. ponytail: a fixed schedule, not a search.
SCHEDULE = [("propose-v1", 0.7), ("propose-v1", 0.5), ("propose-v1", 0.6), ("propose-v1", 0.8), ("propose-v1", 0.9),
            ("propose-v2", None)]


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def oid(identity):
    """A stored output's id: one row per identity."""
    return hashlib.sha256(identity["id"].encode()).hexdigest()[:16]


class Catalog:
    """SP 800-53 units read once: statement text, kind, family, title, withdrawn."""

    def __init__(self, path=os.path.join(DATA, "sp800-53r5_catalog.json")):
        with open(path, "rb") as f:
            raw = f.read()
        self.sha = hashlib.sha256(raw).hexdigest()
        cat = json.loads(raw)["catalog"]
        self.units = {}

        def prose(parts):
            out = []
            for p in parts or []:
                if p.get("prose"):
                    out.append(p["prose"])
                out += prose(p.get("parts"))
            return out

        for g in cat["groups"]:
            for c in g.get("controls", []):
                for u, kind in [(c, "base")] + [(e, "enhancement") for e in c.get("controls", [])]:
                    withdrawn = any(p.get("name") == "status" and p.get("value") == "withdrawn" for p in u.get("props", []))
                    self.units[u["id"]] = {
                        "id": u["id"], "kind": kind, "family": c["id"], "group": g["id"], "title": u.get("title", ""),
                        "base_title": c.get("title", ""), "type": "withdrawn" if withdrawn else kind,
                        "text": " ".join(prose([p for p in u.get("parts", []) if p.get("name") == "statement"]))}

    def release(self, unit_id):
        return f"{unit_id}@{self.sha[:12]}"


def protocol_hash():
    with open(os.path.join(HERE, "protocol.sha256"), encoding="utf-8") as f:
        return f.read().split()[0]


def oscal_id(target):
    m = re.match(r"([A-Za-z]{2})-0*(\d+)(?:\((\d+)\))?", target)
    return f"{m.group(1).lower()}-{int(m.group(2))}" + (f".{int(m.group(3))}" if m.group(3) else "")


def references():
    """Control-level NIST links from reference_semantics.json, by OSCAL unit id: {unit: {subcategory, ...}}."""
    with open(os.path.join(HERE, "reference_semantics.json"), encoding="utf-8") as f:
        entries = json.load(f)["entries"]
    refs = {}
    for e in entries:
        if e.get("granularity") != "control" or e.get("csf_element_status") != "active" or e.get("csf_kind") != "subcategory":
            continue                       # 12 control-level links point at a category, which no candidate can be
        refs.setdefault(e.get("target_oscal_id") or oscal_id(e["target"]), set()).add(e["csf_element"])
    return refs


class Run:
    def __init__(self, protocol, adapter, db_path=STORE, index_path=None):
        self.protocol, self.adapter = protocol, adapter
        self.catalog = Catalog()
        store.init(db_path)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with open(index_path or retrieve.INDEX, encoding="utf-8") as f:
            self.index = json.load(f)
        self.hub_text = {s["id"]: s["text"] for s in retrieve.active_subcategories()}
        self.roles = protocol["roles"]
        self.proposer = self.roles["proposal"]["model"]
        self.triad = self.roles["vote"]["models"]
        self.tally = {"asked": 0, "reused": 0}
        self.lock = threading.Lock()

    # ---- store helpers ------------------------------------------------------------------------
    def row(self, table, output_id):
        with self.lock:
            r = self.db.execute(f"SELECT * FROM {table} WHERE output_id=?", (output_id,)).fetchone()
        return dict(r) if r else None

    def put(self, table, **cols):
        with self.lock:
            with self.db:
                self.db.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                list(cols.values()))

    def count(self, what):
        with self.lock:
            self.tally[what] += 1

    def unit_row(self, u, split):
        self.put("units", unit_release=self.catalog.release(u["id"]), unit_id=u["id"], catalog_sha256=self.catalog.sha,
                 family=u["family"], grp=u["group"], kind=u["kind"], split=split, statement=u["text"],
                 in_scope=int(u["type"] != "withdrawn" and bool(u["text"])), scope_reason=None)

    # ---- stages ---------------------------------------------------------------------------------
    def extract(self, u, attempt):
        rel = self.catalog.release(u["id"])
        q = providers.identity("extraction", unit_release=rel, attempt=attempt, prompt_version="extract-v1")
        out_id = oid(q)
        r = self.row("extractions", out_id)
        if r:
            self.count("reused")
            acts = json.loads(r["activities"])
            return {"complete": True, "work_object": bool(acts), "attempt_number": attempt + 1, "activities": acts}, out_id
        self.count("asked")
        out = self.adapter.call("extraction", self.roles["extraction"]["model"], "extract-v1",
                                providers.render("extraction", unit_id=u["id"], unit_text=u["text"]), q, None,
                                unit_release=rel, attempt=attempt)
        acts = out.get("activities") if isinstance(out, dict) else None
        complete = isinstance(acts, list)
        acts = [str(a) for a in acts] if complete else []
        if complete:
            self.put("extractions", output_id=out_id, unit_release=rel, attempt=attempt, identity=q["id"],
                     activities=json.dumps(acts), quotes=json.dumps(out.get("quotes") or []), prompt_version="extract-v1",
                     created=now())
        return {"complete": complete, "work_object": bool(acts), "attempt_number": attempt + 1, "activities": acts}, out_id

    def retrieve_cands(self, u, ext_id):
        rel = self.catalog.release(u["id"])
        ix = self.index
        q = providers.identity("retrieval", unit_release=rel, retriever=ix["retriever"], index=ix["vectors_sha256"],
                               hub_revision=ix["hub_revision"], k=ix["k"])
        out_id = oid(q)
        r = self.row("retrievals", out_id)
        if r:
            self.count("reused")
            return json.loads(r["candidates"]), out_id
        self.count("asked")
        v = self.adapter.embed(ix["model"], u["text"], q, unit_release=rel)
        scored = sorted(((retrieve.cosine(v, h["vector"]), h["id"]) for h in ix["rows"]), reverse=True)[: ix["k"]]
        cands = [sid for _, sid in scored]
        self.put("retrievals", output_id=out_id, unit_release=rel, extraction_output_id=ext_id, retriever=ix["retriever"],
                 index_version=ix["vectors_sha256"], hub_revision=ix["hub_revision"], route_params=json.dumps({"k": ix["k"]}),
                 identity=q["id"], candidates=json.dumps(cands), created=now())
        return cands, out_id

    def propose(self, u, ext, ext_id, cand, prompt_version):
        rel = self.catalog.release(u["id"])
        q = providers.identity("proposal", unit_release=rel, extraction_output_id=ext_id, hub_entry=cand,
                               hub_revision=self.index["hub_revision"], prompt_version=prompt_version)
        out_id = oid(q)
        r = self.row("proposals", out_id)
        if r:
            self.count("reused")
            quotes = json.loads(r["quotes"])
            return {"level": r["level"], "quotes": quotes, "confidence": r["confidence"],
                    "conflict": r["level"] == cards.NULL and bool(quotes)}, out_id
        self.count("asked")
        prompt = providers.PROMPTS[prompt_version].format(
            unit_id=u["id"], unit_text=u["text"], activities="; ".join(ext["activities"]), hub_id=cand,
            hub_text=self.hub_text[cand], levels=", ".join(cards.LEVELS), null=cards.NULL)
        out = self.adapter.call("proposal", self.proposer, prompt_version, prompt, q,
                                providers.identity("evidence", extraction=ext_id), unit_release=rel, hub_entry=cand)
        level = out.get("level") if isinstance(out, dict) else None
        level = level if level in cards.LEVELS else cards.NULL
        quotes = [str(x) for x in (out.get("quotes") if isinstance(out, dict) and isinstance(out.get("quotes"), list) else [])]
        try:
            conf = float(out.get("confidence")) if isinstance(out, dict) else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        self.put("proposals", output_id=out_id, unit_release=rel, extraction_output_id=ext_id, hub_entry=cand,
                 hub_revision=self.index["hub_revision"], identity=q["id"], level=level, quotes=json.dumps(quotes),
                 confidence=conf, prompt_version=prompt_version, created=now())
        return {"level": level, "quotes": quotes, "confidence": conf, "conflict": level == cards.NULL and bool(quotes)}, out_id

    def vote(self, u, cand, prop, prop_id, voter, c_min, revision):
        spans = hashlib.sha256(json.dumps(prop["quotes"]).encode()).hexdigest()[:16]
        q = providers.identity("vote", proposal_output_id=prop_id, voter=voter, quoted_spans=spans, c_min=c_min,
                               prompt_version="vote-v1")
        out_id = oid(q)
        provider = self.adapter.family(voter)          # the seat: the model's maker, whoever bills the call
        r = self.row("votes", out_id)
        if r:
            self.count("reused")
            return {"provider": provider, "answer": r["vote"], "model_id": voter}
        self.count("asked")
        out = self.adapter.call("vote", voter, "vote-v1",
                                providers.render("vote", unit_id=u["id"], hub_id=cand, level=prop["level"], quotes=prop["quotes"],
                                                 unit_text=u["text"], hub_text=self.hub_text[cand],
                                                 levels=", ".join(cards.LEVELS), null=cards.NULL),
                                q, providers.identity("evidence", proposal=prop_id, spans=spans),
                                unit_release=self.catalog.release(u["id"]), hub_entry=cand, revision=revision)
        answer = out.get("vote") if isinstance(out, dict) else None
        answer = answer if answer in cards.LEVELS or answer == cards.NULL else "off_scale"
        conf = out.get("confidence") if isinstance(out, dict) else None
        self.put("votes", output_id=out_id, proposal_output_id=prop_id, voter=voter, evidence_identity=spans,
                 quoted_spans=json.dumps(prop["quotes"]), vote_params=json.dumps({"c_min": c_min}), identity=q["id"],
                 vote=answer, quotes_verbatim=int(bool(out.get("quotes_verbatim"))) if isinstance(out, dict) else None,
                 confidence=conf if isinstance(conf, (int, float)) else None, prompt_version="vote-v1", revision=revision,
                 created=now())
        return {"provider": provider, "answer": answer, "model_id": voter}

    # ---- one unit under one set -------------------------------------------------------------------
    def unit(self, u, S, prompt_version):
        """Every decision for a unit: the engine walks the stored outputs; this asks only what the walk needs."""
        unit = {"id": u["id"], "type": u["type"], "text": u["text"], "family": u["family"]}
        ext, ext_id, attempt = {}, None, 0
        if u["type"] != "withdrawn":
            while True:                                   # I3/I4: bounded by G_max
                ext, ext_id = self.extract(u, attempt)
                if ext["complete"] or attempt + 1 > S["params"]["G_max"]:
                    break
                attempt += 1
        base = {"unit": unit, "extraction": ext, "operational_ruling": None}
        probe = engine.run_case({**base, "retrieval": {"candidate_ids": []}}, S)
        if probe["card_id"] in ("I1", "I4", "SS-scope"):
            return [probe]
        cands, _ = self.retrieve_cands(u, ext_id)
        if not cands:
            return [engine.run_case({**base, "retrieval": {"candidate_ids": []}}, S)]
        out = []
        for cand in cands:
            prop, prop_id = self.propose(u, ext, ext_id, cand, prompt_version)
            case = {**base, "candidate": {"id": cand}, "retrieval": {"candidate_ids": cands}, "proposal": prop, "votes": []}
            probe = engine.run_case(case, S)
            if probe["card_id"] == "T1":                  # the evidence cards let it through: ask the triad
                case["votes"] = [self.vote(u, cand, prop, prop_id, m, S["params"]["c_min"], S["rev"]) for m in self.triad]
            out.append(engine.run_case(case, S))
        return out

    def run_split(self, units, S, prompt_version, workers):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda u: self.unit(u, S, prompt_version), units))
        decisions = [d for ds in results for d in ds]
        for d in decisions:
            d["unit_release"] = self.catalog.release(d["unit_id"])
        with self.lock:
            with self.db:                    # a unit's decision set under a revision is replaced whole, never row by row
                for u in units:
                    self.db.execute("DELETE FROM decisions WHERE unit_id=? AND revision=?", (u["id"], S["rev"]))
                for d in decisions:
                    self.db.execute("INSERT OR REPLACE INTO decisions (decision_id, unit_id, unit_release, hub_entry, route, reason_code, "
                                    "level, card_id, writer_kind, ruling_output_id, revision, trace, superseded_by, created) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (d["decision_id"], d["unit_id"], d["unit_release"], d["candidate_id"], d["route"], d["reason"],
                                     d["level"], d["card_id"], d["writer_kind"], None, d["set_revision"], json.dumps(d["trace"]),
                                     None, now()))
        return decisions


def reconcile(db_path=STORE):
    """Drop a unit's terminal row (no candidate) under a revision that also holds its candidate rows: the leftover of an
    attempt that stopped at intake before a later attempt went on (review 20261117-100049 F2). Returns what was removed."""
    con = sqlite3.connect(db_path)
    stale = con.execute("SELECT d.decision_id, d.unit_id, d.revision, d.card_id FROM decisions d WHERE d.hub_entry IS NULL AND EXISTS "
                        "(SELECT 1 FROM decisions o WHERE o.unit_id=d.unit_id AND o.revision=d.revision AND o.hub_entry IS NOT NULL)").fetchall()
    with con:
        con.executemany("DELETE FROM decisions WHERE decision_id=?", [(s[0],) for s in stale])
    con.close()
    return [{"decision_id": s[0], "unit_id": s[1], "revision": s[2], "card_id": s[3]} for s in stale]


def weak_score(decisions, units, refs):
    """Link versus no link against the pinned references. A unit is scored when it, or its base control, has a
    control-level reference (an enhancement inherits its base control's links, counted as inherited); a scored
    unit's unreferenced candidates are its weak negatives. A link is predicted when the triad's plurality is a
    level: what an open gate would write (T3), or what a closed one sent to review (T3-gated)."""
    linked = {}
    for u in units:
        own, inherited = refs.get(u["id"]), refs.get(u["family"])
        if own or inherited:
            linked[u["id"]] = (own or inherited, "own" if own else "inherited")
    tp = fp = fn = 0
    for d in decisions:
        if d["unit_id"] not in linked or d["candidate_id"] is None:
            continue
        truth = d["candidate_id"] in linked[d["unit_id"]][0]
        pred = d["card_id"] in ("T3", "T3-gated")
        tp += truth and pred
        fp += pred and not truth
        fn += truth and not pred
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"scored_units": len(linked), "inherited": sum(1 for v in linked.values() if v[1] == "inherited"),
            "unscored_units": len(units) - len(linked), "tp": tp, "fp": fp, "fn": fn,
            "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def calibrate(args):
    protocol = retrieve.protocol_checked()
    adapter = providers.Adapter(protocol)
    run = Run(protocol, adapter)
    refs = references()
    sample = [u for u in protocol["sample"]["units"] if u["split"] == "calibration"]
    if args.units:
        sample = sample[: args.units]
    units = [run.catalog.units[u["id"]] for u in sample]
    for u in units:
        run.unit_row(u, "calibration")
    unit_ids = [u["id"] for u in units]
    if os.path.isfile(CONFIG):
        print(f"calibration already frozen: {CONFIG} exists; remove it and calibration.jsonl to calibrate again", file=sys.stderr)
        sys.exit(2)
    rows, best = [], None
    if os.path.isfile(CALIBRATION):                      # resume: keep the recorded revisions, continue the schedule
        with open(CALIBRATION, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        for r in rows:
            key = (round(r["score"]["f1"], 6), r["c_min"])
            if best is None or key > best["key"]:
                best = {"key": key, "c_min": r["c_min"], "prompt_version": r["prompt_version"], "revision": r["revision"], "score": r["score"]}
        if rows:
            print(f"resuming after {len(rows)} recorded revision(s): {', '.join(r['revision'] for r in rows)}")
    for n, (prompt_version, c_min) in enumerate(SCHEDULE[: args.revisions], 1):
        if n <= len(rows):
            continue
        if c_min is None:
            c_min = best["c_min"]
        rev = f"cal-{n}"
        S = cards.make(rev, run.proposer, run.triad, c_min=c_min, qv={"propose": prompt_version})
        before, by_before = adapter.spend()
        progress = {}
        if os.path.isfile(PROGRESS):               # a revision interrupted after paid calls: its baseline, not a fresh one
            with open(PROGRESS, encoding="utf-8") as f:
                progress = json.load(f)
        if progress.get("revision") == rev:
            before = Decimal(progress["spend_before"])
            by_before = {k: Decimal(v) for k, v in progress["by_provider_before"].items()}
            print(f"{rev}: resuming an interrupted revision; cost counted from its recorded baseline {providers.money(before)} USD")
        else:
            with open(PROGRESS, "w", encoding="utf-8") as f:
                json.dump({"revision": rev, "prompt_version": prompt_version, "c_min": c_min, "spend_before": providers.money(before),
                           "by_provider_before": {k: providers.money(v) for k, v in by_before.items()}, "started": now()}, f)
        run.tally = {"asked": 0, "reused": 0}
        t0 = time.time()
        decisions = run.run_split(units, S, prompt_version, args.workers)
        after, by_after = adapter.spend()
        score = weak_score(decisions, units, refs)
        row = {"revision": rev, "prompt_version": prompt_version, "c_min": c_min, "units": len(units), "unit_ids": unit_ids,
               "split": "calibration", "decisions": len(decisions), "asked": run.tally["asked"], "reused": run.tally["reused"],
               "cost_usd": providers.money(after - before),
               "cost_by_provider_usd": {k: providers.money(by_after.get(k, Decimal(0)) - by_before.get(k, Decimal(0)))
                                        for k in sorted(by_after)},
               "seconds": round(time.time() - t0, 1), "score": score, "engine": S["engine"],
               "routes": {k: sum(1 for d in decisions if d["reason"] == k) for k in sorted({d["reason"] for d in decisions})},
               "ts": now()}
        rows.append(row)
        with open(CALIBRATION, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        os.remove(PROGRESS)
        print(f"{rev}: {prompt_version} c_min {c_min}  cost {row['cost_usd']} USD  asked {row['asked']} reused {row['reused']}  "
              f"f1 {score['f1']:.3f} (p {score['precision']:.3f} r {score['recall']:.3f}; tp {score['tp']} fp {score['fp']} fn {score['fn']}; "
              f"{score['scored_units']} scored units)  {row['seconds']}s")
        key = (round(score["f1"], 6), c_min)
        if best is None or key > best["key"]:
            best = {"key": key, "c_min": c_min, "prompt_version": prompt_version, "revision": rev, "score": score}
    config = {"c_min": best["c_min"], "prompt_versions": {"extraction": "extract-v1", "proposal": best["prompt_version"], "vote": "vote-v1"},
              "chosen_revision": best["revision"], "score": best["score"], "revisions": [r["revision"] for r in rows],
              "k": protocol["budget"]["k"], "engine": cards.engine_hash(),
              "protocol_sha256": protocol_hash(), "frozen": now()}
    raw = json.dumps(config, indent=1, sort_keys=True).encode()
    config["sha256"] = hashlib.sha256(raw).hexdigest()
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=1, sort_keys=True)
        f.write("\n")
    total = sum(Decimal(r["cost_usd"]) for r in rows)
    print(f"chosen: {best['prompt_version']} c_min {best['c_min']} ({best['revision']}, f1 {best['score']['f1']:.3f}); "
          f"calibration spend {providers.money(total)} USD over {len(rows)} revisions; config.frozen.json sha256 {config['sha256'][:16]}")


def frozen_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def frozen_set(rev, run, config):
    """The frozen configuration as a card set: every gate closed, c_min and the proposal wording calibration chose."""
    return cards.make(rev, run.proposer, run.triad, c_min=config["c_min"], qv={"propose": config["prompt_versions"]["proposal"]})


def write_set(rev, S, config, extra=None):
    rec = {"revision": rev, "gates": {g: "closed" for g in cards.GATES}, "engine": S["engine"], "params": S["params"], "qv": S["qv"],
           "proposer": S["proposer"], "triad": S["triad"], "config_sha256": config["sha256"], "protocol_sha256": protocol_hash(),
           "frozen": now(), **(extra or {})}
    raw = json.dumps(rec, indent=1, sort_keys=True).encode()
    rec["sha256"] = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(HERE, f"set_{rev}.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
        f.write("\n")
    return rec


def evaluate(args, split="evaluation", rev="r2"):
    protocol = retrieve.protocol_checked()
    config = frozen_config()
    adapter = providers.Adapter(protocol)
    run = Run(protocol, adapter)
    sample = [u for u in protocol["sample"]["units"] if u["split"] == split]
    units = [run.catalog.units[u["id"]] for u in sample]
    for u in units:
        run.unit_row(u, split)
    S = frozen_set(rev, run, config)
    rec = write_set(rev, S, config, {"split": split, "units": len(units)})
    before, _ = adapter.spend()
    run.tally = {"asked": 0, "reused": 0}
    t0 = time.time()
    decisions = run.run_split(units, S, config["prompt_versions"]["proposal"], args.workers)
    after, _ = adapter.spend()
    routes = {k: sum(1 for d in decisions if d["reason"] == k) for k in sorted({d["reason"] for d in decisions})}
    print(f"{rev} {split}: {len(units)} units, {len(decisions)} decisions, asked {run.tally['asked']} reused {run.tally['reused']}, "
          f"cost {providers.money(after - before)} USD, {round(time.time() - t0, 1)}s; routes {routes}; set_{rev}.json sha256 {rec['sha256'][:16]}")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["calibrate", "reconcile", "evaluate"])
    ap.add_argument("--units", type=int, default=0)
    ap.add_argument("--revisions", type=int, default=len(SCHEDULE))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(argv)
    if args.command == "evaluate":
        return providers.run_pass(lambda: evaluate(args))
    if args.command == "reconcile":
        removed = reconcile()
        print(f"reconcile: removed {len(removed)} stale terminal row(s)" + (": " + ", ".join(f"{r['unit_id']}@{r['revision']} {r['card_id']}" for r in removed) if removed else ""))
        return 0
    return providers.run_pass(lambda: calibrate(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
