# drafted-by: gpt/gpt-6-astra
"""Black-box acceptance tests for the second-domain runner.

Fixture CLI contract introduced by these tests:

    python -I -B runners/sp800-53-to-csf2/check.py \
        --request REQUEST.json --output-dir OUTPUT

REQUEST contains {"operation": ..., "transport": "fixture", "payload": ...}.
The fixture dispatcher must exercise the runner's actual cards, store, replay,
policy, protocol, gold and provider code. It must not implement parallel rules
inside the test dispatcher. All mutable state and exports belong under OUTPUT.
The ordinary, argument-free check.py entry remains the offline conformance check.

Each invocation exports result.json and any artifacts asserted below. Refused
operations also export their audit evidence. Fixture transport supplies canned
provider responses; it does not authorize paid calls.

The runner is currently absent. Only CPython's exact missing-entry failure,
with no output artifacts, satisfies an invariant vacuously. Other failures are
never treated as absence. The three outcome tests require an implemented runner.

The missing-entry behavior and Appendix C source pins were inspected during
drafting. This complete suite has not been executed in the read-only drafting
environment. Fixture success does not establish completion of a paid experiment.
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

APPENDIX_COMMIT = "80b777b74c5615c6cae75cd2d2e48d07a7705f3f"
APPENDIX_SOURCE = (
    "CKI/11 - Project Knowledge/Papers/models-propose-rules-decide/"
    "models-propose-rules-decide_1.1.md"
)
# SHA-256 of each verbatim fenced Appendix C block, including its final LF.
APPENDIX_HASHES = {
    "run": "b81e841e771fca94b3a2c9699849084391d199df87df2a16d96d1058521ef70e",
    "policy": "2e63659d61bca9d9d73c6e7fa451181363adee42b6f566e95c113c6bf447a38c",
}

LEVELS = ("subset_of", "intersects_with", "equal", "superset_of")
ANSWERS = LEVELS + ("no_relationship",)
VOTERS = ("anthropic", "google", "openrouter")
CALL_ROLES = ("model", "retriever", "council")
STAGES = ("extraction", "retrieval", "proposal", "triad")
CANDIDATE = "PR.AA-01"
UNIT_TEXT = "Maintain an inventory of identities and credentials."
CANDIDATE_TEXT = "Identities and credentials are managed."
DIMENSIONS = (
    "nodes", "edges", "card_ids", "lanes", "stores", "decision_semantics"
)
CARD_LISTS = {
    "intake": ["I1", "I3", "I4", "SS-scope", "I5-otherwise"],
    "audit": ["A1", "A2-otherwise"],
    "retrieval": ["SS-retrieval", "R2-otherwise"],
    "evidence": ["E1", "E2", "E3", "E4-otherwise"],
    "curator": ["K1", "K2", "K3-otherwise"],
    "triad": [
        "T1", "T2", "T2-gated", "T3", "T3-gated", "T4", "T5-otherwise"
    ],
    "ruling": ["H1", "H2", "H3", "H4-otherwise"],
}


def encoded(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def candidate_checkout(test):
    value = os.environ.get("GOAL_CANDIDATE")
    test.assertTrue(value, "GOAL_CANDIDATE must name the candidate checkout")
    return Path(value).resolve()


def artifact(test, work, name):
    """Resolve only exported artifacts; reject links escaping the temporary area."""
    root = Path(os.path.abspath(work))
    target = (root / "out" / name).resolve()
    test.assertTrue(
        target.is_relative_to(root),
        f"Artifact escaped the external workspace: {name}",
    )
    test.assertFalse(
        target.is_relative_to(candidate_checkout(test)),
        f"Artifact points into the candidate checkout: {name}",
    )
    test.assertTrue(target.is_file(), f"Missing exported artifact: {name}")
    return target


def read_json(test, work, name):
    return json.loads(artifact(test, work, name).read_text(encoding="utf-8"))


def read_jsonl(test, work, name):
    text = artifact(test, work, name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def invoke(test, root, operation, payload=None, *, absent_ok=False, code=0):
    checkout = candidate_checkout(test)
    work = Path(tempfile.mkdtemp(prefix="invocation-", dir=root)).resolve()
    test.assertFalse(work.is_relative_to(checkout))
    output = work / "out"
    output.mkdir()
    request = work / "request.json"
    body = encoded({
        "operation": operation,
        "transport": "fixture",
        "payload": {} if payload is None else payload,
    })
    request.write_bytes(body)

    completed = subprocess.run(
        [
            sys.executable, "-I", "-B", ENTRY,
            "--request", str(request), "--output-dir", str(output),
        ],
        cwd=str(checkout),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    test.assertEqual(request.read_bytes(), body, "Candidate modified its input")

    missing_message = (
        f"can't open file '{checkout / ENTRY}': "
        "[Errno 2] No such file or directory"
    )
    missing = (
        completed.returncode == 2
        and not completed.stdout
        and len(completed.stderr.strip().splitlines()) == 1
        and completed.stderr.strip().endswith(missing_message)
    )
    if missing:
        test.assertEqual(list(output.iterdir()), [])
        if absent_ok:
            return None
        test.fail(f"Required second-domain runner is absent: {ENTRY}")

    test.assertEqual(
        completed.returncode,
        code,
        f"{operation}: unexpected exit\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
    )
    result = read_json(test, work, "result.json")
    test.assertEqual(result["operation"], operation)
    test.assertEqual(result["transport"], "fixture")
    return completed, result, work


def votes(answer="subset_of"):
    return [
        {
            "provider": provider,
            "model_id": f"fixture-{provider}-v1",
            "answer": answer,
        }
        for provider in VOTERS
    ]


def case(unit_id, *, revision="r1", confidence=0.9, answer="subset_of"):
    return {
        "unit": {
            "id": unit_id,
            "family": unit_id.split("-", 1)[0],
            "release": "fixture-release-1",
            "type": "control",
            "text": UNIT_TEXT,
        },
        "candidate": {"id": CANDIDATE, "text": CANDIDATE_TEXT},
        "set_revision": revision,
        "extraction": {"complete": True, "work_object": True},
        "retrieval": {
            "candidate_ids": [CANDIDATE],
            "retriever_version": "fixture-retriever-1",
            "index_version": "fixture-index-1",
            "hub_revision": "fixture-hub-1",
        },
        "proposal": {
            "provider": "openai",
            "model_id": "fixture-proposer-v1",
            "answer": answer,
            "confidence": confidence,
            "quotes": [UNIT_TEXT],
            "conflict": False,
            "needs_hub": False,
        },
        "votes": votes(answer),
        "operational_ruling": None,
    }


def revision(threshold):
    return {
        "c_min": threshold,
        "closed": list(ANSWERS),
        "question_versions": {
            "extraction": "extract-1",
            "proposal": "propose-1",
            "triad": "vote-1",
        },
        "scale_version": "ir8477-set-theory-1",
        "proposer_provider": "openai",
        "triad_providers": list(VOTERS),
    }


def call_identity(row):
    return (
        row["unit_id"], row["candidate_id"], row["stage"], row["provider"]
    )


def decision_identity(row):
    return row["unit_id"], row["candidate_id"]


def decision_value(row):
    return (
        row["set_revision"], row["route"], row["level"],
        row["reason"], row["card_id"],
    )


def wilson_lower(correct, n):
    if not n:
        return 0.0
    z = 1.6448536269514722
    p = correct / n
    return (
        p + z * z / (2 * n)
        - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    ) / (1 + z * z / n)


def normalized_label(label):
    # Only these explicit domain substitutions are permitted.
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
    """Read the pinned graphs' small Mermaid dialect without dropping semantics."""
    nodes, edges, lanes = {}, [], {}
    lane = None

    def node_at(line, offset):
        match = _NODE.match(line, offset)
        test.assertIsNotNone(match, f"Unsupported graph syntax: {line[offset:]}")
        name = match.group(1)
        if match.group(2):
            labels = match.groups()[2:]
            shape_index = next(i for i, value in enumerate(labels) if value is not None)
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
        "stores": sorted(name for name, value in nodes.items() if value[0] == "store"),
        "decision_semantics": decisions,
    }


