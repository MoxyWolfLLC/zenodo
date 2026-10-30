# Reference runner: models propose, rules decide

A small, stdlib-only demonstration of the decision procedure in `method-spec-r2.md` (a copy ships in this folder). It's not a product. Models are stubs, the domain is synthetic, and the store is an in-memory SQLite database.

```
python3 -I check.py      # prints a PASS/FAIL table, exits 1 on any FAIL
python3 -I wilson.py     # prints the Section 9 sample sizes
```

Python 3.9 or later, standard library only. `expected/check_output.txt` and `expected/wilson_output.txt` hold the output these produce; the check output is deterministic, so `python3 -I check.py | grep -v '^wrote' | diff - expected/check_output.txt` should print nothing. Each run rewrites `decision_record_example.json` and `decision_record_example.sssom.tsv`; only their timestamps change.

The paper this accompanies: Cougias D. *Models Propose, Rules Decide: An Auditable Decision Procedure for AI-Assisted Mapping.* Manuscript, 2026. Archived on Zenodo: https://doi.org/10.5281/zenodo.23819764. Licensed under the MIT License (see `LICENSE` at the repository root).

## Files

| File | What it holds |
|---|---|
| `cards.py` | Card sets as ordered data: `(card_id, revision, condition, outcome)`. Conditions are pure functions of stored data and set parameters. Four set revisions: `r1` (baseline), `r2` (change a: `c_min` 0.7 to 0.5), `r3` (change b: triad question `tq1` to `tq2`), `r4` (change c: acceptance gates closed for no match and augments). Each set pins an engine hash of the shared helpers. |
| `runner.py` | The engine: store, stubs, run graph, ledger, activation, evaluation, write footprints and recovery. |
| `fixtures.json` | 42 synthetic units, 8 hub entries, canned model outputs, canned reviewer and curator rulings, one challenge, gold rulings. |
| `check.py` | The 14 checks. Also writes `decision_record_example.json` and `decision_record_example.sssom.tsv`. Run with `python3 -I check.py`. |
| `wilson.py` | Prints the sample sizes quoted in Section 9 (one-sided 95% Wilson lower bound against a bar). |
| `method-spec-r2.md` | The normative spec the checks read their reason codes from. |
| `decision_record_example.json` | One auto-accepted candidate (S11/C1), end to end, with its replay and its re-evaluation under changes (a) and (b). |
| `decision_record_example.sssom.tsv` | The same decision as one SSSOM row. |

## Synthetic domain

Statements S01 to S42 map to capabilities C1 to C8 (C9 to C11 get added by the curator during the run). Scale: no match (null) < informs < supports < augments < replaces. Retrieval is keyword overlap, top 3. Every unit carries a `note` in `fixtures.json` saying which route it exists to exercise. None of it is real framework content.

## Spec step to card id

| Spec | Card | Spec | Card |
|---|---|---|---|
| I1 | `I1` | E1 | `E1`, then curator set `K1` approve / `K2` decline / `K3-otherwise` pending |
| I2 | `SS-scope`, then audit set `A1` sampled / `A2-otherwise` | E2 | `E2` |
| I3 | `I3` | E3 | `E3` |
| I4 | `I4` | E4 | `E4-otherwise` |
| I5 | `I5-otherwise` | T1 to T5 | `T1` to `T4`, `T5-otherwise` |
| R1 | `SS-retrieval` | Review | ruling set `H1` accept / `H2` no match / `H3` out of scope / `H4-otherwise` awaiting |
| R2 | `R2-otherwise` | Challenge | `CH` row, then the ruling set |

## Choices not fixed by the spec

