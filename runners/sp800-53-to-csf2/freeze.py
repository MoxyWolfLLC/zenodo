#!/usr/bin/env python3
"""Item 2: freeze the protocol.

Reads the pinned catalog under data/ and sources.json, draws the sample and the family-preserving
three-way split with a seed derived from the pinned hashes, fixes the adjudication policy, the
missed-link estimand, the anchoring comparison, the double-ruled draw, r3's formula and the budget,
and writes protocol.json plus protocol.sha256 before any model call is made.

    python3 -I freeze.py                        write protocol.json and protocol.sha256; print the hash
    python3 -I freeze.py --break-split <family> move that family's last unit to another split, so the
                                                exit-3 check can be exercised; writes nothing

Exit codes: 0 ok; 3 a family's units land in more than one split (stderr names the family);
4 the budget does not fit under the cap with its reserve (stderr shows the total).
"""
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CAP, RESERVE = 60.0, 10.0
K = 8                      # retrieval cut-off, fixed, never tuned
R = 5                      # missed-link draws per unit from outside the retrieved top-k
TARGET_UNITS = (300, 400)  # exact sample size falls inside this range
SPLIT_SHARES = {"calibration": 0.2, "evaluation": 0.4, "test": 0.4}
CALIBRATION_REVISIONS = 6
VOICES = 3
DOUBLE_RULED_SHARE = 0.20
ASSISTED_PAIRS = 30        # matched blind-versus-assisted council packets per split
SS_AUDIT_PER_CARD = 30     # SS-scope and SS-retrieval outputs drawn per card, pooled per split

# Unit prices in USD per million tokens, read from https://openrouter.ai/api/v1/models on 2026-11-16.
# The embedding model is not listed there; its price is OpenAI's published one on the same day.
PRICES = {
    "openai/gpt-6-luna":            {"in": 0.100, "out": 0.500, "provider": "openai"},
    "openai/text-embedding-3-small": {"in": 0.020, "out": 0.0,   "provider": "openai",
                                     "source": "https://openai.com/api/pricing/ 2026-11-16"},
    "anthropic/claude-haiku-5.5":   {"in": 0.100, "out": 0.500, "provider": "anthropic"},
    "google/gemini-2.5-flash-lite": {"in": 0.100, "out": 0.400, "provider": "google"},
    "google/gemini-3.1-flash-lite": {"in": 0.250, "out": 1.500, "provider": "google"},
    "mistralai/mistral-small-2603": {"in": 0.150, "out": 0.600, "provider": "openrouter"},
    "mistralai/mistral-large-4-0":  {"in": 0.700, "out": 2.100, "provider": "openrouter"},
}
PRICE_SOURCE = "https://openrouter.ai/api/v1/models retrieved 2026-11-16; usd per 1e6 tokens"

ROLES = {
    "extraction": {"model": "openai/gpt-6-luna", "in": 2000, "out": 400},
    "embedding":  {"model": "openai/text-embedding-3-small", "in": 400, "out": 0},
    "proposal":   {"model": "openai/gpt-6-luna", "in": 1500, "out": 200},
    "vote":       {"models": ["anthropic/claude-haiku-5.5", "google/gemini-2.5-flash-lite", "mistralai/mistral-small-2603"],
                   "in": 1200, "out": 150},
    "council":    {"models": ["anthropic/claude-haiku-5.5", "google/gemini-3.1-flash-lite", "mistralai/mistral-large-4-0"],
                   "in": 1800, "out": 250},
}

LEVELS = ["subset_of", "intersects_with", "equal", "superset_of"]
NULL = "no_relationship"

RUBRIC = """Unit: one active SP 800-53 Rev. 5 control or control enhancement (statement text). Hub entry: one active
CSF 2.0 subcategory (identifier and text). Rule on the set of required activities each describes, following
NIST IR 8477's set-theory relationship types:
  subset_of        every activity the unit requires is required by the subcategory, and the subcategory requires more
  superset_of      every activity the subcategory requires is required by the unit, and the unit requires more
  equal            the two require the same activities
  intersects_with  some required activities are shared, each side also requires activities the other does not
  no_relationship  no required activity is shared
Rule from the two texts alone. A packet shows source text, context, candidate and this rubric, never a proposal,
vote, confidence, route or prediction. When the texts do not settle the level, rule no_relationship and say why."""