def protocol_fixture():
    assignments = (
        ("AC", "calibration"), ("AU", "calibration"),
        ("CM", "evaluation"), ("CP", "evaluation"),
        ("IA", "test"), ("IR", "test"),
    )
    units = [
        {
            "id": f"{family}-{number}",
            "family": family,
            "split": split,
            "stratum": family,
            "selection_probability": 1.0,
        }
        for family, split in assignments
        for number in range(1, 51)
    ]
    return {
        "sample_size": 300,
        "units": units,
        "split_seed": "goal-fixture-split-1",
        "answer_scale": list(ANSWERS),
        "adjudication_policy": "Blind primary council; disagreements adjudicated.",
        "rubric": "Compare the two supplied texts using set-theory relationships.",
        "missed_links": {
            "estimand": "Links among non-retrieved control-subcategory pairs",
            "inclusion_probability": 0.25,
        },
        "anchoring": {"sessions": ["blind", "assisted"], "matched_pairs": True},
        "double_ruled": {
            "fraction": 0.20,
            "unit_ids": [unit["id"] for unit in units[::5]],
        },
        "r3_change": {"parameter": "c_min", "before": 0.7, "after": 0.5},
        "budget": {
            "cap_usd": "60.00",
            "reserve_usd": "5.00",
            "max_calls": 40,
            "providers": {
                provider: {
                    "model_id": f"fixture-{provider}-v1",
                    "calls": 10,
                    "input_token_ceiling": 1000,
                    "output_token_ceiling": 1000,
                    "input_usd_per_million": "250.00",
                    "output_usd_per_million": "250.00",
                    "allowance_usd": allowance,
                }
                for provider, allowance in (
                    ("openai", "20.00"), ("anthropic", "12.00"),
                    ("google", "8.00"), ("openrouter", "20.00"),
                )
            },
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
            completed, report, work = invoke(self, root, "conformance")
            self.assertEqual(report["baseline_commit"], APPENDIX_COMMIT)
            self.assertEqual(report["baseline_source"], APPENDIX_SOURCE)
            self.assertEqual(report["card_lists"], CARD_LISTS)
            self.assertEqual(report["E1"], {"present": True, "exercised": False})
            self.assertRegex(completed.stdout.lower(), r"\bzero deviations\b")

            baseline = {}
            for name in ("run", "policy"):
                with self.subTest(graph=name):
                    raw = artifact(self, work, f"baseline/{name}.mmd").read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), APPENDIX_HASHES[name])
                    baseline[name] = raw.decode("utf-8")
                    actual = artifact(self, work, f"graphs/{name}.mmd").read_text(
                        encoding="utf-8"
                    )
                    expected_graph = graph(self, baseline[name])
                    actual_graph = graph(self, actual)
                    self.assertEqual(set(report["diff"][name]), set(DIMENSIONS))
                    for dimension in DIMENSIONS:
                        self.assertEqual(actual_graph[dimension], expected_graph[dimension])
                        self.assertEqual(report["diff"][name][dimension], [])

            mutations = (
                ("nodes", 'START(["Pick next unit"])', 'START(["Discard next unit"])'),
                ("edges", "REC --> LED", "TV --> LED"),
                ("card_ids", 'E1{"E1 New hub entry,', 'E1{"E99 New hub entry,'),
                ("lanes", 'subgraph PRO["Proposer model"]', 'subgraph PRO["Ledger writer"]'),
                (
                    "stores",
                    'WI[("Work items<br/>(gap and hub attempts)")]',
                    'WI["Work items<br/>(gap and hub attempts)"]',
                ),
                (
                    "decision_semantics",
                    "E3 Unverifiable, low",
                    "E3 Verifiable, low",
                ),
            )
            for dimension, old, new in mutations:
                with self.subTest(fork=dimension):
                    self.assertIn(old, baseline["run"])
                    changed = dict(baseline)
                    changed["run"] = changed["run"].replace(old, new, 1)
                    process, fork, _ = invoke(
                        self, root, "conformance", {"graphs": changed}
                    )
                    self.assertTrue(fork["diff"]["run"][dimension])
                    self.assertRegex(process.stdout.lower(), r"\bfork\b")
                    self.assertIn(dimension, process.stdout)

            damaged = dict(baseline)
            damaged["run"] += "\n"
            process, rejected, _ = invoke(
                self, root, "conformance",
                {"baseline_override": damaged},
                code=6,
            )
            self.assertTrue(rejected["refused"])
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
        ]
        cases[3]["candidate"] = None
        cases[3]["retrieval"]["candidate_ids"] = []
        cases[4]["votes"][0]["answer"] = "off_scale"
        expected = {
            ("AC-1", CANDIDATE): (
                "r1", "REVIEW", None, "level_gate_closed", "T3-gated"
            ),
            ("AU-1", CANDIDATE): (
                "r2", "REVIEW", None, "nomatch_gate_closed", "T2-gated"
            ),
            ("CM-1", CANDIDATE): (
                "r1", "REVIEW", None, "evidence_unclear", "E3"
            ),
            ("CP-1", None): (
                "r2", "RECORD", None, "no_match_nothing_retrieved", "SS-retrieval"
            ),
            ("IA-1", CANDIDATE): (
                "r2", "REVIEW", None, "incomplete_triad", "T1"
            ),
        }

        with tempfile.TemporaryDirectory(prefix="goal-replay-") as root:
            _, seeded, work = invoke(self, root, "seed", {
                "cases": cases,
                "sets": {
                    "r1": revision(0.7),
                    "r2": revision(0.7),
                    "r3": revision(1.0),
                },
                "active_revision": "r3",
            })
            live = seeded["live_decisions"]
            self.assertEqual(len(live), len(expected))
            self.assertEqual(len({row["decision_id"] for row in live}), len(expected))
            self.assertEqual(
                {decision_identity(row): decision_value(row) for row in live},
                expected,
            )
            store = artifact(self, work, "store.sqlite")
            _, replayed, replay_work = invoke(self, root, "replay", {
                "store": str(store),
                "active_revision": "r3",
                "all_live_decisions": True,
            })
            rows = replayed["replayed_decisions"]
            self.assertEqual(len(rows), len(live))
            self.assertEqual(
                {row["decision_id"] for row in rows},
                {row["decision_id"] for row in live},
            )
            self.assertEqual(
                {decision_identity(row): decision_value(row) for row in rows},
                expected,
            )
            self.assertEqual(replayed["defects"], [])
            self.assertEqual(replayed["live_decisions_after"], live)
            self.assertEqual(replayed["outbound_attempts"], dict.fromkeys(CALL_ROLES, 0))
            self.assertEqual(replayed["call_counter_delta"], dict.fromkeys(CALL_ROLES, 0))
            events = read_jsonl(self, replay_work, "events.jsonl")
            self.assertFalse(any(
                event["event"] in ("call_started", "outbound_attempt")
                for event in events
            ))

            # Omit an essential stored output in an isolated copy. Replay must
            # refuse instead of fabricating an output or silently omitting a row.
            _, broken, _ = invoke(self, root, "replay", {
                "store": str(store),
                "all_live_decisions": True,
                "omit_output_in_copy": {
                    "unit_id": "AC-1",
                    "candidate_id": CANDIDATE,
                    "stage": "proposal",
                },
            }, code=5)
            self.assertTrue(broken["defects"])
            self.assertEqual(broken["live_decisions_after"], live)
            self.assertEqual(
                broken["completed_outbound_calls"], dict.fromkeys(CALL_ROLES, 0)
            )


