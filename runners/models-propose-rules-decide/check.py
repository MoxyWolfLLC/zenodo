"""Runs every check, prints a PASS/FAIL table, exits 1 on any FAIL. Run with: python3 -I check.py"""
import itertools
import json
import math
import os
import re
import sys
import traceback
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # -I drops the script dir from sys.path
import cards  # noqa: E402
import runner  # noqa: E402

SPEC = os.path.join(HERE, "method-spec-r2.md")
FX = json.load(open(os.path.join(HERE, "fixtures.json")))
R = runner.Runner(FX)
NEG = {"out_of_scope_rule", "out_of_scope_content", "no_match_nothing_retrieved", "no_match_hub_declined",
       "no_match_voted", "no_match_ruled", "out_of_scope_ruled"}
CALLERS = ("model", "reviewer", "curator", "retriever")
RESULTS, CTX = [], {}


MODEL_KINDS = ("extraction", "proposal", "triad", "retrieval")


def split(t):
    m = sum(t.get(f"{k}:asked", 0) for k in MODEL_KINDS)
    h = sum(t.get(f"{k}:asked", 0) for k in ("ruling", "curator"))
    return f"model/retriever outputs asked {m}, review/hub items re-queued {h}", m


def rows(where="1", args=()):
    return [dict(r) for r in R.db.execute(f"SELECT * FROM decisions WHERE {where} ORDER BY id", args)]


def payload(oid):
    return json.loads(R.db.execute("SELECT payload FROM outputs WHERE id=?", (oid,)).fetchone()[0])


def hits(row):
    return {c[1] for c in json.loads(row["trace"])["cards"] if c[3]}


def check(fn):
    out = []
    try:
        ok, n = fn(out.append)
    except Exception:
        ok, n = False, 0
        out += traceback.format_exc().strip().splitlines()[-3:]
    ok = bool(ok) and n > 0  # a check over zero items cannot pass
    RESULTS.append((fn.__name__, "PASS" if ok else "FAIL", n))
    print(f"\n[{'PASS' if ok else 'FAIL'}] {fn.__name__}  (examined {n})")
    for line in out:
        print("    " + line)


# ---- scenario: initial publication under r1, then the fixture challenge -------------------------
R.activate("r1", "operator:initial-load", None)
CTX["first_pass"] = dict(R.calls)
for c in FX["challenges"]:
    R.challenge(c["unit"], c["cand"], c["code"], c["by"])
R.db.commit()


def route_coverage(p):
    codes = set(re.findall(r"`([a-z_]+)`", open(SPEC).read()))
    allr = rows()
    card_hits = set().union(*(hits(r) for r in allr))
    reasons = {r["reason"] for r in allr} | {r[0] for r in R.db.execute("SELECT reason FROM rulings")}
    GATED = {"T2-gated", "T3-gated"}  # reachable only with a closed gate: check acceptance_gate
    need_cards = [c[0] for s in cards.SETS["r1"]["cards"].values() for c in s if c[0] not in GATED | {"T5-otherwise"}]
    need_reasons = sorted((codes - {"no_majority", "nomatch_gate_closed", "level_gate_closed"}) | {"out_of_scope_ruled"})
    pol = lambda r: "neg" if payload(json.loads(r["basis"])[0]["out"]).get("answer") is None else "pos"
    special = {
        "E3 with a NEGATIVE proposal": any(r["card_id"] == "E3" and pol(r) == "neg" for r in allr),
        "E4 with a positive proposal": any("E4-otherwise" in hits(r) and pol(r) == "pos" for r in allr if r["candidate"]),
        "E4 with a negative proposal": any("E4-otherwise" in hits(r) and pol(r) == "neg" for r in allr if r["candidate"]),
        "E1 approve then reopen at R0 (new candidate set)": any(n > 1 for n, in R.db.execute(
            "SELECT COUNT(*) FROM candidate_sets GROUP BY unit_id")),
        "SS-scope audit sample queued": bool(R.db.execute("SELECT 1 FROM rulings WHERE reason='audit_scope'").fetchone()),
    }
    miss = [c for c in need_cards if c not in card_hits] + [r for r in need_reasons if r not in reasons] + \
           [k for k, v in special.items() if not v]
    p(f"spec reason codes found in spec text: {len(codes)}; T5/no_majority deferred to check 3, gated cards to acceptance_gate")
    p(f"cards required {len(need_cards)}, reasons required {len(need_reasons)}, special paths {len(special)}; "
      f"decision rows scanned {len(allr)}")
    p("unreached: " + (", ".join(miss) or "none"))
    return not miss and codes, len(need_cards) + len(need_reasons) + len(special)


