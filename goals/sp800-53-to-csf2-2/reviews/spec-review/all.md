# Spec review: PLAN.md all

_2026-11-17T03:44:54+00:00 · claude-sonnet-5-5 with claude-fable-5-2 advisor (1 calls)_

I found no contradiction that holds in a reachable scenario, so the contradictions list is empty. I dropped the advisor's council-voices-versus-OpenRouter pair because Mistral's provider is OpenRouter, so the two statements agree.

Ambiguities, one per requirement set:
- **A1:** the Definitions bullet on the two undos (public reads equal "a fresh replay of" the previous set), item 2's rule that replay runs each decision under the revision it was stored under, and item 8's reversal of r1 after r2. Sealed-test decisions were stored under r2, so it is unclear what the reactivated r1 reads are compared against.
- **A2:** the Definitions bullet on the $10 reserve and item 2's wording "reserve of exactly $10 for validation and reporting". The reserve can only be drawn in the `deliverables` phase, but the validation calls in items 7 and 8 come earlier.
- **A3:** item 7's primary packets show the "candidate", item 7's `gold.py audit` forbids any "proposal" in a primary packet, and the Definitions put the candidate hub entry inside a proposal's identity. It is unclear whether showing a candidate counts as showing a proposal.
- **A4:** item 2's list of council packet classes and the Definitions bullet "primary gold audit covers primary packets only". The census, missed-link draw and SS-audit packets are not said to be primary or not, so `gold.py audit` coverage is undecidable.
- **A5:** item 8's `run.py policy r3` and item 7's census and missed-link rules. After r3 re-asks the sealed-test votes, the plan doesn't say which vote set feeds that split's voted-no-relationship census, missed-link estimate and `score.test.json`.
- **A6:** item 7's coverage, defined as the share of evaluation-split units with a proposal ruled a true link. The plan doesn't say whether scope-excluded units count in the denominator.
- **A7:** item 7 criterion 2 (`correct` per level). The plan doesn't say whether "correct" means an exact level match with the council or any true link.
- **A8:** items 7, 8 and 9. `RESULT.md` has to report council agreement, the missed-link estimate and the anchoring comparison, but the plan doesn't say which split each comes from or whether the two splits are pooled.
- **A9:** item 5's unit ("an active SP 800-53 control or enhancement"), item 5's scope rule (withdrawn controls out by metadata) and item 1's withdrawn-control count. If withdrawn controls aren't units, the metadata scope rule can never fire on a sampled unit.
- **A10:** the Definitions bullet "the 18 control families of the catalog", item 1 (counts computed from files) and item 2 ("strata by control family"). The plan doesn't say what happens if the pinned catalog's family count isn't 18.
- **A11:** item 9 criterion 2 requires one `check.py --table` row per Scenario in `GOAL.md`, but the plan doesn't include the Scenarios, so that requirement can't be checked.
- **A12:** the Definitions on E1 ("every other card id must be reached on fixtures") and item 5 (route "retrieval only", fixed hub at revision 1). Whether the curator cards K1 to K3 can be reached on fixtures can't be decided from the plan.
- **A13:** item 2 says the r3 numeric value is recorded in `config.frozen.json` after calibration. Item 6 says calibration writes that file "with its hash". The plan doesn't say whether adding the value later changes the hash or breaks the freeze.

```json
{"contradictions": [],
 "ambiguities": [
  {"id": "A1", "requirements": ["Definitions: the two undos", "item 2: replay runs every decision under the revision it was stored under", "item 8: undo.py publication reversal"], "question": "Reactivating r1 after r2 reads sealed-test decisions stored under r2. What is the 'fresh replay of' the previous set that public reads must equal: r1 applied to every decision, or replay under each decision's stored revision?"},
  {"id": "A2", "requirements": ["Definitions: Model calls / $10 reserve and phase field", "item 2: budget calculation reserve of exactly $10 for validation and reporting"], "question": "Which calls are 'validation' and may draw on the reserve? Items 7 and 8 council and audit calls happen before the deliverables phase, when the guard blocks the reserve."},
  {"id": "A3", "requirements": ["item 7: blind primary council packets with ... candidate", "item 7: gold.py audit exits 0 only when no primary packet contains a proposal", "Definitions: proposal identity"], "question": "A proposal's identity includes the candidate hub entry, and primary packets show the candidate. Does showing a candidate count as containing a proposal for the gold.py audit?"},
  {"id": "A4", "requirements": ["item 2: council packet classes", "Definitions: Assisted-review packets ... primary gold audit covers primary packets only"], "question": "Are the voted-no-relationship census, missed-link draw and SS audit packets primary packets, and so subject to the blind-packet audit, or a separate class?"},
  {"id": "A5", "requirements": ["item 8: run.py policy r3", "item 7: voted no-relationship census", "item 8: score.py --split test"], "question": "After r3 re-asks sealed-test votes, which vote set (r2 or r3) defines that split's voted-no-relationship census, missed-link estimate and score.test.json?"},
  {"id": "A6", "requirements": ["item 7: coverage is the share of evaluation-split units with at least one proposal ruled a true link"], "question": "Are scope-excluded units (no statement text, withdrawn) in the coverage denominator?"},
  {"id": "A7", "requirements": ["item 7: score.json per-level correct"], "question": "Is a proposal 'correct' at a level only when the council ruling matches that exact level, or when the council rules any true link?"},
  {"id": "A8", "requirements": ["item 7", "item 8", "item 9: RESULT.md"], "question": "Which split's (or pooled) council agreement, missed-link estimate and anchoring comparison does RESULT.md report?"},
  {"id": "A9", "requirements": ["item 5: unit is an active control or enhancement", "item 5: scope rules withdrawn out by metadata", "item 1: withdrawn count"], "question": "Do withdrawn controls enter the sample frame? If units are active only, the metadata scope rule never fires on a sampled unit."},
  {"id": "A10", "requirements": ["Definitions: the 18 control families of the catalog", "item 1: counts computed from files", "item 2: strata by control family"], "question": "What happens if the pinned catalog's family count differs from 18? Is 18 a fixed expectation or a computed count?"},
  {"id": "A11", "requirements": ["item 9: check.py --table extended with one row per Scenario in GOAL.md"], "question": "The Scenarios are not in the plan text. Which scenarios must the table contain?"},
  {"id": "A12", "requirements": ["Definitions: E1 and every other card id must be reached on fixtures", "item 5: route retrieval only, fixed hub at revision 1"], "question": "Can curator cards K1 to K3 be reached on fixtures when the hub is fixed and the route is retrieval only?"},
  {"id": "A13", "requirements": ["item 2: r3 numeric value recorded in config.frozen.json after calibration", "item 6: config.frozen.json written with its hash"], "question": "Does adding the r3 value to config.frozen.json after calibration change the file's hash, and which hash is authoritative?"}
 ]}
```