1. **Stub keying.** Canned outputs are keyed by `(role, question_version, evidence_hash)`, where the stub's evidence is `{unit, candidate, hub_revision}` (or `{unit, attempt}` for extraction). The store's reuse key is stricter: the full evidence also includes release, candidate-set hash and, for votes, the quoted spans. Fixtures can't carry the candidate-set hash because it's computed at run time. In this toy world unit plus hub revision fixes the candidate set, so the two keys pick out the same outputs.
2. **Triad roles.** Each triad seat is its own role (`triad/prov-B`, `triad/prov-C`, `triad/prov-D`), so the 3-part key stays unique per provider. The proposer is `prov-A`; `cards.make` asserts it never sits on the triad.
3. **Defaults for missing canned outputs.** Proposer and triad get `answer: null, confidence: 1.0, quotes: []`. Extraction gets `complete: true, work_object: true`. Every default call is counted. A missing reviewer or curator ruling leaves the item pending (`H4-otherwise` or `K3-otherwise`), and that call is counted too.
4. **I3 retry evidence.** The extraction attempt number is part of the extraction's evidence, so a retry is a new question and can get a different answer.
5. **I4 condition.** Read as "extraction incomplete AND gap_attempts >= G_max". Read literally (attempts only), a unit that succeeds on its last retry would still be sent to review.
6. **Conflict.** A proposer output field `conflict: true`. Quote verification is a substring test against the unit text.
7. **Audit sampling.** Deterministic: a unit is sampled when the first 8 hex digits of `sha256(unit_id)`, divided by 2^32, fall below `a_scope` (0.2). Any audit ruling supersedes the SS-scope record, whether it agrees or not. A disagreeing ruling counts as an audited error.
8. **SS-retrieval audit.** For each R1 unit, every hub entry at its revision counts as a sampled non-retrieved pair (rate 1.0). A gold link on any of those pairs counts as a missed link.
9. **Hub loop.** Hub revision is global and only goes up. When E1 is approved the unit's current pass is dropped (siblings included) and the unit reopens at R0 against the new revision. The curator decision stores the new revision, so replay reopens at the same revision without asking anyone.
10. **Pinned evidence on re-decide.** Evaluation, activation and replay start a unit at the hub revision of its first stored candidate set, not today's hub. Otherwise every hub change would invalidate every stored output.
11. **Triad evidence.** The triad sees the unit, the candidate and the proposer's quoted spans. It doesn't see the proposer's answer or confidence.
12. **Levels.** T3 takes the level held by at least 2 valid votes. H1 takes the reviewer's level. Nothing else sets a level.
13. **Ledger shape.** A queued item is a live decision row (`REVIEW` or `HUB`). The ruling that settles it is a new row that supersedes it. Activation writes a fresh chain for every unit and supersedes the prior live rows. `batch` numbers each publication.
14. **Challenges** are external events. A challenge ruling is stored against what was shown (route, reason, level, basis output ids). Replay and re-decide reuse it whenever the gate gets the same outcome on the same basis. The runner never asks for challenges by itself.
15. **Pending items on re-decide.** Evaluation and activation queue a still-pending review or hub item again, and that shows up as a reviewer or curator call. Checks 7 and 8 report these separately from model asks and require model asks to be zero.
16. **Gate precision denominator (check 11).** All auto_accept rows the gate wrote in the current publication batch, one per (unit, candidate). That includes links later overturned by challenge, because the challenge measures a gate error.
17. **Store.** SQLite in memory. Every insert and update goes through one helper that writes a footprint row (table, row id, before, after) to `writes`. Recovery restores a write's footprint newest first. Recovery is itself a write with its own footprint.
18. **Undo 1 for units first decided under rN+1.** The fixtures have none. Every unit exists before the first activation.
19. **Route parameter / inherited candidates.** Not used. R0 is top-k retrieval only.
20. **Change (b) has no canned `tq2` votes.** Every re-asked vote gets the stub's default "no match", so 21 decisions would change. That's the point of the count: rewording the question is a new question, and nothing carries over.

21. **Acceptance gates are set parameters.** A set's `closed` list names the outcomes whose gate is closed (`no_match` or a level). T2-gated and T3-gated send those to review. Closing or opening a gate is a new set revision and re-asks no model output (check 12).
22. **Engine pin.** `cards.engine_hash()` hashes the source of the shared helpers (`valid`, `plurality`, `nulls`, `quotes_ok`, `sampled`). Every set stores it at creation, and `run_unit` refuses to run a set whose hash no longer matches (check 13).
23. **SSSOM export.** The level is the predicate in a synthetic `ex:` namespace; `mapping_justification` is `semapv:CompositeMatching` because the link came from a proposal, a vote and a card, not one matcher. The set revision and engine hash go in `mapping_tool_version`; the stored output ids go in `other`.
24. **Cost (check 14).** Counts are calls to the stubs, not dollars. They show the shape of the cost, not its size.

## Spec observations (no blocking conflicts found)

- **T5 is unreachable at n = 3 for every input, not just all-valid ones.** Any invalid vote is caught by T1 first. Check 3's enumeration lands 0 of 216 combinations on T5. The spec's wording ("unreachable when n = 3 with all votes valid") is true but weaker than what holds.
- **I4 wording.** See choice 5. The literal condition would misroute a successful final retry.
- **Scope order.** The spec places the content scope card (SS-scope) after I3 and I4, so an incomplete extraction is retried or reviewed before any out-of-scope judgment is recorded. The runner follows that order.
- **Triad blindness vs quoted spans.** E4 says the triad sees "only unit, candidate and evidence", and the reuse rule makes quoted spans part of the evidence. Those spans come from the proposer, so they leak a little of its view: a negative proposal usually has none. The runner passes them (choice 11).
- **E1 siblings.** The spec doesn't say what happens to the other candidates in a pass that gets reopened. The runner drops them (choice 9).