def termination(p):
    live, bad, n = R.live(), [], 0
    G, Hm = cards.BASE["G_max"], cards.BASE["H_max"]
    for uid in R.unit_ids:
        mine = [k for k in live if k[0] == uid]
        cs = R.db.execute("SELECT candidates FROM candidate_sets WHERE unit_id=? ORDER BY id DESC", (uid,)).fetchone()
        if (uid, None) in live:
            ok = len(mine) == 1
        else:
            ok = cs and sorted(k[1] for k in mine) == sorted(json.loads(cs[0]))
        bad += [] if ok else [uid]
        n += 1
    att = [json.loads(r["trace"])["attempts"] for r in rows()]
    over = [a for a in att if a.get("gap", 0) > G or a.get("hub", 0) > Hm]
    p(f"units {n}, live decisions {len(live)}; units without exactly one live decision per candidate/unit: {bad or 'none'}")
    p(f"max gap_attempts seen {max(a.get('gap', 0) for a in att)} (G_max {G}), "
      f"max hub_attempts seen {max(a.get('hub', 0) for a in att)} (H_max {Hm}); rows over bound: {len(over)}")
    return not bad and not over, n + len(att)


def otherwise_last_and_exhaustive(p):
    n, bad = 0, []
    for rev, S in cards.SETS.items():
        for name, cs in S["cards"].items():
            n += 1
            if not (cs[-1][0].endswith("otherwise") and cs[-1][2]({}, {}) is True):
                bad.append(f"{rev}.{name}")
    vals = [{"answer": None}] + [{"answer": lv} for lv in cards.LEVELS] + [{"answer": "maybe"}]
    land, t5_valid = Counter(), 0
    for combo in itertools.product(vals, repeat=3):
        trace = []
        cid, _ = R.pick(cards.SETS["r1"], "triad", {"votes": list(combo), "n": 3}, trace)
        land[cid] += 1
        assert sum(t[3] for t in trace) == 1 and trace[-1][1] == cid  # stops at exactly one card
        t5_valid += cid == "T5-otherwise" and all(map(cards.valid, combo))
    p(f"card sets checked {n} ({len(cards.SETS)} set revisions); not ending in otherwise: {bad or 'none'}")
    p(f"triad enumeration over 6^3 = {sum(land.values())} vote combos: " +
      ", ".join(f"{k} {land[k]}" for k in ("T1", "T2", "T2-gated", "T3", "T3-gated", "T4", "T5-otherwise")))
    p(f"T5 hit with all three votes valid: {t5_valid}")
    return not bad and sum(land.values()) == 216 and t5_valid == 0, n + 216