class Policy(unittest.TestCase):
    def test_predeclared_change_reasks_exactly_the_expected_calls(self):
        """Outcome.
        Scenario: Given r3's predeclared threshold change, then the expected invalidations derived from the stage-identity rules equal the observed re-asked calls, including three new votes per candidate newly reaching the triad
        """
        confidences = (0.49, 0.5, 0.6, 0.699999, 0.7, 0.9, 0.6, 0.6)
        cases = [
            case(f"AC-{i}", revision="r2", confidence=confidence)
            for i, confidence in enumerate(confidences, 1)
        ]
        cases[6]["proposal"]["quotes"] = ["This quote is absent from the control."]
        cases[7]["proposal"]["conflict"] = True

        # This oracle is fixed before any candidate operation. The confidence
        # boundary is inclusive at 0.5 and exclusive at 0.7.
        expected = Counter(
            (f"AC-{i}", CANDIDATE, "triad", provider)
            for i in (2, 3, 4)
            for provider in VOTERS
        )
        expected_counts = dict.fromkeys(STAGES, 0)
        expected_counts["triad"] = 9

        with tempfile.TemporaryDirectory(prefix="goal-policy-") as root:
            _, _, seed_work = invoke(self, root, "seed", {
                "cases": cases,
                "sets": {"r2": revision(0.7), "r3": revision(0.5)},
                "active_revision": "r2",
                "frozen_change": {
                    "revision": "r3", "parameter": "c_min",
                    "before": 0.7, "after": 0.5,
                },
            })
            _, result, work = invoke(self, root, "policy", {
                "store": str(artifact(self, seed_work, "store.sqlite")),
                "revision": "r3",
            })
            report = read_json(self, work, "policy_report.json")
            self.assertEqual(report["revision"], "r3")
            self.assertEqual(Counter(map(call_identity, report["expected_calls"])), expected)
            self.assertEqual(Counter(map(call_identity, report["observed_calls"])), expected)
            self.assertEqual(report["expected_counts"], expected_counts)
            self.assertEqual(report["observed_counts"], expected_counts)
            self.assertIs(report["equal"], True)
            self.assertGreaterEqual(Decimal(report["cost_usd"]), Decimal("0"))

            events = read_jsonl(self, work, "events.jsonl")
            plans = [i for i, row in enumerate(events) if row["event"] == "expected_invalidations"]
            starts = [i for i, row in enumerate(events) if row["event"] == "call_started"]
            self.assertEqual(len(plans), 1)
            self.assertEqual(len(starts), 9)
            self.assertLess(plans[0], min(starts))
            self.assertEqual(
                Counter(map(call_identity, events[plans[0]]["calls"])), expected
            )
            self.assertEqual(
                Counter(call_identity(events[i]) for i in starts), expected
            )
            self.assertEqual(result["unplanned_calls"], [])

            # Repeating the same revision reuses the votes just obtained.
            _, _, again_work = invoke(self, root, "policy", {
                "store": str(artifact(self, work, "store.sqlite")),
                "revision": "r3",
            })
            again = read_json(self, again_work, "policy_report.json")
            self.assertEqual(again["expected_calls"], [])
            self.assertEqual(again["observed_calls"], [])
            self.assertEqual(again["observed_counts"], dict.fromkeys(STAGES, 0))