POLICY = {
    "primary_packet": "blind: unit text, unit context (family and base control title), candidate subcategory text, rubric",
    "assisted_packet": "the primary packet plus the run's proposal, votes and confidence; never primary gold",
    "double_ruled_share": DOUBLE_RULED_SHARE,
    "double_ruled_from": "primary packets only",
    "council_voices": ROLES["council"]["models"],
    "ruling": "majority of three voices on the level; a three-way split is ruled no_relationship with reason split",
    "agreement": "Krippendorff's alpha on the double-ruled subset",
}


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def load_units():
    cat = json.load(open(os.path.join(DATA, "sp800-53r5_catalog.json"), encoding="utf-8"))["catalog"]
    families = []   # one per active base control: {"id", "group", "units": [base, enh...]}
    for g in cat["groups"]:
        for c in g.get("controls", []):
            if withdrawn(c):
                continue
            units = [c["id"]] + [e["id"] for e in c.get("controls", []) if not withdrawn(e)]
            families.append({"id": c["id"], "group": g["id"], "units": units})
    return families


def withdrawn(c):
    return any(p.get("name") == "status" and p.get("value") == "withdrawn" for p in c.get("props", []))


def draw(families, seed):
    """Family-preserving stratified sample: whole families, quota per group proportional to its unit
    share, filled in a seeded shuffle until the unit count lands inside TARGET_UNITS."""
    rng = random.Random(seed)
    by_group = {}
    for f in families:
        by_group.setdefault(f["group"], []).append(f)
    for fs in by_group.values():
        rng.shuffle(fs)
    total_units = sum(len(f["units"]) for f in families)
    goal = (TARGET_UNITS[0] + TARGET_UNITS[1]) // 2
    quota = {g: max(1, round(goal * sum(len(f["units"]) for f in fs) / total_units / (sum(len(f["units"]) for f in fs) / len(fs))))
             for g, fs in by_group.items()}
    chosen, count = [], 0
    order = sorted(by_group)
    taken = {g: 0 for g in order}
    while count < TARGET_UNITS[0] or any(taken[g] < quota[g] for g in order):
        progressed = False
        for g in order:
            if taken[g] < min(quota[g], len(by_group[g])) and count + len(by_group[g][taken[g]]["units"]) <= TARGET_UNITS[1]:
                f = by_group[g][taken[g]]
                chosen.append(f); taken[g] += 1; count += len(f["units"]); progressed = True
        if not progressed:
            break
    strata = {}
    for g in order:
        fs = by_group[g]
        base_total = len(fs); base_sel = taken[g]
        enh_total = sum(len(f["units"]) - 1 for f in fs)
        enh_sel = sum(len(f["units"]) - 1 for f in fs[:taken[g]])
        strata[g] = {"base": {"population": base_total, "selected": base_sel, "p": base_sel / base_total},
                     "enhancement": {"population": enh_total, "selected": enh_sel, "p": (enh_sel / enh_total) if enh_total else None}}
    return chosen, count, strata


def split(chosen, seed):
    """Assign whole families to calibration / evaluation / test, balanced within each group."""
    rng = random.Random(seed + ":split")
    by_group = {}
    for f in chosen:
        by_group.setdefault(f["group"], []).append(f)
    assignment = {}
    names = list(SPLIT_SHARES)
    for g in sorted(by_group):
        fs = by_group[g][:]
        rng.shuffle(fs)
        n = len(fs)
        cuts = [round(n * SPLIT_SHARES["calibration"]), round(n * (SPLIT_SHARES["calibration"] + SPLIT_SHARES["evaluation"]))]
        for i, f in enumerate(fs):
            assignment[f["id"]] = names[0] if i < cuts[0] else names[1] if i < cuts[1] else names[2]
    return assignment


def unit_splits(chosen, assignment):
    return {u: assignment[f["id"]] for f in chosen for u in f["units"]}


def check_split(chosen, units):
    for f in chosen:
        if len({units[u] for u in f["units"]}) > 1:
            return f["id"]
    return None


def selection_rules(seed, hub_count):
    """Every later draw is fixed here as a complete rule: eligible population, ordering, seed, count and
    method. Identities do not exist yet (packets are made by later items), so the rule, not a list, is
    what is frozen; a draw that follows it is reproducible from this file alone."""
    common = {"ordering": "eligible identities sorted as strings, ascending, before any draw",
              "method": "Python random.Random(seed).sample(ordered, count): without replacement, the standard library's sampler",
              "seed_derivation": "sha256(protocol seed + ':' + draw name + ':' + split name), hex, as the Random seed",
              "rounding": "count = int(round(share * population)) with Python's round (half to even); at least 1 when the population is non-empty"}
    return {
        "double_ruled": dict(common, population="primary packets of the split, identified by their packet id",
                             share=DOUBLE_RULED_SHARE, draw_name="double_ruled"),
        "missed_link": dict(common, population="per unit: the hub entries outside the unit's retrieved top-k, identified by subcategory id",
                            count=R, draw_name="missed_link", per_unit=True, note=f"r = {R} for every unit; pool {hub_count - K} when every top-k candidate is a distinct subcategory"),
        "assisted_pairs": dict(common, population="primary packets of the split that received a blind ruling, identified by packet id",
                               count=ASSISTED_PAIRS, draw_name="assisted_pairs"),
        "ss_audit": dict(common, population="per card: outputs the card refused or flagged in the split, identified by output id",
                         count=SS_AUDIT_PER_CARD, draw_name="ss_audit", per_card=True, note="all of them when fewer exist; the pool size is recorded"),
    }