def no_single_source_negative(p):
    viol, n = [], 0
    for r in rows("route='RECORD'"):
        if r["reason"] not in NEG:
            continue
        n += 1
        b = json.loads(r["basis"])
        human = any(x["kind"] == "human" for x in b)
        models = {x["who"] for x in b if x["kind"] in ("model", "retriever")}
        if not human and len(models) == 1 and not r["card_id"].startswith("SS-"):
            viol.append(f"{r['unit_id']}/{r['candidate']} {r['reason']} via {r['card_id']}")
    p(f"terminal negative rows examined {n}; single-source negatives outside an SS- card: {viol or 'none'}")
    vol = Counter(r["card_id"] for r in rows("card_id LIKE 'SS-%' AND batch=1"))
    aud = [dict(a) for a in R.db.execute("SELECT * FROM rulings WHERE reason='audit_scope'")]
    err = sum(a["ruling"] != "out_of_scope" for a in aud)
    p(f"SS-scope: volume {vol['SS-scope']}, audit sample {len(aud)} (rate {cards.BASE['a_scope']}), "
      f"audited errors {err}, audited error rate {err / len(aud):.2f}")
    r1 = rows("card_id='SS-retrieval' AND batch=1")
    hub_n = R.db.execute("SELECT COUNT(*) FROM hub WHERE revision<=1").fetchone()[0]
    missed = sum(1 for r in r1 for k, v in FX["gold"].items() if k.startswith(r["unit_id"] + "|") and v)
    p(f"SS-retrieval: volume {vol['SS-retrieval']}, audit sample {len(r1) * hub_n} non-retrieved (unit, hub entry) "
      f"pairs, missed links {missed}, audited error rate {missed / max(1, len(r1) * hub_n):.3f}")
    return not viol and aud and r1, n


def replay_zero_calls(p):
    c0, live, n, diff = Counter(R.calls), R.live(), 0, []
    R.tally.clear()
    for uid in R.unit_ids:
        rev = next(v["set_revision"] for k, v in live.items() if k[0] == uid)
        try:
            chains = R.run_unit(uid, cards.SETS[rev], "replay")
        except runner.ReplayDefect as e:
            diff.append(f"{uid}: {e}")
            continue
        for ch in chains:
            f, lv = ch[-1], live[(uid, ch[-1]["cand"])]
            n += 1
            if (f["route"], f["reason"], f["level"], f["card"]) != (lv["route"], lv["reason"], lv["level"], lv["card_id"]):
                diff.append(f"{uid}/{f['cand']}")
    delta = {k: R.calls[k] - c0[k] for k in CALLERS}
    asked = sum(v for k, v in R.tally.items() if k.endswith(":asked"))
    p(f"live decisions replayed {n}; route/level/reason/card mismatches or replay defects: {diff or 'none'}")
    p(f"call counter deltas during replay: {delta}; outputs asked {asked}; "
      f"outputs reused {sum(v for k, v in R.tally.items() if k.endswith(':reused'))}")
    return not diff and not any(delta.values()) and asked == 0 and n == len(live), n


def policy_eval_reuse(p):
    b1 = rows("batch=1")
    r1, r2 = cards.SETS["r1"]["params"]["c_min"], cards.SETS["r2"]["params"]["c_min"]
    moved = 0
    for r in b1:
        if r["card_id"] == "E3":
            pr = payload(json.loads(r["basis"])[0]["out"])
            u = R.unit(r["unit_id"])
            moved += r2 <= pr["confidence"] < r1 and cards.quotes_ok(pr, u) and not pr.get("conflict")
    triad_pairs = {(r["unit_id"], r["candidate"]) for r in b1 if "E4-otherwise" in hits(r)}
    a = CTX["a"] = R.evaluate("r2")
    b = CTX["b"] = R.evaluate("r3")
    t = lambda rep, k: rep["tally"].get(k, 0)
    for name, rep in (("(a) c_min 0.7 -> 0.5, set r2", a), ("(b) triad question tq1 -> tq2, set r3", b)):
        p(f"{name}: decisions changed {rep['changed']}, model calls {rep['calls']['model']}, "
          f"reviewer calls {rep['calls']['reviewer']}, curator calls {rep['calls']['curator']}")
        p("    " + ", ".join(f"{k} {v}" for k, v in rep["tally"].items()))
    p(f"(a) expected newly asked triad votes = 3 x {moved} candidates moved E3 -> triad = {3 * moved}")
    p(f"(b) expected re-asked triad votes = 3 x {len(triad_pairs)} candidates reaching the triad = {3 * len(triad_pairs)}")
    ok_a = (t(a, "triad:asked") == 3 * moved > 0 and t(a, "proposal:asked") == 0 and t(a, "proposal:reused") > 0
            and t(a, "extraction:asked") == 0 and t(a, "retrieval:asked") == 0)
    ok_b = (t(b, "triad:asked") == 3 * len(triad_pairs) and t(b, "triad:reused") == 0
            and t(b, "proposal:asked") == 0 and t(b, "proposal:reused") > 0)
    return ok_a and ok_b, t(a, "triad:asked") + t(a, "triad:reused") + t(b, "triad:asked") + t(a, "proposal:reused")