class Gates(unittest.TestCase):
    def test_closed_gates_write_nothing_and_open_only_on_the_bound(self):
        """Invariant.
        Scenario: Given set r1 with every gate closed, then no automatic write and no voted no-relationship reaches the ledger, and a set-theory level opens only on a one-sided 95% Wilson lower bound above 0.90 over at least 25 eligible outcomes
        """
        score_cases = []
        expected = {}
        for level in ANSWERS:
            for n, correct in ((0, 0), (24, 24), (25, 25), (25, 24), (50, 48), (50, 49)):
                key = f"{level}:{n}:{correct}"
                wrong = "equal" if level != "equal" else "subset_of"
                outcomes = [
                    {
                        "unit_id": f"AC-{i + 1}",
                        "candidate_id": CANDIDATE,
                        "family": "AC",
                        "prediction": level,
                        "reference_label": level if i < correct else wrong,
                        "eligible": True,
                    }
                    for i in range(n)
                ]
                # These tempting successes must not inflate an eligible denominator.
                outcomes.extend({
                    "unit_id": f"AU-{i + 1}",
                    "candidate_id": CANDIDATE,
                    "family": "AU",
                    "prediction": level,
                    "reference_label": level,
                    "eligible": False,
                } for i in range(30))
                score_cases.append({"id": key, "level": level, "outcomes": outcomes})
                expected[key] = (n, correct)

        payload = {
            "set_revision": "r1",
            "set": revision(0.7),
            "cases": [case(f"AC-{i}", answer=level) for i, level in enumerate(ANSWERS, 1)],
            "score_cases": score_cases,
        }
        with tempfile.TemporaryDirectory(prefix="goal-gates-") as root:
            response = invoke(self, root, "gates", payload, absent_ok=True)
            if response is None:
                return
            _, result, _ = response
            decisions = result["closed_gate_decisions"]
            self.assertEqual(len(decisions), len(ANSWERS))
            self.assertEqual({row["unit_id"] for row in decisions}, {
                f"AC-{i}" for i in range(1, len(ANSWERS) + 1)
            })
            for row in decisions:
                self.assertEqual(row["route"], "REVIEW")
                null_case = row["unit_id"] == "AC-5"
                self.assertEqual(
                    row["reason"], "nomatch_gate_closed" if null_case else "level_gate_closed"
                )
                self.assertEqual(row["card_id"], "T2-gated" if null_case else "T3-gated")

            for row in result["ledger"]:
                self.assertNotEqual(row["route"], "WRITE")
                self.assertNotIn(row["reason"], ("auto_accept", "no_match_voted"))
                self.assertEqual(row["writer_kind"], "card")
                self.assertTrue(row["card_id"])

            rows = result["scores"]
            self.assertEqual(len(rows), len(expected))
            self.assertEqual({row["id"] for row in rows}, set(expected))
            for row in rows:
                with self.subTest(score=row["id"]):
                    n, correct = expected[row["id"]]
                    lower = wilson_lower(correct, n)
                    self.assertEqual((row["n"], row["correct"]), (n, correct))
                    self.assertAlmostEqual(row["wilson_lower"], lower, places=6)
                    self.assertIs(row["gate_open"], n >= 25 and lower > 0.90)
                    self.assertEqual(
                        row["families_with_multiple_outcomes"], 1 if n > 1 else 0
                    )
                    if n < 25:
                        self.assertEqual(row["status"], "insufficient evidence")


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
            _, result, work = response
            raw = artifact(self, work, "protocol.json").read_bytes()
            actual_hash = hashlib.sha256(raw).hexdigest()
            recorded_hash = artifact(self, work, "protocol.sha256").read_text(
                encoding="utf-8"
            ).strip()
            self.assertEqual(recorded_hash, actual_hash)
            self.assertEqual(result["protocol_sha256"], actual_hash)
            frozen = json.loads(raw)
            self.assertEqual(frozen, protocol)
            self.assertEqual(frozen["sample_size"], len(frozen["units"]))
            self.assertTrue(300 <= frozen["sample_size"] <= 400)

            families = {}
            for unit in frozen["units"]:
                families.setdefault(unit["family"], set()).add(unit["split"])
            self.assertTrue(families)
            self.assertTrue(all(len(splits) == 1 for splits in families.values()))
            self.assertEqual(
                {unit["split"] for unit in frozen["units"]},
                {"calibration", "evaluation", "test"},
            )

            events = read_jsonl(self, work, "events.jsonl")
            freezes = [
                i for i, row in enumerate(events) if row["event"] == "protocol_frozen"
            ]
            calls = [i for i, row in enumerate(events) if row["event"] == "call_started"]
            self.assertEqual(len(freezes), 1)
            self.assertTrue(calls, "Fixture must exercise the first-call boundary")
            self.assertLess(freezes[0], min(calls))
            self.assertEqual(events[freezes[0]]["protocol_sha256"], actual_hash)
            for index in calls:
                self.assertEqual(events[index]["protocol_sha256"], actual_hash)

            split_family = copy.deepcopy(protocol)
            split_family["units"][1]["split"] = "evaluation"
            process, refused, bad_work = invoke(self, root, "protocol", {
                "mode": "freeze_then_fixture_call",
                "protocol": split_family,
            }, code=3)
            self.assertTrue(process.stderr.strip())
            self.assertTrue(refused["refused"])
            self.assertFalse(any(
                row["event"] == "call_started"
                for row in read_jsonl(self, bad_work, "events.jsonl")
            ))

            # Alter only the external export, retaining its original hash file.
            artifact(self, work, "protocol.json").write_bytes(raw + b"\n")
            _, changed, changed_work = invoke(self, root, "protocol", {
                "mode": "verify_then_fixture_call",
                "protocol_path": str(artifact(self, work, "protocol.json")),
                "hash_path": str(artifact(self, work, "protocol.sha256")),
            }, code=1)
            self.assertTrue(changed["refused"])
            self.assertFalse(any(
                row["event"] == "call_started"
                for row in read_jsonl(self, changed_work, "events.jsonl")
            ))


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
                "unit": {"id": f"AC-{number}", "text": UNIT_TEXT},
                "candidate": {"id": CANDIDATE, "text": CANDIDATE_TEXT},
                "reference_semantics": "association",
                "context": {"run_outputs": dict(taints)},
            }
            item.update(taints)
            items.append(item)

        with tempfile.TemporaryDirectory(prefix="goal-gold-") as root:
            response = invoke(self, root, "gold", {
                "mode": "assemble_primary",
                "items": items,
                "rubric": "Compare only the supplied source and target texts.",
                "fixture_council_answers": ["subset_of", "intersects_with"],
            }, absent_ok=True)
            if response is None:
                return
            _, result, _ = response
            packets = result["packets"]
            self.assertEqual(len(packets), len(items))
            self.assertEqual(
                {packet["unit"]["id"] for packet in packets}, {"AC-1", "AC-2"}
            )
            for packet in packets:
                self.assertEqual(packet["unit"]["text"], UNIT_TEXT)
                self.assertEqual(packet["candidate"]["text"], CANDIDATE_TEXT)
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
            for row in labels:
                self.assertEqual(row["origin"], "council")
                self.assertIn(row["label"], ANSWERS)
                self.assertIs(row["human_validated"], False)

            _, clean, _ = invoke(self, root, "gold", {
                "mode": "audit", "packets": packets,
            })
            self.assertEqual(clean["violations"], [])

            for key in ("proposal", "vote", "confidence", "route", "prediction"):
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
                    self.assertFalse(any(
                        row["event"] == "call_started"
                        for row in read_jsonl(self, audit_work, "events.jsonl")
                    ))


