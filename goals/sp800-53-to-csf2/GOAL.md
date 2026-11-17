# Goal: sp800-53-to-csf2

## Serves
Second domain for Models Propose, Rules Decide

## Outcome
Run a second-domain experiment using SP 800-53 controls and enhancements against CSF 2.0 subcategories. Report separately: structural conformance to both Appendix C graphs; replay, reuse, revision activation and restoration behavior; and semantic results against a mixed reference standard comprising eligible NIST mappings and independently produced council labels. A successful run supplies evidence for the next paper version. It does not automatically retire either Section 11 item; that depends on the item's stated acceptance criteria. Council judgments are not human validation. The answer scale is IR 8477's set-theory types, `subset_of`, `intersects_with`, `equal` and `superset_of`, with `no_relationship` as the null; the paper's demonstration scale (informs, supports, augments, replaces) is not used anywhere in this runner or its fixtures. The runner lives in `runners/sp800-53-to-csf2/` beside the paper's reference runner, with the same stdlib-only entry (`python3 -I check.py`) for its offline conformance checks and a separate `run.py` for the paid passes. Brief co-authored with Dorian in the live doc of 2026-11-16; his protocol sections are the normative text for sampling, splits, gates, reference-standard construction, missed links, anchoring, replay, undos, graph conformance, budget and deliverables.

## Non-goals
- changing the 1.0.0 or 1.1.0 runner folders
- publishing a Zenodo record or editing the paper (separate authorizations)
- human gold rulings; the council stands in and the report says so
- a curator loop; the hub is fixed and E1 is present but unexercised
- claiming accuracy, domain-freeness or retirement of any Section 11 item

## Scenarios
- Given the run's store and the pinned Appendix C baseline, then `check.py` prints a conformance diff of nodes, edges, card ids, lanes, stores and decision semantics with zero deviations, or names each one as a fork
- Given a store of live decisions and the network disabled for models, retriever and council, then every decision replays under its original revision with the same route, level and reason, and the call counter stays at zero
- Given r3's predeclared threshold change, then the expected invalidations derived from the stage-identity rules equal the observed re-asked calls, including three new votes per candidate newly reaching the triad
- Given set r1 with every gate closed, then no automatic write and no voted no-relationship reaches the ledger, and a set-theory level opens only on a one-sided 95% Wilson lower bound above 0.90 over at least 25 eligible outcomes
- Given the frozen protocol file, then its hash equals the hash recorded before the first model call, and every family's controls sit in one split
- Given the primary gold packets, then none contains a proposal, vote, confidence, route or prediction
- Given the budget guard and a remaining allowance smaller than a call's maximum expected charge, then the call is refused before it is made and the refusal is written to the accounting log
- A primary gold label derived from a packet containing proposals or votes must never happen
- A stored vote whose provider equals the proposer's provider must never happen
- A ledger write made by a model output or a council ruling rather than a card must never happen
- An Appendix C card, lane or store changed to make the domain fit must never happen

## Goal tests
- `tests/test_runner.py::Conformance.test_graph_diff_against_pinned_appendix_c` (outcome)
- `tests/test_runner.py::Replay.test_replays_every_decision_with_network_disabled` (outcome)
- `tests/test_runner.py::Policy.test_predeclared_change_reasks_exactly_the_expected_calls` (outcome)
- `tests/test_runner.py::Gates.test_closed_gates_write_nothing_and_open_only_on_the_bound` (invariant)
- `tests/test_runner.py::Protocol.test_frozen_protocol_hash_and_family_preserving_splits` (invariant)
- `tests/test_runner.py::Gold.test_primary_gold_packets_are_blind` (invariant)
- `tests/test_runner.py::Budget.test_guard_refuses_a_call_past_the_allowance` (invariant)
- `tests/test_runner.py::Triad.test_proposer_never_votes` (invariant)

## Allowed paths
- runners/sp800-53-to-csf2/*
- runners/sp800-53-to-csf2/*/*
- runners/sp800-53-to-csf2/*/*/*

## Spend cap
$60

## Provider budgets
- openai: $20
- anthropic: $12
- google: $8
- openrouter: $20

## Max calls
40

## Max items
10

## Max review rounds per item
3

## Stop conditions
- any goal test regresses
- a change reaches a path outside runners/sp800-53-to-csf2/, including .github/, goals/ and goal-runs/
- the marketplace's top-level version moves on the goal branch
- model spend across the four providers reaches $60, or a call's maximum expected charge exceeds the remaining allowance
- any outbound model, retriever or council call during a replay
- a Zenodo record is created or published, or the paper under GitHub/Projects/CKI is edited, from this goal; both are Dorian's call
- a council packet for a primary gold label is found to contain a proposal, vote, confidence, route or prediction

## Pre-mortem
- the conformance diff normalizes more than domain labels, so a real fork reads as a match
- replay passes because the stubbed network check is bypassed by a cached HTTP client, not because no call was attempted
- the r3 expected-call derivation is computed from observed calls after the fact instead of from the stage-identity rules before the run
- gates open on pooled outcomes where one control family contributes many correlated writes at one level, and the report never says so
- the blind council sees the candidate's NIST reference label through the rubric or the packet's context, so "blind" is only blind to the run's outputs
- NIST informative references are counted as five-class labels where they are untyped associations
- the budget guard prices by the usage response after the call, not by the token ceiling before it
- the sealed-test split is read during calibration through a shared cache
- the runner passes `check.py` on fixtures but `run.py` never completed a paid pass, and RESULT.md reports fixture numbers as results
