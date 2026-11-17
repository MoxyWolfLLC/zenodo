# drafted-by: gpt/gpt-6-astra
"""Black-box acceptance tests for sp800-53-to-csf2-2.

Required offline fixture interface:

    python -I -B runners/sp800-53-to-csf2/check.py \
        --request REQUEST.json --output-dir OUTPUT

Requests contain operation, transport="fixture", and payload. Operations must
exercise the production cards, storage, identity, replay, protocol, gold and
accounting paths. Only provider responses use the recording fixture transport.
The candidate exports result.json, events.jsonl and operation-specific artifacts
into OUTPUT. No test imports candidate code or reads candidate checkout files.

events.jsonl records call_started before invoking a transport, including fixture
transports. Replay additionally reports attempted outbound calls by role.
These checks establish fixture behavior, not completion of a paid experiment.

The entry point is currently absent. Invariants accept only Python's exact
missing-entry failure with no exported artifacts; outcomes reject it. Once the
entry exists, crashes, unsupported requests and missing artifacts fail every
test. Absence is not evidence that an implemented invariant has been exercised.

Drafting verification: the missing-entry exit and the two graph hashes were
checked against the current checkout and the pinned paper commit respectively.
The complete suite was not executed in the read-only drafting environment.
"""

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from decimal import Decimal


ENTRY = "runners/sp800-53-to-csf2/check.py"
LEVELS = ("subset_of", "intersects_with", "equal", "superset_of")
ANSWERS = LEVELS + ("no_relationship",)
VOTERS = ("anthropic", "google", "openrouter")
STAGES = ("extraction", "retrieval", "proposal", "vote", "council")
ROLES = ("model", "retriever", "council")
TARGET = "PR.AA-01"
SOURCE_TEXT = "Maintain an inventory of identities and credentials."
TARGET_TEXT = "Identities and credentials are managed."
APPENDIX_COMMIT = "4c3a05ec6"
APPENDIX_SOURCE = (
    "CKI/11 - Project Knowledge/Papers/models-propose-rules-decide/"
    "models-propose-rules-decide_1.1.md"
)

# Verbatim fenced Appendix C graph bodies, including their final LF.
GRAPH_SHA256 = {
    "run": "b81e841e771fca94b3a2c9699849084391d199df87df2a16d96d1058521ef70e",
    "policy": "2e63659d61bca9d9d73c6e7fa451181363adee42b6f566e95c113c6bf447a38c",
}

CARD_LISTS = {
    "intake": ["I1", "I3", "I4", "SS-scope", "I5-otherwise"],
    "audit": ["A1", "A2-otherwise"],
    "retrieval": ["SS-retrieval", "R2-otherwise"],
    "evidence": ["E1", "E2", "E3", "E4-otherwise"],
    "curator": ["K1", "K2", "K3-otherwise"],
    "triad": ["T1", "T2", "T2-gated", "T3", "T3-gated", "T4", "T5-otherwise"],
    "ruling": ["H1", "H2", "H3", "H4-otherwise"],
}

