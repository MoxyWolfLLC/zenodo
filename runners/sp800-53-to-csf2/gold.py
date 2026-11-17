#!/usr/bin/env python3
"""Item 7: the reference standard, assembled under the frozen policy.

    python3 -I gold.py assemble [--split evaluation] [--workers W]
        primary packets for every (unit, candidate) pair the split retrieved: blind (unit text, unit context, candidate
        text, rubric; never a proposal, vote, confidence, route or prediction), ruled by the three council voices
        (majority on the level; a three-way split of three valid votes is no_relationship with reason split; a reply that
        does not parse or names no level is not a vote: it is re-asked once, and a panel still short of three valid votes
        is unresolved, with no label, outside every draw and every score, and counted); the 20% double-ruled draw ruled
        a second time in its own session; the 30 matched blind-versus-assisted pairs ruled in an assisted session with
        the run's proposal, votes and confidence attached; the missed-link draw of r non-retrieved candidates per unit
        ruled in the same pass. Every ruling is stored in council_rulings keyed by (packet id, voice, session) and reused
        on a rerun. Writes gold.<split>.json. Exit 0; 4 on a budget refusal.
    python3 -I gold.py audit [--split evaluation]
        exit 0 only when no primary packet of the split contains a proposal, vote, confidence, route or prediction

NIST informative references enter gold as associations (reference_semantics.json: tag association); a five_class entry
would carry its level, and the pinned sources yield none.

Draws follow protocol.json selection_rules: identities sorted as strings, Python's random.Random(seed).sample, seed
sha256(protocol seed + ':' + draw name + ':' + split); the per-unit missed-link draw seeds one Random with that and
draws unit by unit in sorted unit order.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import random
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("GSTACK_STORE") or os.path.join(HERE, "run.sqlite")
FORBIDDEN = {"proposal", "proposals", "vote", "votes", "confidence", "route", "prediction", "predictions",
             "nist_reference", "reference_label"}


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


providers, retrieve = load("providers"), load("retrieve")
cards = load("cards")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in FORBIDDEN}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def forbidden_paths(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else str(k)
            if str(k).lower() in FORBIDDEN:
                yield here
            yield from forbidden_paths(v, here)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from forbidden_paths(v, f"{path}[{i}]")


def draw_seed(protocol, name, split):
    return hashlib.sha256(f"{protocol['seed']}:{name}:{split}".encode()).hexdigest()


def draw(protocol, name, split, population, count):
    """selection_rules: sorted identities, Random(seed).sample; all of them when fewer exist."""
    ordered = sorted(population, key=str)
    count = min(count, len(ordered))
    return random.Random(draw_seed(protocol, name, split)).sample(ordered, count) if count else []


def share_count(share, population):
    return max(1, int(round(share * population))) if population else 0


class Gold:
    def __init__(self, protocol, adapter, db_path=STORE):
        self.protocol, self.adapter = protocol, adapter
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.hub = {s["id"]: s["text"] for s in retrieve.active_subcategories()}
        self.voices = protocol["roles"]["council"]["models"]
        self.lock = threading.Lock()
        self.tally = {"asked": 0, "reused": 0}

    # ---- the store ---------------------------------------------------------------------------------
    def rows(self, sql, *args):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    def pairs(self, split, rev):
        """Every (unit, candidate) decision of the split under the revision, with the unit's text and context."""
        return self.rows("SELECT d.*, u.unit_id AS uid, u.statement, u.family, u.kind FROM decisions d JOIN units u ON u.unit_release=d.unit_release "
                         "WHERE u.split=? AND d.revision=? AND d.superseded_by IS NULL ORDER BY d.unit_id, d.hub_entry", split, rev)

    def retrieved(self, unit_release):
        r = self.rows("SELECT candidates FROM retrievals WHERE unit_release=? ORDER BY created DESC LIMIT 1", unit_release)
        return json.loads(r[0]["candidates"]) if r else []

    def run_outputs(self, unit_release, hub_entry):
        """What the run proposed and voted for a pair: the assisted packet's attachment, never a primary one's."""
        p = self.rows("SELECT * FROM proposals WHERE unit_release=? AND hub_entry=? ORDER BY created DESC LIMIT 1", unit_release, hub_entry)
        if not p:
            return None
        p = p[0]
        votes = self.rows("SELECT voter, vote FROM votes WHERE proposal_output_id=? ORDER BY created DESC", p["output_id"])
        seen, latest = set(), []
        for v in votes:
            if v["voter"] not in seen:
                seen.add(v["voter"]); latest.append(v)
        return {"proposal": {"level": p["level"], "quotes": json.loads(p["quotes"]), "confidence": p["confidence"]},
                "votes": [{"voter": v["voter"], "vote": v["vote"]} for v in latest]}

    # ---- packets -----------------------------------------------------------------------------------
    def primary_packet(self, unit_id, statement, family, base_title, hub_entry):
        return {"unit": {"id": unit_id, "text": statement}, "context": {"family": family, "base_title": base_title},
                "candidate": {"id": hub_entry, "text": self.hub[hub_entry]}, "rubric": self.protocol["rubric"]}

    def rule(self, packet, packet_class, split, session, blind=True, attachment=None):
        """Three voices, one call each, keyed by (packet id, voice, session); majority on the level, a three-way split of
        three valid votes is no_relationship with reason split. A reply that does not parse or names no level is not a
        vote: it is asked again once (a stored invalid counts as the first try), and a panel still short of three valid
        votes is unresolved (label None, reason unresolved). Returns the ruling dict."""
        pid = digest(packet)
        votes = []
        for m in self.voices:
            stored = self.rows("SELECT level, reason FROM council_rulings WHERE packet_id=? AND voice=? AND session=?", pid, m, session)
            if stored and stored[0]["level"] != "invalid":
                with self.lock:
                    self.tally["reused"] += 1
                votes.append({"model": m, "level": stored[0]["level"], "reason": stored[0]["reason"]})
                continue
            tries = 1 if stored else 2   # a stored invalid was the first try
            for attempt in range(tries):
                level, reason = self.ask(packet, pid, packet_class, split, session, m, blind, attachment, attempt + 1 + (1 if stored else 0))
                if level is not None:
                    break
            votes.append({"model": m, "level": level, "reason": reason})
        valid = [v["level"] for v in votes if v["level"] is not None]
        if len(valid) < len(self.voices):
            return {"packet_id": pid, "label": None, "reason": "unresolved", "votes": votes, "session": session}
        winner = max(set(valid), key=valid.count)
        if valid.count(winner) < 2:
            return {"packet_id": pid, "label": cards.NULL, "reason": "split", "votes": votes, "session": session}
        return {"packet_id": pid, "label": winner, "reason": "majority", "votes": votes, "session": session}

    def ask(self, packet, pid, packet_class, split, session, m, blind, attachment, attempt):
        """One call to one voice; the reply stored under (packet id, voice, session), level 'invalid' when it is not a vote."""
        with self.lock:
            self.tally["asked"] += 1
        context = f"family {packet['context']['family']}: {packet['context']['base_title']}"
        if attachment:
            context += (f". The run proposed {attachment['proposal']['level']} (confidence {attachment['proposal']['confidence']}, "
                        f"quotes {json.dumps(attachment['proposal']['quotes'])}); votes: "
                        + ", ".join(f"{v['voter'].split('/')[0]} {v['vote']}" for v in attachment["votes"]))
        prompt = providers.render("council", rubric=packet["rubric"], unit_id=packet["unit"]["id"], unit_text=packet["unit"]["text"],
                                  context=context, hub_id=packet["candidate"]["id"], hub_text=packet["candidate"]["text"],
                                  levels=", ".join(cards.LEVELS), null=cards.NULL)
        q = providers.identity("council", packet=pid, voice=m, session=session, prompt_version="council-v1")
        out = self.adapter.call("council", m, "council-v1", prompt, q, providers.identity("evidence", packet=pid),
                                packet_sha256=pid, packet_class=packet_class, split=split, session=session, council_attempt=attempt)
        level = out.get("level") if isinstance(out, dict) else None
        level = level if level in cards.LEVELS or level == cards.NULL else None
        reason = (out.get("reason") if isinstance(out, dict) else None) or ("unparsed" if level is None else "")
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO council_rulings (packet_id, voice, packet_class, split, blind, level, reason, session, created) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", (pid, m, packet_class, split, int(blind), level or "invalid", str(reason)[:300], session, now()))
        return level, reason

    # ---- the pass -----------------------------------------------------------------------------------
    def assemble(self, split, rev, workers, catalog):
        protocol = self.protocol
        pairs = [p for p in self.pairs(split, rev) if p["hub_entry"]]
        units = {p["unit_release"]: p for p in self.pairs(split, rev)}
        packets = {}
        for p in pairs:
            u = catalog.units[p["uid"]]
            pk = self.primary_packet(p["uid"], p["statement"], p["family"], u["base_title"], p["hub_entry"])
            packets[(p["unit_release"], p["hub_entry"])] = pk
        violations = [{"pair": list(k), "path": path} for k, pk in packets.items() for path in forbidden_paths(pk)]
        if violations:
            print(f"refused: {len(violations)} primary packet(s) carry a forbidden field", file=sys.stderr)
            sys.exit(1)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            primary = dict(zip(packets, ex.map(lambda k: self.rule(packets[k], "primary", split, "blind"), packets)))
        pid_of = {k: r["packet_id"] for k, r in primary.items()}
        by_pid = {r["packet_id"]: k for k, r in primary.items()}
        # the 20% double-ruled draw: primary packets of the split, identified by packet id
        n_double = share_count(protocol["double_ruled"]["share"], len(primary))
        double_ids = draw(protocol, "double_ruled", split, list(by_pid), n_double)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            double = dict(zip(double_ids, ex.map(lambda pid: self.rule(packets[by_pid[pid]], "primary", split, "double"), double_ids)))
        alpha = krippendorff_nominal([(primary[by_pid[pid]]["label"], double[pid]["label"]) for pid in double_ids])
        # the 30 matched pairs: the same primary packet ruled assisted in a separate session
        ruled_ids = [pid for pid, k in by_pid.items() if primary[k]["label"] is not None]   # every valid blind ruling, split included
        pair_ids = draw(protocol, "assisted_pairs", split, ruled_ids, protocol["anchoring_comparison"]["pairs_per_split"])
        attachments = {pid: self.run_outputs(*by_pid[pid]) for pid in pair_ids}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            assisted = dict(zip(pair_ids, ex.map(lambda pid: self.rule(packets[by_pid[pid]], "assisted", split, "assisted", blind=False,
                                                                       attachment=attachments[pid]), pair_ids)))
        anchoring = anchoring_stat([(primary[by_pid[pid]]["label"], assisted[pid]["label"], attachments[pid]["proposal"]["level"])
                                    for pid in pair_ids if attachments[pid]])
        # the missed-link draw: r non-retrieved hub entries per unit, one Random for the split, units in sorted order
        r = protocol["missed_link_estimand"]["non_retrieved_stratum"]["draws_per_unit"]
        rng = random.Random(draw_seed(protocol, "missed_link", split))
        missed_packets = {}
        for rel in sorted(units):
            p = units[rel]
            pool = sorted(set(self.hub) - set(self.retrieved(rel)))
            u = catalog.units[p["uid"]]
            for hub_entry in rng.sample(pool, min(r, len(pool))):
                missed_packets[(rel, hub_entry)] = self.primary_packet(p["uid"], p["statement"], p["family"], u["base_title"], hub_entry)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            missed = dict(zip(missed_packets, ex.map(lambda k: self.rule(missed_packets[k], "missed_link", split, "blind"), missed_packets)))
        out = {"split": split, "revision": rev, "policy": {"nist_references": "association", "five_class": "carries its level; the pinned sources yield none",
                                                           "primary_packet": "blind: unit text, unit context (family and base control title), candidate text, rubric",
                                                           "ruling": "majority of three valid votes on the level; a three-way split is no_relationship with reason split; "
                                                                     "a reply that is not a vote is re-asked once; a panel short of three valid votes is unresolved and carries no label"},
               "voices": self.voices, "packets": len(primary),
               "unresolved": {"primary": sum(1 for r in primary.values() if r["label"] is None), "double": sum(1 for r in double.values() if r["label"] is None),
                              "assisted": sum(1 for r in assisted.values() if r["label"] is None), "missed_link": sum(1 for r in missed.values() if r["label"] is None)},
               "primary_gold": [{"unit_release": k[0], "unit_id": units[k[0]]["uid"], "candidate_id": k[1], "packet_sha256": r["packet_id"],
                                 "label": r["label"], "reason": r["reason"], "origin": "council", "human_validated": False,
                                 "votes": [{"model": v["model"], "level": v["level"]} for v in r["votes"]]} for k, r in primary.items()],
               "double_ruled": {"share": protocol["double_ruled"]["share"], "n": len(double_ids), "seed": draw_seed(protocol, "double_ruled", split),
                                "krippendorff_alpha": alpha,
                                "rows": [{"packet_sha256": pid, "first": primary[by_pid[pid]]["label"], "second": double[pid]["label"]} for pid in double_ids]},
               "anchoring": {"pairs": len(pair_ids), "seed": draw_seed(protocol, "assisted_pairs", split), **anchoring,
                             "rows": [{"packet_sha256": pid, "blind": primary[by_pid[pid]]["label"], "assisted": assisted[pid]["label"],
                                       "proposal": attachments[pid]["proposal"]["level"] if attachments[pid] else None} for pid in pair_ids]},
               "missed_link_draw": {"r": r, "seed": draw_seed(protocol, "missed_link", split), "n": len(missed),
                                    "weight": protocol["missed_link_estimand"]["non_retrieved_stratum"]["weight"],
                                    "rows": [{"unit_release": k[0], "unit_id": units[k[0]]["uid"], "candidate_id": k[1], "packet_sha256": r["packet_id"],
                                              "label": r["label"], "reason": r["reason"]} for k, r in missed.items()]},
               "tally": dict(self.tally), "assembled": now()}
        return out


