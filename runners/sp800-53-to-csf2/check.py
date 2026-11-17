#!/usr/bin/env python3
"""Item 5: the card table and the fixture harness.

    python3 -I check.py --table
        every card id, reached on fixtures through the same engine the run uses; E1 is present_unreachable
        (route is retrieval only: H_max = 0); exit 0 only when every row is PASS or present_unreachable

    python3 -I -B check.py --request REQUEST.json --output-dir OUTPUT
        the goal's black-box interface: {operation, transport: "fixture", payload}. Writes result.json,
        events.jsonl and accounting.jsonl into OUTPUT, plus the operation's own artifacts. Only provider
        responses are fixtures; the cards, engine, store, identities, guard and events are the run's own.

Operations: gates, protocol, gold, provider_call, triad (item 5) and seed, replay, policy, conformance
(fixture_ops.py, items 7 to 9).

What the harness is built on: FixtureAdapter subclasses Adapter in runners/sp800-53-to-csf2/providers.py and
replaces only _send, _usage and _content (the guard, ceilings, identities, accounting rows and events are
inherited); provider_call also replaces spend() with the ledger position the payload states. The cards are
runners/sp800-53-to-csf2/cards.py, the walk is runners/sp800-53-to-csf2/engine.py, the ledger is
runners/sp800-53-to-csf2/store.py.

Exit codes: 0 done; 1 refused (gold contamination, triad conflict, protocol hash mismatch); 2 unsupported
operation; 3 split violation; 4 budget refusal; 5 outbound attempt in replay mode.
"""
import argparse
import hashlib
import importlib.util
import itertools
import json
import math
import os
import sys
import time
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


engine, providers, store, fixture_ops = load("engine"), load("providers"), load("store"), load("fixture_ops")
cards = engine.cards          # one module object: the engine and the harness read the same card sets
D = providers.D
FORBIDDEN = {"proposal", "proposals", "vote", "votes", "confidence", "route", "prediction", "predictions",
             "nist_reference", "reference_label"}
Z95 = 1.6448536269514722   # one-sided 95%


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- fixture transport and adapter ----------------------------------------------------------------
class FixtureTransport:
    """Records every request the adapter would have sent; the response is the fixture the payload names."""
    kind = "fixture"

    def __init__(self):
        self.calls = []

    def post(self, url, headers, body, timeout=120):
        self.calls.append({"url": url, "model": body.get("model"), "n": len(self.calls) + 1})
        return {}


class FixtureAdapter(providers.Adapter):
    """The real adapter (guard, ceilings, identities, accounting, events) over a fixture response."""

    def __init__(self, protocol, out, response=None, protocol_sha256=None, phase="run", replay=False):
        state = os.path.join(out, "run_state.json")
        with open(state, "w", encoding="utf-8") as f:
            json.dump({"phase": phase}, f)
        super().__init__(protocol=protocol, transport=FixtureTransport(), accounting=os.path.join(out, "accounting.jsonl"),
                         state=state, events=os.path.join(out, "events.jsonl"), replay=replay, protocol_sha256=protocol_sha256)
        self.response = response or {"text": "fixture"}

    def _send(self, provider, model, prompt, tout):
        self.transport.post(f"fixture://{provider}", {}, {"model": model, "prompt": prompt, "max_tokens": tout})
        return self.response

    @staticmethod
    def _usage(provider, raw):
        cost = raw.get("provider_reported_cost_usd")
        return {"in": raw.get("input_tokens"), "out": raw.get("output_tokens")}, cost

    @staticmethod
    def _content(provider, raw):
        return raw.get("text", "")


def fixture_protocol(budget, models, roles):
    """The adapter's protocol shape from the harness budget: one price for every fixture model."""
    return {"budget": {"cap_usd": str(budget["cap_usd"]), "reserve_usd": str(budget["reserve_usd"]),
                       "prices": {m: {"in": str(budget["input_usd_per_million"]), "out": str(budget["output_usd_per_million"]),
                                      "provider": p} for m, p in models.items()},
                       "provider_caps": budget.get("provider_caps", {})},
            "roles": {r: {"in": budget["input_token_ceiling"], "out": budget["output_token_ceiling"]} for r in roles},
            "null": cards.NULL}


COUNCIL = {"anthropic/fixture-anthropic-v1": "anthropic", "google/fixture-google-v1": "google",
           "openrouter/fixture-mistral-v1": "openrouter"}