def price(model, tokens_in, tokens_out):
    p = PRICES[model]
    return (tokens_in * p["in"] + tokens_out * p["out"]) / 1e6


def calls_usd(line):
    return line["calls"] * price(line["model"], line["tokens_in_ceiling"], line["tokens_out_ceiling"])


def budget(n_units, n_by_split, hub_count):
    """Every per-unit count derives from the sample and K; each fixed count names its source."""
    lines = []

    def add(stage, model, calls, basis, tin, tout):
        lines.append({"stage": stage, "model": model, "provider": PRICES[model]["provider"], "calls": calls,
                      "basis": basis, "tokens_in_ceiling": tin, "tokens_out_ceiling": tout,
                      "usd": round(calls * price(model, tin, tout), 4)})

    n_cal, n_eval, n_test = n_by_split["calibration"], n_by_split["evaluation"], n_by_split["test"]
    e, m, p, v, c = ROLES["extraction"], ROLES["embedding"], ROLES["proposal"], ROLES["vote"], ROLES["council"]
    add("extraction", e["model"], n_units, "one per sampled unit", e["in"], e["out"])
    add("query_embedding", m["model"], n_units, "one per sampled unit", m["in"], m["out"])
    add("hub_embedding", m["model"], hub_count, "active subcategories, sources.json counts.csf2.subcategories", m["in"], m["out"])
    add("proposal", p["model"], n_units * K, "sample x k", p["in"], p["out"])
    for vm in v["models"]:
        add("vote", vm, n_units * K, "sample x k, one per voter", v["in"], v["out"])
    # calibration: each revision re-runs extraction, proposals and votes on the calibration split
    add("calibration_extraction", e["model"], CALIBRATION_REVISIONS * n_cal, "six-revision cap x calibration units", e["in"], e["out"])
    add("calibration_proposal", p["model"], CALIBRATION_REVISIONS * n_cal * K, "six-revision cap x calibration units x k", p["in"], p["out"])
    for vm in v["models"]:
        add("calibration_vote", vm, CALIBRATION_REVISIONS * n_cal * K, "six-revision cap x calibration units x k, one per voter", v["in"], v["out"])
    # council: three voices per packet over every packet class, for the evaluation and sealed-test splits
    for name, n_s in (("evaluation", n_eval), ("test", n_test)):
        packets = {"primary_and_census": n_s * K, "double_ruled": int(round(DOUBLE_RULED_SHARE * n_s * K)),
                   "missed_link_draw": R * n_s, "ss_audit": 2 * SS_AUDIT_PER_CARD, "assisted_pairs": ASSISTED_PAIRS}
        for cm in c["models"]:
            add(f"council_{name}", cm, sum(packets.values()),
                f"{name} split: " + ", ".join(f"{k} {v}" for k, v in packets.items()) + "; one call per packet per voice", c["in"], c["out"])
    for vm in v["models"]:
        add("r3_allowance", vm, n_test * K, "sealed-test votes re-asked, worst case every one, one per voter", v["in"], v["out"])
    total = round(sum(calls_usd(l) for l in lines), 2)
    by_provider = {}
    for l in lines:   # accumulate at full precision, round once (review F2)
        by_provider[l["provider"]] = by_provider.get(l["provider"], 0) + calls_usd(l)
    by_provider = {k: round(v, 2) for k, v in by_provider.items()}
    return {"cap_usd": CAP, "reserve_usd": RESERVE, "reserve_rule": "inside the cap; released only in the deliverables phase",
            "k": K, "r": R, "voices": VOICES, "lines": lines, "total_usd": total, "total_with_reserve_usd": round(total + RESERVE, 2),
            "by_provider_usd": by_provider, "prices": PRICES, "price_source": PRICE_SOURCE}