def activation(p):
    before = R.live()
    asked = R.activate("r2", "approver:d.c", CTX["a"]["hash"])
    after = R.live()
    act = R.db.execute("SELECT * FROM activations WHERE set_revision='r2' AND report_hash=?",
                       (CTX["a"]["hash"],)).fetchone()
    old_sup = all(R.row("decisions", v["id"])["superseded_by"] for v in before.values())
    p(f"live decisions before {len(before)}, after {len(after)}; all now under r2: "
      f"{all(v['set_revision'] == 'r2' for v in after.values())}; all prior live rows marked superseded: {old_sup}")
    p(f"activation record: who={act['who'] if act else None}, report_hash={act['report_hash'] if act else None}")
    txt, m = split(asked)
    p(f"during activation: {txt} (evaluation already paid for the votes), "
      f"reused {sum(v for k, v in asked.items() if k.endswith(':reused'))}")
    p(f"decisions that changed vs r1: {sum(1 for k in after if (after[k]['reason'], after[k]['level']) != (before.get(k, {}).get('reason'), before.get(k, {}).get('level')))}")
    return (m == 0 and act and old_sup and all(v["set_revision"] == "r2" for v in after.values()) and set(after) == set(before)), len(before)


def undo_publication(p):
    asked = R.activate("r1", "approver:d.c", "undo:reactivate-r1")
    live, c0, diff, n = R.live(), Counter(R.calls), [], 0
    for uid in R.unit_ids:
        for ch in R.run_unit(uid, cards.SETS["r1"], "replay"):
            f, lv = ch[-1], live.get((uid, ch[-1]["cand"]))
            n += 1
            if not lv or (f["route"], f["reason"], f["level"]) != (lv["route"], lv["reason"], lv["level"]):
                diff.append(f"{uid}/{f['cand']}")
    delta = {k: R.calls[k] - c0[k] for k in CALLERS}
    txt, m = split(asked)
    p(f"re-activated r1; during re-activation: {txt}")
    p(f"public reads {len(live)} vs fresh replay of r1 {n}; differences: {diff or 'none'}; replay call deltas {delta}")
    return m == 0 and not diff and n == len(live) and not any(delta.values()), n


TABLES = ["units", "hub", "index_versions", "outputs", "candidate_sets", "decisions", "rulings",
          "curator_decisions", "activations"]


def snapshot():
    return {(t, r["id"]): dict(r) for t in TABLES for r in R.db.execute(f"SELECT * FROM {t}")}


def undo_recovery(p):
    pre = snapshot()
    targets = [v["id"] for v in R.live().values() if v["reason"] == "auto_accept"][:5]
    R.op = "bad-write-001"
    for rid in targets:
        R.update("decisions", rid, level="CORRUPT")
    mid = snapshot()
    fp = {(w[0], w[1]) for w in R.db.execute("SELECT tbl, row_id FROM writes WHERE op='bad-write-001'")}
    outside_bad = [k for k in pre if k not in fp and pre[k] != mid.get(k)]
    restored = R.recover("bad-write-001")
    post = snapshot()
    back = all(post[("decisions", rid)] == pre[("decisions", rid)] for rid in targets)
    others = [k for k in set(pre) | set(post) if k not in fp and pre.get(k) != post.get(k)]
    p(f"bad write corrupted level on {len(targets)} decisions; footprint rows {len(fp)}; "
      f"rows outside footprint changed by the bad write {len(outside_bad)}")
    p(f"recovery restored {len(restored)} footprint rows; footprint rows equal pre-write values: {back}; "
      f"rows outside footprint changed (of {len(pre)} snapshotted): {len(others)}")
    return len(targets) == 5 and fp == {("decisions", t) for t in targets} and back and not others and not outside_bad, len(pre)