# ---- the card table -------------------------------------------------------------------------------
def card_table():
    """Every card id and whether fixtures reach it through pick(); E1 cannot fire with H_max = 0."""
    S = cards.make("r1", "openai/p", ["anthropic/a", "google/g", "openrouter/m"])
    S_open = cards.make("r1-open", "openai/p", ["anthropic/a", "google/g", "openrouter/m"], closed=[])
    hit = set()

    def run(set_, name, ctx, n=None):
        trace = []
        hit.add(engine.pick(set_, name, ctx, trace)[0])

    unit = {"id": "AC-1", "type": "control", "text": "Maintain an inventory."}
    for ctx in ({"unit": {**unit, "type": "withdrawn"}, "ext": {}, "gap": 0},
                {"unit": unit, "ext": {"complete": False}, "gap": 0},
                {"unit": unit, "ext": {"complete": False}, "gap": 2},
                {"unit": unit, "ext": {"complete": True, "work_object": False}, "gap": 0},
                {"unit": unit, "ext": {"complete": True, "work_object": True}, "gap": 0}):
        run(S, "intake", ctx)
    for i in range(1, 200):                      # the audit draw is a hash of the unit id: find both sides
        run(S, "audit", {"unit_id": f"AC-{i}"})
    run(S, "retrieval", {"cands": []})
    run(S, "retrieval", {"cands": ["PR.AA-01"]})
    e1 = False
    for needs_hub, hub_att in itertools.product((True, False), (0, 1, 2)):
        trace = []
        cid, _ = engine.pick(S, "evidence", {"prop": {"needs_hub": needs_hub, "quotes": [], "confidence": 0.9}, "unit": unit,
                                             "hub_att": hub_att}, trace)
        hit.add(cid)
        e1 |= cid == "E1"
    run(S, "evidence", {"prop": {"quotes": ["not in the text"], "confidence": 0.9}, "unit": unit, "hub_att": 0})
    run(S, "evidence", {"prop": {"quotes": [], "confidence": 0.2}, "unit": unit, "hub_att": 0})
    for cur in ({"decision": "approve"}, {"decision": "decline"}, None):
        run(S, "curator", {"cur": cur})
    vals = [{"answer": None}] + [{"answer": lv} for lv in cards.LEVELS] + [{"answer": "off_scale"}]
    for combo in itertools.product(vals, repeat=3):
        for set_ in (S, S_open):
            run(set_, "triad", {"votes": list(combo), "n": 3})
    nm, a, b = {"answer": None}, {"answer": "equal"}, {"answer": "subset_of"}
    run(S, "triad", {"votes": [nm, nm, a, a], "n": 4})       # the even-panel tie: T5-otherwise
    run(S, "triad", {"votes": [a, a, b, b], "n": 4})         # two levels, no level majority: T4
    for r in ({"ruling": "accept", "level": "equal"}, {"ruling": "no_match"}, {"ruling": "out_of_scope"}, None):
        run(S, "ruling", {"ruling": r})
    table = {}
    for cid in (c for ids in cards.card_ids().values() for c in ids):
        table[cid] = "PASS" if cid in hit else ("present_unreachable" if cid == "E1" and not e1 else "FAIL")
    return table


def print_table(table):
    print(f"{'card':<16} {'result':<20}")
    print("-" * 37)
    for cid, res in table.items():
        print(f"{cid:<16} {res:<20}")
    ok = all(r in ("PASS", "present_unreachable") for r in table.values())
    print("-" * 37)
    print(f"{sum(r == 'PASS' for r in table.values())} PASS, "
          f"{sum(r == 'present_unreachable' for r in table.values())} present_unreachable (E1: route is retrieval only, H_max = 0), "
          f"{sum(r == 'FAIL' for r in table.values())} FAIL")
    return 0 if ok else 1