def main(argv):
    sources = json.load(open(os.path.join(HERE, "sources.json")))
    seed = sha256_bytes("".join(sources["files"][n]["sha256"] for n in sorted(sources["files"])).encode())
    families = load_units()
    chosen, n_units, strata = draw(families, seed)
    assignment = split(chosen, seed)
    units = unit_splits(chosen, assignment)
    if "--break-split" in argv:
        fam = argv[argv.index("--break-split") + 1]
        last = next(f for f in chosen if f["id"] == fam)["units"][-1]
        units[last] = next(s for s in SPLIT_SHARES if s != units[last])
    bad = check_split(chosen, units)
    if bad:
        print(f"family in more than one split: {bad}", file=sys.stderr)
        return 3
    n_by_split = {s: sum(len(f["units"]) for f in chosen if assignment[f["id"]] == s) for s in SPLIT_SHARES}
    hub_count = sources["counts"]["csf2"]["subcategories"]
    b = budget(n_units, n_by_split, hub_count)
    if b["total_with_reserve_usd"] >= CAP:
        print(f"budget {b['total_with_reserve_usd']} with reserve is not under the cap {CAP}", file=sys.stderr)
        return 4
    protocol = {
        "goal": "sp800-53-to-csf2-2",
        "seed": seed, "seed_basis": "sha256 of the three pinned source hashes in sources.json, in file-name order",
        "sources": {n: sources["files"][n]["sha256"] for n in sorted(sources["files"])},
        "unit": "active SP 800-53 Rev. 5 control or enhancement", "hub": f"{hub_count} active CSF 2.0 subcategories at hub revision 1",
        "levels": LEVELS, "null": NULL,
        "sample": {"size": n_units, "range": TARGET_UNITS, "families": len(chosen), "population_units": sum(len(f["units"]) for f in families),
                   "population_families": len(families), "strata": strata,
                   "units": [{"id": u, "family": f["id"], "group": f["group"], "kind": "base" if u == f["id"] else "enhancement",
                              "split": units[u]} for f in chosen for u in f["units"]]},
        "split": {"shares": SPLIT_SHARES, "rule": "whole families; a base control and its enhancements always share a split",
                  "units_by_split": n_by_split},
        "adjudication": POLICY, "rubric": RUBRIC,
        "missed_link_estimand": {
            "definition": "share of ruled true links the run recorded as no_relationship or never retrieved, over ruled true links",
            "retrieved_stratum": {"source": "every retrieved candidate the run voted no_relationship, ruled by the council", "weight": 1},
            "non_retrieved_stratum": {"draws_per_unit": R, "pool_per_unit": hub_count - K,
                                      "inclusion_probability": R / (hub_count - K), "weight": (hub_count - K) / R,
                                      "note": "the same r for every unit; weight is the inverse inclusion probability",
                                      "selection": "selection_rules.missed_link"}},
        "anchoring_comparison": {"pairs_per_split": ASSISTED_PAIRS, "selection": "selection_rules.assisted_pairs", "design": "matched pairs, the same primary packet ruled blind and, in a separate session, assisted",
                                 "statistic": "difference in the share of rulings that agree with the run's proposal, blind minus assisted, with a 95% Wilson interval on the discordant pairs"},
        "double_ruled": {"share": DOUBLE_RULED_SHARE, "from": "primary packets", "statistic": "Krippendorff's alpha",
                         "selection": "selection_rules.double_ruled"},
        "selection_rules": selection_rules(seed, hub_count),
        "r3": {"formula": "threshold_r2 - 0.2 floored at 0; if that leaves the value unchanged, threshold_r2 + 0.2 capped at 1",
               "numeric_value": "recorded in config.frozen.json after calibration, never here",
               "scope": "sealed-test split only; its vote identities change, so exactly its vote calls are re-asked; rulings naming a re-asked vote are re-derived at no model cost and counted separately"},
        "calibration": {"revisions_cap": CALIBRATION_REVISIONS, "tunes": ["prompt wording", "confidence threshold"],
                        "labels": "pinned NIST informative references for the calibration split, link versus no link only"},
        "ss_audit": {"per_card": SS_AUDIT_PER_CARD, "cards": ["SS-scope", "SS-retrieval"], "pooled_per_split": True, "selection": "selection_rules.ss_audit"},
        "roles": ROLES, "budget": b,
    }
    raw = json.dumps(protocol, indent=2, sort_keys=True).encode()
    open(os.path.join(HERE, "protocol.json"), "wb").write(raw)
    digest = sha256_bytes(raw)
    open(os.path.join(HERE, "protocol.sha256"), "w").write(digest + "\n")
    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