class Budget(unittest.TestCase):
    def test_guard_refuses_a_call_past_the_allowance(self):
        """Invariant.
        Scenario: Given the budget guard and a remaining allowance smaller than a call's maximum expected charge, then the call is refused before it is made and the refusal is written to the accounting log
        """
        base = {
            "provider": "openai",
            "model_id": "fixture-proposer-v1",
            "global_allowance_usd": "60.00",
            "provider_allowance_usd": "20.00",
            "max_calls": 40,
            "calls_already_made": 39,
            "input_token_ceiling": 1000,
            "output_token_ceiling": 1000,
            "input_usd_per_million": "10.00",
            "output_usd_per_million": "30.00",
            # Cheap actual usage cannot justify an unaffordable token ceiling.
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

        scenarios = (
            ("global", "59.97", "10.00", "0.03", 4),
            ("provider", "19.97", "19.97", "0.03", 4),
            ("exact_boundary", "59.96", "19.96", "0.04", 0),
        )
        with tempfile.TemporaryDirectory(prefix="goal-budget-") as root:
            for index, (name, total_spent, provider_spent, remaining, code) in enumerate(scenarios):
                with self.subTest(boundary=name):
                    payload = dict(
                        base,
                        total_spent_usd=total_spent,
                        provider_spent_usd=provider_spent,
                    )
                    response = invoke(
                        self, root, "provider_call", payload,
                        absent_ok=index == 0, code=code,
                    )
                    if response is None:
                        return
                    process, result, work = response
                    accounting = read_jsonl(self, work, "accounting.jsonl")
                    self.assertTrue(accounting)
                    row = accounting[-1]
                    self.assertEqual(Decimal(row["max_expected_charge_usd"]), maximum)
                    self.assertEqual(Decimal(row["remaining_usd"]), Decimal(remaining))
                    events = read_jsonl(self, work, "events.jsonl")
                    starts = [
                        event for event in events if event["event"] == "call_started"
                    ]
                    if code == 4:
                        self.assertIn("refused: budget", process.stderr.lower())
                        self.assertIn(remaining, process.stderr)
                        self.assertEqual(starts, [])
                        self.assertEqual(result["transport_calls"], [])
                        self.assertIsNone(result["output"])
                        self.assertEqual(row["status"], "refused")
                        self.assertEqual(row["reason"], "budget")
                        self.assertEqual(row["evidence_class"], "unpriced")
                    else:
                        self.assertEqual(len(starts), 1)
                        self.assertEqual(len(result["transport_calls"]), 1)
                        self.assertEqual(result["output"], "fixture")
                        self.assertEqual(row["status"], "completed")
                        self.assertEqual(row["evidence_class"], "provider_reported")


class Triad(unittest.TestCase):
    def test_proposer_never_votes(self):
        """Invariant.
        Scenario: A stored vote whose provider equals the proposer's provider must never happen
        """
        payload = {
            "proposer": {"provider": "openai", "model_id": "fixture-proposer-v1"},
            "unit": {"id": "AC-1", "text": UNIT_TEXT},
            "candidate": {"id": CANDIDATE, "text": CANDIDATE_TEXT},
            "votes": votes(),
        }
        with tempfile.TemporaryDirectory(prefix="goal-triad-") as root:
            response = invoke(self, root, "triad", payload, absent_ok=True)
            if response is None:
                return
            _, accepted, _ = response
            stored = accepted["stored_votes"]
            self.assertEqual(len(stored), 3)
            self.assertEqual(Counter(row["provider"] for row in stored), Counter(VOTERS))
            self.assertTrue(all(row["provider"] != "openai" for row in stored))

            for seat in range(3):
                with self.subTest(conflicting_seat=seat):
                    invalid = copy.deepcopy(payload)
                    invalid["votes"][seat]["provider"] = "openai"
                    # A different model from the same provider remains forbidden.
                    invalid["votes"][seat]["model_id"] = "fixture-other-openai-model"
                    _, rejected, _ = invoke(
                        self, root, "triad", invalid, code=1
                    )
                    self.assertTrue(rejected["refused"])
                    self.assertTrue(all(
                        row["provider"] != invalid["proposer"]["provider"]
                        for row in rejected["stored_votes"]
                    ))


if __name__ == "__main__":
    unittest.main()