R3_CHANGE = {
    "parameter": "c_min",
    "base": "config.frozen.json",
    "split": "test",
    "formula": (
        "max(0, c_min - 0.2) if max(0, c_min - 0.2) != c_min "
        "else min(1, c_min + 0.2)"
    ),
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def checkout(test):
    value = os.environ.get("GOAL_CANDIDATE")
    test.assertTrue(value, "GOAL_CANDIDATE must name the candidate checkout")
    return Path(value).resolve()


def external(test, path, root):
    path = Path(path).resolve()
    test.assertTrue(path.is_relative_to(Path(root).resolve()), str(path))
    test.assertFalse(
        path.is_relative_to(checkout(test)),
        "Tests may not read or write candidate checkout files",
    )
    return path


def artifact(test, work, name):
    output = external(test, Path(work) / "out", work)
    path = external(test, output / name, output)
    test.assertTrue(path.is_file(), f"Missing exported artifact: {name}")
    return path


def read_json(test, work, name):
    return json.loads(artifact(test, work, name).read_text(encoding="utf-8"))


def read_jsonl(test, work, name):
    return [
        json.loads(line)
        for line in artifact(test, work, name).read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def invoke(test, root, operation, payload=None, *, absent_ok=False, code=0):
    candidate = checkout(test)
    external(test, root, root)
    work = Path(tempfile.mkdtemp(prefix="invocation-", dir=root)).resolve()
    output = work / "out"
    output.mkdir()
    request = work / "request.json"
    body = encoded({
        "operation": operation,
        "transport": "fixture",
        "payload": {} if payload is None else payload,
    })
    request.write_bytes(body)

    process = subprocess.run(
        [
            sys.executable, "-I", "-B", ENTRY,
            "--request", str(request), "--output-dir", str(output),
        ],
        cwd=str(candidate),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    test.assertEqual(
        external(test, request, work).read_bytes(), body,
        "Candidate modified its request",
    )

    missing_message = (
        f"can't open file '{candidate / ENTRY}': "
        "[Errno 2] No such file or directory"
    )
    missing = (
        process.returncode == 2
        and not process.stdout
        and len(process.stderr.strip().splitlines()) == 1
        and process.stderr.strip().endswith(missing_message)
    )
    if missing:
        test.assertEqual(list(external(test, output, work).iterdir()), [])
        test.assertEqual(
            {path.name for path in work.iterdir()}, {"request.json", "out"}
        )
        if absent_ok:
            return None
        test.fail(f"Required second-domain runner is absent: {ENTRY}")

    test.assertEqual(
        process.returncode, code,
        f"{operation}: unexpected exit\n"
        f"stdout:\n{process.stdout}\nstderr:\n{process.stderr}",
    )
    result = read_json(test, work, "result.json")
    test.assertEqual(result["operation"], operation)
    test.assertEqual(result["transport"], "fixture")
    return process, result, work


def votes(answer="subset_of"):
    return [
        {
            "provider": provider,
            "model_id": (
                "fixture-mistral-v1" if provider == "openrouter"
                else f"fixture-{provider}-v1"
            ),
            "answer": answer,
        }
        for provider in VOTERS
    ]


def case(unit_id, *, revision="r1", split="evaluation",
         confidence=0.9, answer="subset_of"):
    base = unit_id.split("(", 1)[0]
    return {
        "unit": {
            "id": unit_id,
            "base_control": base,
            "family": base,
            "catalog_family": base.split("-", 1)[0],
            "release": "fixture-release-1",
            "type": "enhancement" if "(" in unit_id else "control",
            "text": SOURCE_TEXT,
            "split": split,
        },
        "candidate": {"id": TARGET, "revision": "1", "text": TARGET_TEXT},
        "set_revision": revision,
        "extraction": {
            "complete": True, "work_object": True, "attempt_number": 1,
        },
        "retrieval": {
            "candidate_ids": [TARGET],
            "retriever_version": "fixture-retriever-1",
            "index_version": "fixture-index-1",
            "hub_revision": "1",
            "route_parameters": {"k": 8},
        },
        "proposal": {
            "provider": "openai",
            "model_id": "fixture-proposer-v1",
            "answer": answer,
            "confidence": confidence,
            "quotes": [SOURCE_TEXT],
            "conflict": False,
            "needs_hub": False,
        },
        "votes": votes(answer),
        "operational_ruling": None,
    }


def revision(threshold, *, closed=True):
    return {
        "c_min": threshold,
        "closed": list(ANSWERS) if closed else [],
        "question_versions": {
            "extraction": "extract-1",
            "proposal": "propose-1",
            "vote": "vote-1",
        },
        "scale_version": "ir8477-set-theory-1",
        "answer_scale": list(ANSWERS),
        "proposer_provider": "openai",
        "triad_providers": list(VOTERS),
    }


def call_identity(row):
    return row["unit_id"], row["candidate_id"], row["stage"], row["provider"]


def decision_identity(row):
    return row["unit_id"], row["candidate_id"]


def decision_value(row):
    return (
        row["set_revision"], row["route"], row["level"],
        row["reason"], row["card_id"],
    )


def indexed(test, rows, key):
    result = {key(row): row for row in rows}
    test.assertEqual(len(result), len(rows), "Duplicate exported identities")
    return result


def no_calls(test, work):
    events = read_jsonl(test, work, "events.jsonl")
    test.assertFalse(any(
        row["event"] in ("call_started", "outbound_attempt")
        for row in events
    ), "A forbidden call was attempted")


def wilson_lower(correct, n):
    if n == 0:
        return 0.0
    z = 1.6448536269514722
    p = correct / n
    return (
        p + z * z / (2 * n)
        - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    ) / (1 + z * z / n)


def normalized_label(label):
    # Only these noun substitutions and layout whitespace are ignored.
    aliases = (
        ("SP 800-53 work items", "Work items"),
        ("SP 800-53 controls", "units"),
        ("SP 800-53 control", "unit"),
        ("CSF 2.0 subcategories", "hub entries"),
        ("CSF 2.0 subcategory", "hub entry"),
        ("CSF 2.0 catalog", "hub"),
        ("Council reviewer", "Reviewer"),
        ("council reviewer", "reviewer"),
        ("no_relationship", "no match"),
    )
    for domain, original in aliases:
        label = label.replace(domain, original)
    return " ".join(label.replace("<br/>", " ").replace("<br>", " ").split())


_NODE = re.compile(
    r'([A-Za-z][A-Za-z0-9_]*)\s*'
    r'(\[\("([^"]*)"\)\]|\(\["([^"]*)"\]\)|'
    r'\["([^"]*)"\]|\{"([^"]*)"\})?'
)
_ARROW = re.compile(
    r'\s*(-->|--\s*"([^"]*)"\s*-->|-\.\s*"([^"]*)"\s*\.->)\s*'
)


def graph(test, source):
    """Independently project the pinned Mermaid dialect into six dimensions."""
    nodes, edges, lanes = {}, [], {}
    lane = None

    def node_at(line, offset):
        match = _NODE.match(line, offset)
        test.assertIsNotNone(match, f"Unsupported graph syntax: {line[offset:]}")
        name = match.group(1)
        if match.group(2):
            labels = match.groups()[2:]
            shape_index = next(
                i for i, value in enumerate(labels) if value is not None
            )
            shape = ("store", "terminal", "process", "decision")[shape_index]
            value = (shape, normalized_label(labels[shape_index]), lane)
            if name in nodes:
                test.assertEqual(nodes[name], value)
            nodes[name] = value
        return name, match.end()

    for original in source.splitlines():
        line = original.strip()
        if not line or line.startswith("%%") or line.startswith("flowchart "):
            continue
        subgraph = re.fullmatch(r'subgraph\s+(\w+)\["([^"]*)"\]', line)
        if subgraph:
            test.assertIsNone(lane, "Unexpected nested lane")
            lane = subgraph.group(1)
            test.assertNotIn(lane, lanes)
            lanes[lane] = normalized_label(subgraph.group(2))
            continue
        if line == "end":
            test.assertIsNotNone(lane)
            lane = None
            continue
        source_id, offset = node_at(line, 0)
        while offset < len(line):
            arrow = _ARROW.match(line, offset)
            test.assertIsNotNone(arrow, f"Unsupported graph edge: {line[offset:]}")
            target_id, offset = node_at(line, arrow.end())
            label = normalized_label(arrow.group(2) or arrow.group(3) or "")
            kind = "dotted" if arrow.group(1).startswith("-.") else "solid"
            edges.append((source_id, target_id, kind, label))
            source_id = target_id

    test.assertIsNone(lane, "Unclosed lane")
    for source_id, target_id, _, _ in edges:
        test.assertIn(source_id, nodes)
        test.assertIn(target_id, nodes)

    decisions = {
        name: value[1] for name, value in nodes.items() if value[0] == "decision"
    }
    card_ids = []
    for label in decisions.values():
        match = re.match(r"(SS-scope|SS-retrieval|[IERT]\d+(?:-gated)?)\b", label)
        if match:
            card_ids.append(match.group(1))

    return {
        "nodes": nodes,
        "edges": Counter(edges),
        "card_ids": sorted(card_ids),
        "lanes": lanes,
        "stores": sorted(
            name for name, value in nodes.items() if value[0] == "store"
        ),
        "decision_semantics": decisions,
    }


def protocol_fixture():
    units = []
    for number in range(150):
        prefix = "AC" if number < 75 else "AU"
        base = f"{prefix}-{number % 75 + 1}"
        split = ("calibration", "evaluation", "test")[number % 3]
        for enhancement in (False, True):
            units.append({
                "id": f"{base}(1)" if enhancement else base,
                "base_control": base,
                "family": base,
                "catalog_family": prefix,
                "type": "enhancement" if enhancement else "control",
                "split": split,
                "stratum": f"{prefix}:{'enhancement' if enhancement else 'base'}",
                "selection_probability": 0.5,
            })
    return {
        "sample_size": 300,
        "units": units,
        "split_seed": "goal-fixture-family-split-2",
        "answer_scale": list(ANSWERS),
        "k": 8,
        "calibration_revision_cap": 6,
        "adjudication_policy": (
            "Primary council labels use blind packets; disagreements are "
            "adjudicated. Untyped NIST associations are not five-class labels."
        ),
        "rubric": "Compare source and candidate using set-theory relationships.",
        "missed_links": {
            "estimand": (
                "Share of ruled true links recorded as no_relationship or "
                "never retrieved, over ruled true links."
            ),
            "r": 2,
            "non_retrieved_pool_size": 98,
            "non_retrieved_weight": 49,
            "inclusion_probabilities": {
                "retrieved": 1.0, "non_retrieved": 2 / 98,
            },
        },
        "anchoring": {
            "sessions": ["blind", "assisted"],
            "matched_pairs": True,
            "statistic": "paired difference in agreement with the proposal",
        },
        "double_ruled": {
            "fraction": 0.20,
            "unit_ids": [unit["id"] for unit in units[::5]],
        },
        "r3_change": copy.deepcopy(R3_CHANGE),
        "budget": {
            "cap_usd": "60.00",
            "reserve_usd": "10.00",
            "fixture_pricing": True,
            "input_token_ceiling": 1000,
            "output_token_ceiling": 1000,
            "input_usd_per_million": "0.001",
            "output_usd_per_million": "0.001",
        },
    }


def walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield "key", str(key)
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)
    elif isinstance(value, str):
        yield "value", value


class Conformance(unittest.TestCase):
    def test_graph_diff_against_pinned_appendix_c(self):
        """Outcome.
        Scenario: Given the run's store and the pinned Appendix C baseline, then `check.py` prints a conformance diff of nodes, edges, card ids, lanes, stores and decision semantics with zero deviations, or names each one as a fork
        """
        with tempfile.TemporaryDirectory(prefix="goal-conformance-") as root:
            _, _, seed_work = invoke(self, root, "seed", {
                "cases": [case("AC-1")],
                "sets": {"r1": revision(0.7)},
                "active_revision": "r1",
            })
            store = str(artifact(self, seed_work, "store.sqlite"))
            process, report, work = invoke(
                self, root, "conformance", {"store": store}
            )
            self.assertRegex(
                report["baseline_commit"],
                rf"^{APPENDIX_COMMIT}(?:[0-9a-f]{{31}})?$",
            )
            self.assertEqual(report["baseline_source"], APPENDIX_SOURCE)
            self.assertEqual(report["card_lists"], CARD_LISTS)
            self.assertEqual(report["E1"], {"present": True, "exercised": False})
            self.assertRegex(process.stdout.lower(), r"\bzero deviations\b")
            self.assertRegex(process.stdout, r"\bE1\b")
            self.assertIn("unexercised", process.stdout.lower())

            baseline = {}
            for name, expected_hash in GRAPH_SHA256.items():
                raw = artifact(self, work, f"baseline/{name}.mmd").read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_hash)
                baseline[name] = raw.decode("utf-8")
                expected = graph(self, baseline[name])
                actual = graph(
                    self,
                    artifact(self, work, f"graphs/{name}.mmd").read_text(
                        encoding="utf-8"
                    ),
                )
                self.assertEqual(set(report["diff"][name]), set(expected))
                for dimension in expected:
                    with self.subTest(graph=name, dimension=dimension):
                        self.assertEqual(actual[dimension], expected[dimension])
                        self.assertEqual(report["diff"][name][dimension], [])

            table = report["card_table"]
            self.assertEqual(
                set(table), {c for cards in CARD_LISTS.values() for c in cards}
            )
            for card, status in table.items():
                self.assertEqual(
                    status, "present_unreachable" if card == "E1" else "PASS"
                )

            mutations = (
                ("run", "nodes", 'START(["Pick next unit"])',
                 'START(["Discard next unit"])'),
                ("run", "edges", "REC --> LED", "TV --> LED"),
                ("run", "card_ids", 'E1{"E1 New hub entry,',
                 'E1{"E99 New hub entry,'),
                ("run", "lanes", 'subgraph PRO["Proposer model"]',
                 'subgraph PRO["Ledger writer"]'),
                ("run", "stores",
                 'WI[("Work items<br/>(gap and hub attempts)")]',
                 'WI["Work items<br/>(gap and hub attempts)"]'),
                ("run", "decision_semantics", "E3 Unverifiable, low",
                 "E3 Verifiable, low"),
                ("policy", "edges", 'REUSE -- "yes: reuse" --> SO',
                 'REUSE -- "yes: reuse" --> ASK'),
            )
            for name, dimension, old, new in mutations:
                with self.subTest(graph=name, fork=dimension):
                    changed = dict(baseline)
                    self.assertEqual(changed[name].count(old), 1)
                    changed[name] = changed[name].replace(old, new, 1)
                    process, fork, _ = invoke(self, root, "conformance", {
                        "store": store, "graphs": changed,
                    })
                    self.assertTrue(fork["diff"][name][dimension])
                    self.assertRegex(process.stdout.lower(), r"\bfork\b")
                    self.assertIn(dimension, process.stdout)

            damaged = dict(baseline)
            damaged["run"] += "\n"
            process, rejected, _ = invoke(self, root, "conformance", {
                "store": store, "baseline_override": damaged,
            }, code=6)
            self.assertIs(rejected["refused"], True)
            self.assertIn("hash", process.stderr.lower())


class Replay(unittest.TestCase):
    def test_replays_every_decision_with_network_disabled(self):
        """Outcome.
        Scenario: Given a store of live decisions and the network disabled for models, retriever and council, then every decision replays under its original revision with the same route, level and reason, and the call counter stays at zero
        """
        cases = [
            case("AC-1"),
            case("AU-1", revision="r2", answer="no_relationship"),
            case("CM-1", confidence=0.4),
            case("CP-1", revision="r2"),
            case("IA-1", revision="r2"),
            case("IR-1", revision="r2", answer="equal"),
        ]
        cases[3]["candidate"] = None
        cases[3]["retrieval"]["candidate_ids"] = []
        cases[4]["votes"][0]["answer"] = "off_scale"
        expected = {
            ("AC-1", TARGET): (
                "r1", "REVIEW", None, "level_gate_closed", "T3-gated"
            ),
            ("AU-1", TARGET): (
                "r2", "RECORD", None, "no_match_voted", "T2"
            ),
            ("CM-1", TARGET): (
                "r1", "REVIEW", None, "evidence_unclear", "E3"
            ),
            ("CP-1", None): (
                "r2", "RECORD", None,
                "no_match_nothing_retrieved", "SS-retrieval"
            ),
            ("IA-1", TARGET): (
                "r2", "REVIEW", None, "incomplete_triad", "T1"
            ),
            ("IR-1", TARGET): (
                "r2", "WRITE", "equal", "auto_accept", "T3"
            ),
        }
        with tempfile.TemporaryDirectory(prefix="goal-replay-") as root:
            _, seeded, work = invoke(self, root, "seed", {
                "cases": cases,
                "sets": {
                    "r1": revision(0.7),
                    "r2": revision(0.7, closed=False),
                    "r3": revision(1.0),
                },
                "active_revision": "r3",
            })
            live = seeded["live_decisions"]
            indexed(self, live, lambda row: row["decision_id"])
            self.assertEqual(
                {
                    key: decision_value(row)
                    for key, row in indexed(
                        self, live, decision_identity
                    ).items()
                },
                expected,
            )
            store = artifact(self, work, "store.sqlite")
            before = hashlib.sha256(store.read_bytes()).hexdigest()
            _, replayed, replay_work = invoke(self, root, "replay", {
                "store": str(store),
                "active_revision": "r3",
                "all_live_decisions": True,
            })
            rows = replayed["replayed_decisions"]
            self.assertEqual(
                Counter(row["decision_id"] for row in rows),
                Counter(row["decision_id"] for row in live),
            )
            self.assertEqual(
                {
                    key: decision_value(row)
                    for key, row in indexed(
                        self, rows, decision_identity
                    ).items()
                },
                expected,
            )
            self.assertEqual(replayed["defects"], [])
            self.assertEqual(replayed["live_decisions_after"], live)
            for field in (
                "outbound_attempts", "call_counter_delta",
                "completed_outbound_calls",
            ):
                self.assertEqual(replayed[field], dict.fromkeys(ROLES, 0))
            no_calls(self, replay_work)
            self.assertEqual(
                hashlib.sha256(
                    external(self, store, root).read_bytes()
                ).hexdigest(),
                before,
                "Replay modified its source store",
            )


class Policy(unittest.TestCase):
    def test_predeclared_change_reasks_exactly_the_expected_calls(self):
        """Outcome.
        Scenario: Given r3's predeclared threshold change, then the expected invalidations derived from the stage-identity rules equal the observed re-asked calls, including three new votes per candidate newly reaching the triad
        """
        scenarios = (
            (0.7, 0.5, (0.49, 0.5, 0.6, 0.699999, 0.7, 0.9)),
            (0.1, 0.0, (0.0, 0.05, 0.099999, 0.1, 0.5, 0.9)),
            (0.0, 0.2, (0.0, 0.1, 0.199999, 0.2, 0.7, 0.9)),
        )
        with tempfile.TemporaryDirectory(prefix="goal-policy-") as root:
            for before, after, confidences in scenarios:
                with self.subTest(before=before, after=after):
                    cases = [
                        case(
                            f"AC-{i}", revision="r2", split="test",
                            confidence=value,
                        )
                        for i, value in enumerate(confidences, 1)
                    ]
                    cases.extend([
                        case("AC-7", revision="r2", split="test"),
                        case("AC-8", revision="r2", split="test"),
                        case("AU-1", revision="r1", split="evaluation"),
                    ])
                    cases[6]["proposal"]["quotes"] = ["Not in the source text."]
                    cases[7]["proposal"]["conflict"] = True
                    cases[5]["operational_ruling"] = {
                        "ruling": "accept",
                        "level": "subset_of",
                        "origin": "card",
                        "card_id": "H1",
                        "packet_uses": ["proposal", "votes"],
                    }

                    # Derive expectations before the candidate runs.
                    eligible = {
                        f"AC-{i}" for i, value in enumerate(confidences, 1)
                        if value >= after
                    }
                    newly_reaching = {
                        f"AC-{i}" for i, value in enumerate(confidences, 1)
                        if after <= value < before
                    }
                    expected = Counter(
                        (unit, TARGET, "vote", provider)
                        for unit in eligible for provider in VOTERS
                    )
                    counts = dict.fromkeys(STAGES, 0)
                    counts["vote"] = 3 * len(eligible)

                    _, seeded, seed_work = invoke(self, root, "seed", {
                        "cases": cases,
                        "sets": {
                            "r1": revision(before), "r2": revision(before),
                        },
                        "active_revision": "r2",
                        "frozen_change": copy.deepcopy(R3_CHANGE),
                        "frozen_config": {
                            "c_min": before, "r3_c_min": after, "k": 8,
                        },
                    })
                    _, result, work = invoke(self, root, "policy", {
                        "store": str(artifact(
                            self, seed_work, "store.sqlite"
                        )),
                        "revision": "r3",
                    })
                    report = read_json(self, work, "policy_report.json")
                    self.assertEqual(report["revision"], "r3")
                    self.assertAlmostEqual(report["threshold_before"], before)
                    self.assertAlmostEqual(report["threshold_after"], after)
                    for field in ("expected_calls", "observed_calls"):
                        self.assertEqual(
                            Counter(map(call_identity, report[field])), expected
                        )
                    self.assertEqual(report["expected_counts"], counts)
                    self.assertEqual(report["observed_counts"], counts)
                    self.assertIs(report["equal"], True)
                    self.assertEqual(result["unplanned_calls"], [])
                    self.assertEqual(
                        Counter(
                            call_identity(row)
                            for row in report["observed_calls"]
                            if row["unit_id"] in newly_reaching
                        ),
                        Counter(
                            (unit, TARGET, "vote", provider)
                            for unit in newly_reaching for provider in VOTERS
                        ),
                    )

                    events = read_jsonl(self, work, "events.jsonl")
                    plans = [
                        i for i, event in enumerate(events)
                        if event["event"] == "expected_invalidations"
                    ]
                    starts = [
                        i for i, event in enumerate(events)
                        if event["event"] == "call_started"
                    ]
                    self.assertEqual(len(plans), 1)
                    self.assertEqual(len(starts), counts["vote"])
                    self.assertLess(plans[0], min(starts))
                    self.assertEqual(
                        Counter(map(call_identity, events[plans[0]]["calls"])),
                        expected,
                    )
                    self.assertEqual(
                        Counter(call_identity(events[i]) for i in starts),
                        expected,
                    )

                    old_outputs = indexed(
                        self, seeded["live_outputs"], call_identity
                    )
                    new_outputs = indexed(
                        self, result["live_outputs"], call_identity
                    )
                    self.assertEqual(
                        set(new_outputs), set(old_outputs) | set(expected)
                    )
                    for key, old_row in old_outputs.items():
                        if key in expected:
                            self.assertNotEqual(
                                new_outputs[key]["output_id"],
                                old_row["output_id"],
                            )
                        else:
                            self.assertEqual(
                                new_outputs[key]["output_id"],
                                old_row["output_id"],
                            )
                    old_eval = [
                        row for row in seeded["live_decisions"]
                        if row["unit_id"] == "AU-1"
                    ]
                    self.assertEqual(len(old_eval), 1)
                    self.assertEqual(
                        [
                            row for row in result["live_decisions"]
                            if row["unit_id"] == "AU-1"
                        ],
                        old_eval,
                    )

                    rerulings = report["rederived_rulings"]
                    self.assertEqual(len(rerulings), 1)
                    ruling = rerulings[0]
                    self.assertEqual(decision_identity(ruling), ("AC-6", TARGET))
                    self.assertNotEqual(
                        ruling["old_ruling_output_id"],
                        ruling["new_ruling_output_id"],
                    )
                    for prefix, outputs in (
                        ("old", old_outputs), ("new", new_outputs)
                    ):
                        self.assertEqual(
                            Counter(ruling[f"{prefix}_vote_output_ids"]),
                            Counter(
                                outputs[("AC-6", TARGET, "vote", provider)][
                                    "output_id"
                                ]
                                for provider in VOTERS
                            ),
                        )

                    accounting = read_jsonl(self, work, "accounting.jsonl")
                    self.assertEqual(
                        Counter(map(call_identity, accounting)), expected
                    )
                    self.assertTrue(all(not row["refused"] for row in accounting))
                    total = sum(
                        (Decimal(row["charged_usd"]) for row in accounting),
                        Decimal("0"),
                    )
                    self.assertGreaterEqual(total, Decimal("0"))
                    self.assertEqual(Decimal(report["cost_usd"]), total)

                    _, _, again_work = invoke(self, root, "policy", {
                        "store": str(artifact(self, work, "store.sqlite")),
                        "revision": "r3",
                    })
                    again = read_json(self, again_work, "policy_report.json")
                    self.assertEqual(again["expected_calls"], [])
                    self.assertEqual(again["observed_calls"], [])
                    self.assertEqual(again["rederived_rulings"], [])
                    self.assertEqual(Decimal(again["cost_usd"]), Decimal("0"))
                    no_calls(self, again_work)


class Gates(unittest.TestCase):
    def test_closed_gates_write_nothing_and_open_only_on_the_bound(self):
        """Invariant.
        Scenario: Given set r1 with every gate closed, then no automatic write and no voted no-relationship reaches the ledger, and a set-theory level opens only on a one-sided 95% Wilson lower bound above 0.90 over at least 25 eligible outcomes
        """
        score_cases, expected = [], {}
        for level in ANSWERS:
            for n, correct in (
                (0, 0), (24, 24), (25, 25), (25, 24), (50, 48), (50, 49)
            ):
                key = f"{level}:{n}:{correct}"
                wrong = "equal" if level != "equal" else "subset_of"
                outcomes = []
                for i in range(n):
                    base = f"AC-{i // 2 + 1}"
                    outcomes.append({
                        "unit_id": base if i % 2 == 0 else f"{base}(1)",
                        "candidate_id": TARGET,
                        "base_control": base,
                        "family": base,
                        "catalog_family": "AC",
                        "prediction": level,
                        "reference_label": level if i < correct else wrong,
                        "reference_semantics": "five_class",
                        "reference_origin": "council",
                        "eligible": True,
                    })
                outcomes.extend({
                    "unit_id": f"AU-{i + 1}",
                    "candidate_id": TARGET,
                    "base_control": f"AU-{i + 1}",
                    "family": f"AU-{i + 1}",
                    "catalog_family": "AU",
                    "prediction": level,
                    "reference_label": None,
                    "reference_semantics": "association",
                    "reference_origin": "nist",
                    "eligible": False,
                } for i in range(30))
                score_cases.append({
                    "id": key, "level": level, "outcomes": outcomes,
                })
                expected[key] = n, correct

        with tempfile.TemporaryDirectory(prefix="goal-gates-") as root:
            response = invoke(self, root, "gates", {
                "set_revision": "r1",
                "set": revision(0.7),
                "cases": [
                    case(f"AC-{i}", answer=level)
                    for i, level in enumerate(ANSWERS, 1)
                ],
                "score_cases": score_cases,
            }, absent_ok=True)
            if response is None:
                return
            _, result, _ = response
            decisions = indexed(
                self, result["closed_gate_decisions"], lambda row: row["unit_id"]
            )
            self.assertEqual(set(decisions), {
                f"AC-{i}" for i in range(1, len(ANSWERS) + 1)
            })
            for unit, row in decisions.items():
                null_case = unit == "AC-5"
                self.assertEqual(row["route"], "REVIEW")
                self.assertIsNone(row["level"])
                self.assertEqual(
                    row["reason"],
                    "nomatch_gate_closed" if null_case else "level_gate_closed",
                )
                self.assertEqual(
                    row["card_id"], "T2-gated" if null_case else "T3-gated"
                )
            for row in result["ledger"]:
                self.assertNotEqual(row["route"], "WRITE")
                self.assertNotIn(
                    row["reason"], ("auto_accept", "no_match_voted")
                )
                self.assertEqual(row["writer_kind"], "card")
                self.assertIn(
                    row["card_id"],
                    {card for cards in CARD_LISTS.values() for card in cards},
                )

            scores = indexed(self, result["scores"], lambda row: row["id"])
            self.assertEqual(set(scores), set(expected))
            for key, row in scores.items():
                with self.subTest(score=key):
                    n, correct = expected[key]
                    lower = wilson_lower(correct, n)
                    opens = n >= 25 and lower > 0.90
                    decision = (
                        "closed_insufficient" if n < 25
                        else "open" if opens else "closed"
                    )
                    self.assertEqual((row["n"], row["correct"]), (n, correct))
                    self.assertAlmostEqual(row["wilson_lower"], lower, places=8)
                    self.assertIs(row["gate_open"], opens)
                    self.assertEqual(row["gate_decision"], decision)
                    self.assertEqual(
                        row["families_with_multiple_outcomes"], n // 2
                    )


class Protocol(unittest.TestCase):
    def test_frozen_protocol_hash_and_family_preserving_splits(self):
        """Invariant.
        Scenario: Given the frozen protocol file, then its hash equals the hash recorded before the first model call, and every family's controls sit in one split
        """
        protocol = protocol_fixture()
        with tempfile.TemporaryDirectory(prefix="goal-protocol-") as root:
            response = invoke(self, root, "protocol", {
                "mode": "freeze_then_fixture_call",
                "protocol": protocol,
            }, absent_ok=True)
            if response is None:
                return
            process, result, work = response
            raw = artifact(self, work, "protocol.json").read_bytes()
            actual_hash = hashlib.sha256(raw).hexdigest()
            self.assertEqual(
                artifact(self, work, "protocol.sha256").read_text(
                    encoding="utf-8"
                ).strip(),
                actual_hash,
            )
            self.assertEqual(result["protocol_sha256"], actual_hash)
            self.assertIn(actual_hash, process.stdout)
            frozen = json.loads(raw)
            self.assertEqual(frozen, protocol)
            self.assertEqual(frozen["sample_size"], len(frozen["units"]))
            self.assertTrue(300 <= frozen["sample_size"] <= 400)
            self.assertEqual(len({u["id"] for u in frozen["units"]}), 300)

            families, strata = {}, {}
            for unit in frozen["units"]:
                families.setdefault(unit["base_control"], set()).add(
                    unit["split"]
                )
                strata.setdefault(unit["catalog_family"], set()).add(
                    unit["split"]
                )
            self.assertTrue(all(len(splits) == 1 for splits in families.values()))
            self.assertTrue(all(
                splits == {"calibration", "evaluation", "test"}
                for splits in strata.values()
            ))
            self.assertEqual(frozen["r3_change"], R3_CHANGE)
            self.assertEqual(frozen["k"], 8)

            events = read_jsonl(self, work, "events.jsonl")
            freezes = [
                i for i, row in enumerate(events)
                if row["event"] == "protocol_frozen"
            ]
            calls = [
                i for i, row in enumerate(events)
                if row["event"] == "call_started"
            ]
            self.assertEqual(len(freezes), 1)
            self.assertTrue(calls, "Must exercise the first-call boundary")
            self.assertLess(freezes[0], min(calls))
            self.assertEqual(
                events[freezes[0]]["protocol_sha256"], actual_hash
            )
            for i in calls:
                self.assertEqual(events[i]["protocol_sha256"], actual_hash)

            invalid = copy.deepcopy(protocol)
            invalid["units"][1]["split"] = "evaluation"
            process, refused, bad_work = invoke(self, root, "protocol", {
                "mode": "freeze_then_fixture_call",
                "protocol": invalid,
            }, code=3)
            self.assertIn("AC-1", process.stderr)
            self.assertIs(refused["refused"], True)
            no_calls(self, bad_work)

            # Even semantically equivalent bytes must fail the frozen hash.
            artifact(self, work, "protocol.json").write_bytes(raw + b"\n")
            _, refused, bad_work = invoke(self, root, "protocol", {
                "mode": "verify_then_fixture_call",
                "protocol_path": str(artifact(self, work, "protocol.json")),
                "hash_path": str(artifact(self, work, "protocol.sha256")),
            }, code=1)
            self.assertIs(refused["refused"], True)
            no_calls(self, bad_work)


class Gold(unittest.TestCase):
    def test_primary_gold_packets_are_blind(self):
        """Invariant.
        Scenario: Given the primary gold packets, then none contains a proposal, vote, confidence, route or prediction
        """
        forbidden = {
            "proposal", "proposals", "vote", "votes", "confidence", "route",
            "prediction", "predictions", "nist_reference", "reference_label",
        }
        taints = {
            key: f"PRIVATE_{key.upper()}_DO_NOT_SHOW_74931"
            for key in forbidden
        }
        items = []
        for number in (1, 2):
            item = {
                "unit": {"id": f"AC-{number}", "text": SOURCE_TEXT},
                "candidate": {"id": TARGET, "text": TARGET_TEXT},
                "reference_semantics": "association",
                "context": {
                    "source": "fixture-control-text",
                    "run_outputs": dict(taints),
                },
            }
            item.update(taints)
            items.append(item)

        with tempfile.TemporaryDirectory(prefix="goal-gold-") as root:
            response = invoke(self, root, "gold", {
                "mode": "assemble_then_label",
                "items": items,
                "rubric": "Compare only the supplied source and target texts.",
                "fixture_council_answers": ["subset_of", "intersects_with"],
            }, absent_ok=True)
            if response is None:
                return
            _, result, work = response
            packets = result["packets"]
            self.assertEqual(len(packets), len(items))
            self.assertEqual(
                Counter(packet["unit"]["id"] for packet in packets),
                Counter(("AC-1", "AC-2")),
            )
            for packet in packets:
                self.assertEqual(packet["unit"]["text"], SOURCE_TEXT)
                self.assertEqual(packet["candidate"]["text"], TARGET_TEXT)
                for kind, text in walk(packet):
                    if kind == "key":
                        self.assertNotIn(text.lower(), forbidden)
                    for taint in taints.values():
                        self.assertNotIn(taint, text)

            labels = result["primary_gold"]
            self.assertEqual(len(labels), len(packets))
            self.assertEqual(
                Counter(row["packet_sha256"] for row in labels),
                Counter(digest(packet) for packet in packets),
            )
            self.assertEqual(
                Counter(row["label"] for row in labels),
                Counter(("subset_of", "intersects_with")),
            )
            for row in labels:
                self.assertEqual(row["origin"], "council")
                self.assertIs(row["human_validated"], False)

            sent = [
                row["packet"]
                for row in read_jsonl(self, work, "events.jsonl")
                if row["event"] == "call_started" and row["role"] == "council"
            ]
            self.assertEqual(
                Counter(digest(packet) for packet in sent),
                Counter({digest(packet): 3 for packet in packets}),
                "Inspect the exact packets sent to all three council voices",
            )

            _, clean, _ = invoke(self, root, "gold", {
                "mode": "audit",
                "packets": packets,
                "assisted_packets": [
                    {"proposal": taints["proposal"], "votes": taints["votes"]}
                ],
            })
            self.assertEqual(clean["violations"], [])

            for key in sorted(forbidden):
                with self.subTest(contamination=key):
                    dirty = copy.deepcopy(packets)
                    dirty[0]["context"] = {"nested": [{key: taints[key]}]}
                    _, rejected, audit_work = invoke(self, root, "gold", {
                        "mode": "audit_then_label",
                        "packets": dirty,
                        "fixture_council_answers": ["equal", "equal"],
                    }, code=1)
                    self.assertTrue(rejected["violations"])
                    self.assertEqual(rejected["primary_gold"], [])
                    no_calls(self, audit_work)


class Budget(unittest.TestCase):
    def test_guard_refuses_a_call_past_the_allowance(self):
        """Invariant.
        Scenario: Given the budget guard and a remaining allowance smaller than a call's maximum expected charge, then the call is refused before it is made and the refusal is written to the accounting log
        """
        base = {
            "global_allowance_usd": "60.00",
            "reserve_usd": "10.00",
            "input_token_ceiling": 1000,
            "output_token_ceiling": 1000,
            "input_usd_per_million": "10.00",
            "output_usd_per_million": "30.00",
            "fixture_response": {
                "text": "fixture",
                "input_tokens": 1,
                "output_tokens": 1,
                "provider_reported_cost_usd": "0.00004",
            },
        }
        maximum = (
            Decimal(base["input_token_ceiling"])
            * Decimal(base["input_usd_per_million"])
            + Decimal(base["output_token_ceiling"])
            * Decimal(base["output_usd_per_million"])
        ) / Decimal(1_000_000)
        providers = (
            ("openai", "20.00", "proposal"),
            ("anthropic", "12.00", "vote"),
            ("google", "8.00", "vote"),
            ("openrouter", "20.00", "vote"),
        )
        first = True
        with tempfile.TemporaryDirectory(prefix="goal-budget-") as root:
            for provider, allowance, stage in providers:
                limit = Decimal(allowance)
                scenarios = (
                    ("reserve", "49.97", "0", "evaluation", 4),
                    (
                        "provider", str(limit - Decimal("0.03")),
                        str(limit - Decimal("0.03")), "evaluation", 4,
                    ),
                    ("exact_boundary", "49.96", "0", "evaluation", 0),
                    ("reserve_released", "49.97", "0", "deliverables", 0),
                    ("global", "59.97", "0", "deliverables", 4),
                )
                for name, spent, provider_spent, phase, code in scenarios:
                    with self.subTest(provider=provider, boundary=name):
                        reserve = (
                            Decimal("0") if phase == "deliverables"
                            else Decimal("10")
                        )
                        remaining = min(
                            Decimal("60") - Decimal(spent) - reserve,
                            limit - Decimal(provider_spent),
                        )
                        payload = dict(
                            base,
                            provider=provider,
                            model_id=(
                                "fixture-mistral-v1" if provider == "openrouter"
                                else f"fixture-{provider}-v1"
                            ),
                            stage=stage,
                            phase=phase,
                            provider_allowance_usd=allowance,
                            total_spent_usd=spent,
                            provider_spent_usd=provider_spent,
                        )
                        response = invoke(
                            self, root, "provider_call", payload,
                            absent_ok=first, code=code,
                        )
                        first = False
                        if response is None:
                            return
                        process, result, work = response
                        accounting = read_jsonl(self, work, "accounting.jsonl")
                        self.assertEqual(len(accounting), 1)
                        row = accounting[0]
                        self.assertEqual(row["provider"], provider)
                        self.assertEqual(row["model"], payload["model_id"])
                        self.assertEqual(row["stage"], stage)
                        self.assertEqual(
                            Decimal(row["max_expected_charge_usd"]), maximum
                        )
                        self.assertEqual(
                            Decimal(row["remaining_usd"]), remaining
                        )
                        starts = [
                            event for event in read_jsonl(
                                self, work, "events.jsonl"
                            ) if event["event"] == "call_started"
                        ]
                        if code == 4:
                            self.assertLess(remaining, maximum)
                            refusal = re.search(
                                r"(?m)^refused: budget ([0-9]+(?:\.[0-9]+)?)\s*$",
                                process.stderr,
                            )
                            self.assertIsNotNone(refusal)
                            self.assertEqual(
                                Decimal(refusal.group(1)), remaining
                            )
                            self.assertEqual(starts, [])
                            self.assertEqual(result["transport_calls"], [])
                            self.assertIsNone(result["output"])
                            self.assertIs(row["refused"], True)
                            self.assertEqual(row["evidence"], "refused")
                            self.assertEqual(
                                Decimal(row["charged_usd"]), Decimal("0")
                            )
                            no_calls(self, work)
                        else:
                            self.assertGreaterEqual(remaining, maximum)
                            self.assertEqual(len(starts), 1)
                            self.assertEqual(len(result["transport_calls"]), 1)
                            self.assertEqual(result["output"], "fixture")
                            self.assertIs(row["refused"], False)
                            self.assertEqual(
                                row["evidence"], "provider_reported"
                            )
                            self.assertEqual(
                                Decimal(row["charged_usd"]), Decimal("0.00004")
                            )

            for evidence, response, expected_charge in (
                (
                    "usage_priced",
                    {"text": "fixture", "input_tokens": 1, "output_tokens": 1},
                    Decimal("0.00004"),
                ),
                ("unpriced", {"text": "fixture"}, maximum),
            ):
                with self.subTest(evidence=evidence):
                    payload = dict(
                        base,
                        provider="openai",
                        model_id="fixture-openai-v1",
                        stage="proposal",
                        phase="evaluation",
                        provider_allowance_usd="20.00",
                        provider_spent_usd="0",
                        total_spent_usd="49.96",
                        fixture_response=response,
                    )
                    _, result, work = invoke(
                        self, root, "provider_call", payload
                    )
                    accounting = read_jsonl(self, work, "accounting.jsonl")
                    self.assertEqual(len(accounting), 1)
                    self.assertEqual(accounting[0]["evidence"], evidence)
                    self.assertEqual(
                        Decimal(accounting[0]["charged_usd"]), expected_charge
                    )
                    self.assertEqual(len(result["transport_calls"]), 1)


class Triad(unittest.TestCase):
    def test_proposer_never_votes(self):
        """Invariant.
        Scenario: A stored vote whose provider equals the proposer's provider must never happen
        """
        payload = {
            "proposer": {
                "provider": "openai", "model_id": "fixture-proposer-v1",
            },
            "unit": {"id": "AC-1", "text": SOURCE_TEXT},
            "candidate": {"id": TARGET, "text": TARGET_TEXT},
            "votes": votes(),
        }
        with tempfile.TemporaryDirectory(prefix="goal-triad-") as root:
            response = invoke(self, root, "triad", payload, absent_ok=True)
            if response is None:
                return
            _, accepted, _ = response
            stored = accepted["stored_votes"]
            self.assertEqual(len(stored), 3)
            self.assertEqual(
                Counter(row["provider"] for row in stored), Counter(VOTERS)
            )
            self.assertTrue(all(
                row["provider"] != payload["proposer"]["provider"]
                for row in stored
            ))

            for seat in range(3):
                with self.subTest(conflicting_seat=seat):
                    invalid = copy.deepcopy(payload)
                    invalid["votes"][seat]["provider"] = "openai"
                    invalid["votes"][seat]["model_id"] = (
                        "fixture-different-openai-model"
                    )
                    _, rejected, _ = invoke(
                        self, root, "triad", invalid, code=1
                    )
                    self.assertIs(rejected["refused"], True)
                    self.assertTrue(all(
                        row["provider"] != invalid["proposer"]["provider"]
                        for row in rejected["stored_votes"]
                    ))


if __name__ == "__main__":
    unittest.main()