def krippendorff_nominal(pairs):
    """Krippendorff's alpha, nominal, two coders, no missing values: 1 - Do/De over the coincidence matrix."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = 2 * len(pairs)
    if n < 4:
        return None
    from collections import Counter
    counts = Counter()
    for a, b in pairs:
        counts[a] += 1; counts[b] += 1
    disagreements = sum(1 for a, b in pairs if a != b) * 2
    do = disagreements / n
    de = 1 - sum(c * (c - 1) for c in counts.values()) / (n * (n - 1))
    return round(1 - do / de, 6) if de else 1.0


def anchoring_stat(rows):
    """Share of rulings that agree with the run's proposal, blind minus assisted, with a 95% Wilson interval on the
    discordant pairs (pairs where exactly one of the two rulings agrees with the proposal)."""
    import math
    n = len(rows)
    if not n:
        return {"agree_blind": None, "agree_assisted": None, "difference": None, "discordant": 0, "wilson_95": None}
    ab = sum(1 for b, a, p in rows if b == p) / n
    aa = sum(1 for b, a, p in rows if a == p) / n
    disc = [(b == p, a == p) for b, a, p in rows if (b == p) != (a == p)]
    k, m = sum(1 for b, a in disc if b), len(disc)
    wilson = None
    if m:
        z, ph = 1.959963984540054, k / m
        centre = (ph + z * z / (2 * m)) / (1 + z * z / m)
        half = z * math.sqrt(ph * (1 - ph) / m + z * z / (4 * m * m)) / (1 + z * z / m)
        wilson = [round(centre - half, 4), round(centre + half, 4)]
    return {"agree_blind": round(ab, 4), "agree_assisted": round(aa, 4), "difference": round(ab - aa, 4), "discordant": m,
            "blind_wins_among_discordant": k, "wilson_95": wilson}


def audit(split, db_path=STORE):
    """gold.py audit: no primary packet of the split carries a forbidden field. The packets are rebuilt from the store
    exactly as assemble builds them, so what is audited is what the council saw."""
    protocol = retrieve.protocol_checked()
    run = load("run")
    g = Gold(protocol, None, db_path)
    cat = run.Catalog()
    out = json.load(open(os.path.join(HERE, f"gold.{split}.json"), encoding="utf-8")) if os.path.isfile(os.path.join(HERE, f"gold.{split}.json")) else None
    rev = out["revision"] if out else "r2"
    violations, n = [], 0
    for p in g.pairs(split, rev):
        if not p["hub_entry"]:
            continue
        u = cat.units[p["uid"]]
        pk = g.primary_packet(p["uid"], p["statement"], p["family"], u["base_title"], p["hub_entry"])
        n += 1
        violations += [{"unit": p["uid"], "candidate": p["hub_entry"], "path": path} for path in forbidden_paths(pk)]
        if out:
            gold_row = next((r for r in out["primary_gold"] if r["unit_release"] == p["unit_release"] and r["candidate_id"] == p["hub_entry"]), None)
            if gold_row and gold_row["packet_sha256"] != digest(pk):
                violations.append({"unit": p["uid"], "candidate": p["hub_entry"], "path": "packet_sha256 differs from the ruled packet"})
    print(f"audit {split}: {n} primary packets, {len(violations)} violation(s)")
    for v in violations[:20]:
        print("  ", v)
    return 0 if not violations else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["assemble", "audit"])
    ap.add_argument("--split", default="evaluation")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    if args.command == "audit":
        return audit(args.split)

    def body():
        protocol = retrieve.protocol_checked()
        adapter = providers.Adapter(protocol)
        run = load("run")
        g = Gold(protocol, adapter)
        before, _ = adapter.spend()
        t0 = time.time()
        out = g.assemble(args.split, "r2", args.workers, run.Catalog())
        after, _ = adapter.spend()
        out["cost_usd"] = providers.money(after - before)
        path = os.path.join(HERE, f"gold.{args.split}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, sort_keys=True)
            f.write("\n")
        labels = {}
        for r in out["primary_gold"]:
            labels[r["label"]] = labels.get(r["label"], 0) + 1
        print(f"gold {args.split}: {out['packets']} primary packets ruled ({labels}); unresolved {out['unresolved']}; double-ruled {out['double_ruled']['n']} alpha "
              f"{out['double_ruled']['krippendorff_alpha']}; assisted pairs {out['anchoring']['pairs']} blind-minus-assisted "
              f"{out['anchoring']['difference']}; missed-link draw {out['missed_link_draw']['n']}; asked {out['tally']['asked']} reused "
              f"{out['tally']['reused']}; cost {out['cost_usd']} USD; {round(time.time() - t0, 1)}s -> {os.path.basename(path)}")
    return providers.run_pass(body)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