def challenge(p):
    c = FX["challenges"][0]
    hist = rows("unit_id=? AND candidate=? AND batch=1", (c["unit"], c["cand"]))
    aa = [h for h in hist if h["reason"] == "auto_accept"]
    ch = [h for h in hist if h["reason"] == "challenge"]
    ru = [h for h in hist if h["reason"] == "no_match_ruled"]
    ok = (len(aa) == len(ch) == len(ru) == 1 and aa[0]["superseded_by"] == ch[0]["id"]
          and ch[0]["superseded_by"] == ru[0]["id"])
    rul = R.db.execute("SELECT * FROM rulings WHERE kind='challenge'").fetchone()
    live = R.live()[(c["unit"], c["cand"])]
    p(f"challenged {c['unit']}/{c['cand']} (applied in setup); rows in ledger: " +
      " -> ".join(f"#{h['id']} {h['route']} {h['reason']} {h['level']}" for h in hist))
    p(f"ruling stored: {rul['ruling']} by {rul['reviewer']}, reason code '{rul['note']}'")
    p(f"live after two activations and an undo: {live['reason']} (stored ruling reused, evidence shown unchanged)")
    return ok and rul["ruling"] == "no_match" and live["reason"] == "no_match_ruled", len(hist)


def wilson_lower(k, n, z=1.6449):  # one-sided 95%
    if n == 0:
        return 0.0
    ph = k / n
    return (ph + z * z / (2 * n) - z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def per_level_reporting(p):
    cur = rows("batch=?", (R.batch,))
    live = R.live()
    auto = [r for r in cur if r["reason"] == "auto_accept"]
    hum = [v for v in live.values() if v["reason"] == "human_accept"]
    voted = [v for v in live.values() if v["reason"] == "no_match_voted"]
    p(f"set {cards.SETS['r1']['rev']}, publication batch {R.batch}. Denominators: one row per (unit, candidate); "
      f"duplicates impossible (one gate outcome per pair per batch)")
    p(f"auto_accept rows by the gate (incl. later challenged): {len(auto)}; voted no match (live): {len(voted)}; "
      f"human accepts (live): {len(hum)}")
    p("SYNTHETIC precision of the auto-accept gate vs canned gold rulings, per level:")
    p(f"    {'level':<9} {'auto':>4} {'human':>5} {'correct':>7} {'precision':>9} {'wilson95_lb':>11}")
    tot, missing = 0, []
    for lv in cards.LEVELS:
        a = [r for r in auto if r["level"] == lv]
        missing += [f"{r['unit_id']}|{r['candidate']}" for r in a if f"{r['unit_id']}|{r['candidate']}" not in FX["gold"]]
        k = sum(FX["gold"].get(f"{r['unit_id']}|{r['candidate']}") == lv for r in a)
        tot += len(a)
        prec = f"{k / len(a):.3f}" if a else "n/a"
        p(f"    {lv:<9} {len(a):>4} {sum(h['level'] == lv for h in hum):>5} {k:>7} {prec:>9} {wilson_lower(k, len(a)):>11.3f}")
    p("no pooled figure is reported alone; gold missing for: " + (", ".join(missing) or "none"))
    return tot == len(auto) and not missing, len(auto) + len(hum) + len(voted)


def acceptance_gate(p):
    """Closing a gate is a deterministic change: affected outcomes go to review, nothing is re-asked."""
    S, live = cards.SETS["r4"], R.live()
    land = Counter()
    vals = [{"answer": None}] + [{"answer": lv} for lv in cards.LEVELS] + [{"answer": "maybe"}]
    for combo in itertools.product(vals, repeat=3):
        land[R.pick(S, "triad", {"votes": list(combo), "n": 3}, [])[0]] += 1
    rep = CTX["c"] = R.evaluate("r4")
    bad, moved = [], Counter()
    for (uid, cand), lv in live.items():
        new = rep["outcomes"].get(f"{uid}|{cand or '-'}")
        old = [lv["route"], lv["reason"], lv["level"]]
        if lv["reason"] == "no_match_voted":
            moved["no_match"] += 1
            bad += [] if new[:2] == ["REVIEW", "nomatch_gate_closed"] else [f"{uid}|{cand} {new}"]
        elif lv["reason"] == "auto_accept" and lv["level"] == "augments":
            moved["augments"] += 1
            bad += [] if new[:2] == ["REVIEW", "level_gate_closed"] else [f"{uid}|{cand} {new}"]
    asked = sum(v for k, v in rep["tally"].items() if k.split(":")[0] in MODEL_KINDS and k.endswith(":asked"))
    p(f"set r4 closes the gates for no match and augments; enumeration: T2-gated {land['T2-gated']}, "
      f"T3-gated {land['T3-gated']}, T2 {land['T2']}")
    p(f"live voted no matches sent to review {moved['no_match']}, live augments writes sent to review {moved['augments']}; "
      f"misrouted: {bad or 'none'}")
    p(f"model/retriever outputs asked {asked}; " + split(rep["tally"])[0])
    return (not bad and asked == 0 and land["T2"] == 0 and land["T2-gated"] > 0 and land["T3-gated"] > 0
            and moved["no_match"] > 0 and moved["augments"] > 0), sum(moved.values()) + 216


def engine_pin(p):
    """Changing a shared helper without a new set revision must make replay fail loudly."""
    uid = "S11"
    orig = cards.plurality
    ok_before = R.run_unit(uid, cards.SETS["r1"], "replay") is not None
    cards.plurality = lambda votes: next((v["answer"] for v in votes if cards.valid(v) and v["answer"]), None)
    try:
        R.run_unit(uid, cards.SETS["r1"], "replay")
        refused = False
    except RuntimeError as e:
        refused, msg = True, str(e)
    finally:
        cards.plurality = orig
    ok_after = R.run_unit(uid, cards.SETS["r1"], "replay") is not None
    p(f"engine hash pinned in every set: {sorted({S['engine'] for S in cards.SETS.values()})}")
    p(f"replay with a silently edited plurality(): {'refused: ' + msg if refused else 'NOT refused'}")
    p(f"replay after restoring the helper: {'ok' if ok_after else 'failed'}")
    return ok_before and refused and ok_after, 3


def cost_reporting(p):
    """SYNTHETIC cost: model calls on first pass and under each change, per unit and per live decision."""
    fp, n_units, n_live = CTX["first_pass"], len(R.unit_ids), len(R.live())
    p(f"first pass under r1: model calls {fp['model']}, retriever calls {fp['retriever']}, reviewer calls "
      f"{fp['reviewer']}, curator calls {fp['curator']}; {fp['model'] / n_units:.2f} model calls per unit, "
      f"{fp['model'] / n_live:.2f} per live decision ({n_units} units, {n_live} live decisions)")
    for k, label in (("a", "(a) loosen confidence"), ("b", "(b) reword triad question"), ("c", "(c) close two gates")):
        c = CTX[k]["calls"]
        p(f"{label}: model calls {c['model']} ({c['model'] / n_live:.2f} per live decision), reviewer {c['reviewer']}, "
          f"curator {c['curator']}")
    return fp["model"] > 0, n_live


def write_sssom(rec):
    d, v = rec["decision"], rec["votes"]
    head = ["# curie_map:", "#   ex: https://example.org/synthetic/", "#   semapv: https://w3id.org/semapv/vocab/",
            "#   orcid: https://orcid.org/", "# license: https://creativecommons.org/publicdomain/zero/1.0/",
            "# mapping_set_id: https://example.org/synthetic/mapping-set/r1"]
    cols = ["subject_id", "predicate_id", "object_id", "mapping_justification", "mapping_tool", "mapping_tool_version",
            "mapping_date", "comment", "other"]
    row = [f"ex:{d['unit_id']}", f"ex:{d['level']}", f"ex:{d['candidate']}", "semapv:CompositeMatching",
           "reference-runner", f"{d['set_revision']}/engine:{cards.SETS[d['set_revision']]['engine']}",
           "2026-10-30", f"{d['route']} {d['reason']} by card {d['card_id']}; triad " +
           ",".join(x["model_id"] for x in v),
           f"decision_id={d['id']}|candidate_set={rec['candidate_set']['hash']}|proposal={rec['proposal']['id']}|"
           f"votes={'/'.join(str(x['id']) for x in v)}"]
    with open(os.path.join(HERE, "decision_record_example.sssom.tsv"), "w") as f:
        f.write("\n".join(head) + "\n" + "\t".join(cols) + "\n" + "\t".join(map(str, row)) + "\n")


def write_example():
    uid, cand = "S11", "C1"
    d = rows("unit_id=? AND candidate=? AND batch=1", (uid, cand))[0]
    b = json.loads(d["basis"])
    cs = dict(R.db.execute("SELECT * FROM candidate_sets WHERE unit_id=?", (uid,)).fetchone())
    c0 = Counter(R.calls)
    rp = R.run_unit(uid, cards.SETS["r1"], "replay")[0][-1]
    calls = {k: R.calls[k] - c0[k] for k in CALLERS}
    out = lambda i: {k: v for k, v in R.row("outputs", i).items()}
    rec = {
        "unit": R.unit(uid), "release": R.unit(uid)["release"],
        "candidate_set": {**cs, "candidates": json.loads(cs["candidates"])},
        "proposal": {**out(b[0]["out"]), "payload": payload(b[0]["out"])},
        "votes": [{**out(x["out"]), "payload": payload(x["out"])} for x in b[1:]],
        "cards_evaluated_in_order": [{"set": t[0], "card": t[1], "revision": t[2], "matched": t[3]}
                                     for t in json.loads(d["trace"])["cards"]],
        "decision": {k: d[k] for k in ("id", "unit_id", "candidate", "route", "reason", "level", "card_id",
                                       "set_revision", "batch", "superseded_by", "created_at", "superseded_at")},
        "replay": {"set_revision": "r1", "route": rp["route"], "reason": rp["reason"], "level": rp["level"],
                   "calls": calls, "calls_total": sum(calls.values())},
        "re_evaluation": {k: {"set_revision": CTX[k]["set"], "outcome": CTX[k]["outcomes"][f"{uid}|{cand}"],
                              "this_unit": CTX[k]["per_unit"].get(uid, {}),
                              "model_calls_this_unit": sum(v for x, v in CTX[k]["per_unit"].get(uid, {}).items()
                                                           if x.endswith(":asked"))}
                          for k in ("a", "b")},
    }
    json.dump(rec, open(os.path.join(HERE, "decision_record_example.json"), "w"), indent=1, default=str)
    write_sssom(rec)
    return rec


for fn in (route_coverage, termination, otherwise_last_and_exhaustive, no_single_source_negative,
           replay_zero_calls, policy_eval_reuse, activation, undo_publication, undo_recovery, challenge,
           per_level_reporting, acceptance_gate, engine_pin, cost_reporting):
    check(fn)
ex = write_example()
print(f"\nwrote decision_record_example.json ({ex['unit']['unit_id']}/{ex['decision']['candidate']}, "
      f"replay calls {ex['replay']['calls_total']}, re-eval (a) calls {ex['re_evaluation']['a']['model_calls_this_unit']}, "
      f"(b) calls {ex['re_evaluation']['b']['model_calls_this_unit']})")
print("\n" + "-" * 62 + f"\n{'check':<34} {'result':<6} {'examined':>8}\n" + "-" * 62)
for name, res, n in RESULTS:
    print(f"{name:<34} {res:<6} {n:>8}")
print("-" * 62)
sys.exit(1 if any(r[1] == "FAIL" for r in RESULTS) else 0)