# ---- the harness ----------------------------------------------------------------------------------
class Harness:
    def __init__(self, out):
        self.out = out
        for name in ("accounting.jsonl", "events.jsonl"):
            open(os.path.join(out, name), "a", encoding="utf-8").close()

    def path(self, name):
        return os.path.join(self.out, name)

    def write(self, name, value):
        with open(self.path(name), "w", encoding="utf-8") as f:
            json.dump(value, f, indent=1, sort_keys=True, ensure_ascii=False)
            f.write("\n")

    def finish(self, operation, result, code=0):
        self.write("result.json", {"operation": operation, "transport": "fixture", **result})
        return code

    # ---- provider_call: the budget guard over one fixture call -------------------------------
    def provider_call(self, p):
        provider, model, stage = p["provider"], p["model_id"], p["stage"]
        proto = fixture_protocol({**p, "cap_usd": p["global_allowance_usd"], "provider_caps": {provider: str(p["provider_allowance_usd"])}},
                                 {model: provider}, [stage])
        a = FixtureAdapter(proto, self.out, p["fixture_response"], phase=p.get("phase", "run"))
        spent, provider_spent = D(p["total_spent_usd"]), D(p.get("provider_spent_usd", "0"))
        a.spend = lambda: (spent, {provider: provider_spent})     # the harness states the ledger position
        got = {}

        def body():
            got["output"] = a.call(stage, model, "fixture-v1", "fixture prompt", providers.identity(stage, fixture=True),
                                   None, json_out=False)
        code = providers.run_pass(body)
        return self.finish("provider_call", {"transport_calls": a.transport.calls, "output": got.get("output"),
                                             "exit": code}, code)

    # ---- triad: a vote by the proposer's provider is never stored ----------------------------
    def triad(self, p):
        proposer = p["proposer"]["provider"]
        bad = engine.panel_violations(p["votes"], proposer)
        if bad:
            i, why = bad[0]
            print(f"refused: {why}: {p['votes'][i]['provider']} ({p['votes'][i]['model_id']}); proposer is {proposer}", file=sys.stderr)
            return self.finish("triad", {"refused": True, "stored_votes": [], "violations": [[i, why] for i, why in bad]}, 1)
        stored = [{"provider": v["provider"], "model_id": v["model_id"], "answer": v["answer"],
                   "unit_id": p["unit"]["id"], "candidate_id": p["candidate"]["id"]} for v in p["votes"]]
        return self.finish("triad", {"refused": False, "stored_votes": stored})

    # ---- protocol: freeze, then the first call carries the hash ------------------------------
    def protocol(self, p):
        mode = p["mode"]
        if mode == "freeze_then_fixture_call":
            protocol = p["protocol"]
            bad = split_violations(protocol)
            if bad:
                for fam, splits in bad:
                    print(f"split violation: family {fam} sits in {sorted(splits)}", file=sys.stderr)
                return self.finish("protocol", {"refused": True, "violations": [[f, sorted(s)] for f, s in bad]}, 3)
            raw = json.dumps(protocol, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
            with open(self.path("protocol.json"), "wb") as f:
                f.write(raw)
            sha = hashlib.sha256(raw).hexdigest()
            with open(self.path("protocol.sha256"), "w", encoding="utf-8") as f:
                f.write(sha + "\n")
        elif mode == "verify_then_fixture_call":
            with open(p["protocol_path"], "rb") as f:
                raw = f.read()
            with open(p["hash_path"], encoding="utf-8") as f:
                pinned = f.read().split()[0]
            sha = hashlib.sha256(raw).hexdigest()
            if sha != pinned:
                print(f"refused: protocol hash mismatch: file {sha}, pinned {pinned}", file=sys.stderr)
                return self.finish("protocol", {"refused": True, "protocol_sha256": sha, "pinned": pinned}, 1)
            protocol = json.loads(raw)
        else:
            return self.finish("protocol", {"refused": True, "reason": f"unknown mode {mode}"}, 2)
        b = protocol["budget"]
        model = "openai/fixture-proposer-v1"
        a = FixtureAdapter(fixture_protocol(b, {model: "openai"}, ["proposal"]), self.out, protocol_sha256=sha)
        a.event("protocol_frozen", protocol_sha256=sha, sample_size=protocol["sample_size"], k=protocol["k"])
        text = a.call("proposal", model, "propose-v1", "fixture prompt", providers.identity("proposal", fixture=True), None,
                      json_out=False)
        print(f"protocol frozen {sha} ({protocol['sample_size']} units, k {protocol['k']}); first call: {text}")
        return self.finish("protocol", {"refused": False, "protocol_sha256": sha})

    # ---- gold: blind packets, the council's label ----------------------------------------------
    def gold(self, p):
        mode = p["mode"]
        if mode == "assemble_then_label":
            packets = [blind_packet(item, p.get("rubric", "")) for item in p["items"]]
        else:
            packets = p["packets"]
        violations = [{"packet": i, "path": path} for i, pk in enumerate(packets) for path in forbidden_paths(pk)]
        if violations or mode == "audit":
            code = 1 if violations else 0
            if violations:
                print(f"refused: {len(violations)} forbidden field(s) in primary packets", file=sys.stderr)
            return self.finish("gold", {"refused": bool(violations), "violations": violations, "packets": packets,
                                        "primary_gold": []}, code)
        answers = p.get("fixture_council_answers") or []
        a = FixtureAdapter(fixture_protocol(GOLD_BUDGET, COUNCIL, ["council"]), self.out)
        gold = []
        for i, pk in enumerate(packets):
            answer = answers[i % len(answers)] if answers else cards.NULL
            a.response = {"text": json.dumps({"level": answer, "reason": "fixture"}), "input_tokens": 1, "output_tokens": 1}
            sha = digest(pk)
            votes = []
            for m in COUNCIL:                    # three voices, each its own call; the packet is what each one sees
                out = a.call("council", m, "council-v1", json.dumps(pk, sort_keys=True, ensure_ascii=False),
                             providers.identity("council", packet=sha, voice=m), None, packet=pk, packet_sha256=sha)
                votes.append({"model": m, "level": out.get("level")})
            levels = [v["level"] for v in votes]
            winner = max(set(levels), key=levels.count)
            label, reason = (winner, "majority") if levels.count(winner) >= 2 else (cards.NULL, "split")
            gold.append({"packet_sha256": sha, "label": label, "origin": "council", "human_validated": False,
                         "reason": reason, "votes": votes})
        self.write("primary_gold.json", gold)
        return self.finish("gold", {"refused": False, "violations": [], "packets": packets, "primary_gold": gold})

    # ---- gates: set r1 with every gate closed writes nothing automatic -----------------------
    def gates(self, p):
        spec = p["set"]
        S = engine.set_from_revision(p.get("set_revision", "r1"), spec, f"{spec['proposer_provider']}/fixture-proposer-v1",
                                     [f"{v}/fixture-{v}-v1" for v in spec["triad_providers"]])
        decisions = [engine.run_case(c, S) for c in p["cases"]]
        db = store.init(self.path("store.sqlite"))
        ledger = store.write_decisions(db, decisions)
        scores = [score(case) for case in p.get("score_cases", [])]
        self.write("scores.json", scores)
        return self.finish("gates", {"set_revision": S["rev"], "closed": S["params"]["closed"], "engine": S["engine"],
                                     "closed_gate_decisions": decisions, "ledger": ledger, "scores": scores})


GOLD_BUDGET = {"cap_usd": "60.00", "reserve_usd": "10.00", "input_token_ceiling": 1000, "output_token_ceiling": 1000,
               "input_usd_per_million": "0.001", "output_usd_per_million": "0.001"}


def split_violations(protocol):
    fams = {}
    for u in protocol["units"]:
        fams.setdefault(u["base_control"], set()).add(u["split"])
    return sorted((f, s) for f, s in fams.items() if len(s) != 1)


def scrub(value):
    """Drop every forbidden key, at any depth; values are copied, never reworded."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).lower() not in FORBIDDEN}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def blind_packet(item, rubric):
    """Source text, context, candidate and rubric only: nothing the run proposed, voted or predicted."""
    return {"unit": {"id": item["unit"]["id"], "text": item["unit"]["text"]},
            "candidate": {"id": item["candidate"]["id"], "text": item["candidate"]["text"]},
            "context": scrub(item.get("context") or {}), "rubric": rubric}


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


def wilson_lower(correct, n):
    if n == 0:
        return 0.0
    p = correct / n
    return (p + Z95 * Z95 / (2 * n) - Z95 * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))) / (1 + Z95 * Z95 / n)


def score(case):
    """One row of score.json: eligible outcomes at this level, the one-sided Wilson bound, the gate."""
    level = case["level"]
    rows = [o for o in case["outcomes"] if o.get("eligible") and o.get("prediction") == level]
    n, correct = len(rows), sum(o.get("reference_label") == level for o in rows)
    lower = wilson_lower(correct, n)
    opens = n >= 25 and lower >= 0.90        # the plan's bound: at least 25 outcomes, lower bound at least 0.90
    families = {}
    for o in rows:
        families[o["family"]] = families.get(o["family"], 0) + 1
    return {"id": case["id"], "level": level, "n": n, "correct": correct, "wilson_lower": lower, "gate_open": opens,
            "gate_decision": "closed_insufficient" if n < 25 else ("open" if opens else "closed"),
            "families_with_multiple_outcomes": sum(1 for k in families.values() if k > 1)}


OPERATIONS = {"provider_call": Harness.provider_call, "triad": Harness.triad, "protocol": Harness.protocol,
              "gold": Harness.gold, "gates": Harness.gates,
              "seed": lambda h, p: fixture_ops.seed(h, p, engine, providers, store),
              "replay": lambda h, p: fixture_ops.replay(h, p, engine, providers),
              "policy": lambda h, p: fixture_ops.policy(h, p, engine, providers, FixtureAdapter, fixture_protocol),
              "conformance": lambda h, p: fixture_ops.conformance(h, p, card_table, cards)}


def main(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--table", action="store_true")
    ap.add_argument("--request")
    ap.add_argument("--output-dir")
    args = ap.parse_args(argv)
    if args.table:
        return print_table(card_table())
    if not (args.request and args.output_dir):
        print(__doc__)
        return 2
    with open(args.request, encoding="utf-8") as f:
        req = json.load(f)
    h = Harness(args.output_dir)
    op = req.get("operation")
    if req.get("transport") != "fixture" or op not in OPERATIONS:
        print(f"unsupported: operation {op!r} transport {req.get('transport')!r}", file=sys.stderr)
        return h.finish(op, {"refused": True, "reason": "unsupported"}, 2)
    return OPERATIONS[op](h, req.get("payload") or {})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
