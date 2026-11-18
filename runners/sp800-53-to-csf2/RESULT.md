# sp800-53-to-csf2-2: result

Second-domain instantiation of "Models Propose, Rules Decide": SP 800-53 Rev. 5 controls → CSF 2.0 subcategories. Every number below names the file it is read from; `result.py` writes this file from those files and nothing else.

- Protocol: `protocol.json`, sha256 `c6fa1c26cc6bb4d76938082692e665d3cf8d61838ae8c3f87c5d326e33a49492` (`protocol.sha256`); it supersedes `34523becd7a3ae762f966ca24e6057ea2e9f87928388111ae020c79de9c97687` (changed: roles.council.models, budget, routing_note, supersedes; `protocol.json` → `supersedes`).
- Frozen configuration: `config.frozen.json` sha256 `a6f9a70f142f6e5efb9de8322d8073b6a04bb7bef3e2b5df4f80eb30657e2359`: c_min 0.7, proposal prompt propose-v1, chosen revision cal-1 (F1 0.328 against the weak labels, 66 scored units), calibrated under protocol `34523becd7a3ae76`.
- Sets: r1 (`set_r1.json`, the paper's defaults, recorded and never published), r2 (`set_r2.json` sha256 `eb4bb7f724b8dc0f`, every gate closed, published: `run.sqlite` → `sets`), r3 (`set_r3.json` sha256 `81e27ee6b0b1a2f4`, c_min 0.5, unpublished).
- Sample: `protocol.json` → `split.units_by_split`: {'calibration': 91, 'evaluation': 160, 'test': 106}; 104 families, whole families per split.
- Council voices: anthropic/claude-haiku-5.5, google/gemini-2.5-flash-lite, mistralai/mistral-large-2512 (`protocol.json` → `roles.council.models`).

## Measures (the plan's Table 6 list)

### Precision of automatic writes per level (sealed-test split, r2)

No gate opened on the evaluation split, so set r2 writes nothing automatically: 0 automatic writes on the test split (`score.test.json` → `automatic_writes`). Every level reports n = 0 and `closed_insufficient`:

| level | n | correct | Wilson lower (one-sided 95%) | gate | families with >1 outcome |
|---|---|---|---|---|---|
| subset_of | 0 | 0 | 0.000 | closed_insufficient | 0 |
| intersects_with | 0 | 0 | 0.000 | closed_insufficient | 0 |
| equal | 0 | 0 | 0.000 | closed_insufficient | 0 |
| superset_of | 0 | 0 | 0.000 | closed_insufficient | 0 |
| no_relationship | 0 | 0 | 0.000 | closed_insufficient | 0 |

(source: `score.test.json` → `rows`)

### Precision of voted no-relationship (one-sided 95% Wilson lower bound)

- sealed test: 41 of 45 council-ruled no_relationship, Wilson lower 0.816 (`score.test.json` → `prediction_rows`, level no_relationship).
- evaluation: 61 of 67 council-ruled no_relationship, Wilson lower 0.836 (`score.json` → `prediction_rows`, level no_relationship).

### Gate qualification on the evaluation split (every prediction, gated or not)

| level | n | correct | Wilson lower (one-sided 95%) | gate | families with >1 outcome |
|---|---|---|---|---|---|
| subset_of | 8 | 0 | 0.000 | closed_insufficient | 1 |
| intersects_with | 150 | 98 | 0.587 | closed | 26 |
| equal | 72 | 0 | 0.000 | closed | 15 |
| superset_of | 0 | 0 | 0.000 | closed_insufficient | 0 |
| no_relationship | 67 | 61 | 0.836 | closed | 17 |

(source: `score.json` → `rows`)

The same rows for the sealed-test split's gated predictions, for comparison (`score.test.json` → `prediction_rows`):

| level | n | correct | Wilson lower (one-sided 95%) | gate | families with >1 outcome |
|---|---|---|---|---|---|
| subset_of | 6 | 0 | 0.000 | closed_insufficient | 1 |
| intersects_with | 93 | 53 | 0.485 | closed | 20 |
| equal | 47 | 0 | 0.000 | closed | 9 |
| superset_of | 1 | 0 | 0.000 | closed_insufficient | 0 |
| no_relationship | 45 | 41 | 0.816 | closed | 11 |

(source: `score.test.json` → `prediction_rows`)

### Missed links (Horvitz-Thompson, non-retrieved stratum weighted by its inverse inclusion probability)

- evaluation: estimate **withheld**; retrieved stratum (weight 1): 308 ruled true links, 6 of them the run voted no_relationship, 13 pairs unresolved; non-retrieved stratum: 800 draws (5 per unit, weight 19.6), 19 true links, 4 unresolved; withheld because: 17 required ruling(s) unresolved (13 retrieved, 4 drawn); the frozen weights admit no non-response adjustment (`score.json` → `missed_links`).
- sealed test: estimate **withheld**; retrieved stratum (weight 1): 220 ruled true links, 4 of them the run voted no_relationship, 9 pairs unresolved; non-retrieved stratum: 530 draws (5 per unit, weight 19.6), 13 true links, 3 unresolved; withheld because: 12 required ruling(s) unresolved (9 retrieved, 3 drawn); the frozen weights admit no non-response adjustment (`score.test.json` → `missed_links`).

The estimate is withheld on both splits on Dorian's call (2026-11-17): the frozen weights come from the random draw and admit no non-response adjustment, so an unresolved ruling is a missing observation and the estimate is reported only when every required ruling is in. Uncertainty: with the estimate withheld, no interval is reported; the counts above are the complete inputs.

### Single-source error rates (SS-scope and SS-retrieval audit samples, per split)

| split | card | pool | drawn | resolved n | errors | error rate |
|---|---|---|---|---|---|---|
| evaluation | SS-retrieval | 0 | 0 | 0 | 0 | none drawn |
| evaluation | SS-scope | 1 | 1 | 1 | 0 | 0.0 |
| sealed test | SS-retrieval | 0 | 0 | 0 | 0 | none drawn |
| sealed test | SS-scope | 1 | 1 | 1 | 0 | 0.0 |

(sources: `audit.evaluation.json`, `audit.test.json` → `cards`; the run routed one unit to SS-scope per split and none to SS-retrieval, so the pools are 1 and 0)

### Coverage (share of a split's units with at least one proposal the council ruled a true link)

- evaluation: 0.775 (124 of 160 units) (`score.json` → `coverage`).
- sealed test: 0.830 (88 of 106 units) (`score.test.json` → `coverage`).

### Council agreement (Krippendorff's alpha, nominal, on the double-ruled draw)

- evaluation: alpha 0.924744 over 249 resolved pairs of 254 drawn (20% of 1272 primary packets) (`gold.evaluation.json` → `double_ruled`).
- sealed test: alpha 0.920543 over 166 resolved pairs of 168 drawn (20% of 840 primary packets) (`gold.test.json` → `double_ruled`).

### Reuse cost (the policy change r3 on the sealed-test split)

- c_min 0.7 → 0.5 by the frozen formula; expected calls by stage {'embedding': 0, 'extraction': 0, 'proposal': 0, 'vote': 852}, observed {'embedding': 0, 'extraction': 0, 'proposal': 0, 'vote': 852}, equal: True; 852 calls asked, 1051 stored outputs reused; cost 0.0436 USD ({'vote': '0.043623844'}); 841 decisions re-decided, 0 automatic writes; re-derived rulings: 0 (no stored operational ruling names a vote (the run's rulings table holds none), so nothing is re-derived; the engine re-decides every re-asked pair under r3 at no model cost) (`policy_report.json`).

### Review queue size and age (the store at the end of item 8)

- 2112 decisions routed REVIEW under the published set ['r2'] at 2026-11-18T00:28:11Z: evaluation: 1272 (age 0.651–0.652 h; reasons {'evidence_unclear': 865, 'incomplete_triad': 61, 'level_gate_closed': 236, 'level_split': 43, 'nomatch_gate_closed': 67}); test: 840 (age 0.737–0.737 h; reasons {'evidence_unclear': 556, 'incomplete_triad': 55, 'level_gate_closed': 152, 'level_split': 32, 'nomatch_gate_closed': 45}) (`queue.json`). Ages are measured from the rows' `created` stamps, which the last `run.py evaluate` re-run reset; the queue has had no reviewer.

## Anchoring comparison (blind versus assisted rulings of the same primary packet, separate sessions)

- evaluation: 30 of 30 pairs resolved; agreement with the run's proposal blind 0.8, assisted 0.8333, difference (blind minus assisted) -0.0333; discordant pairs 5, blind wins 2, 95% Wilson on the discordant share [0.1176, 0.7693] (`gold.evaluation.json` → `anchoring`).
- sealed test: 28 of 30 pairs resolved; agreement with the run's proposal blind 0.7857, assisted 0.7857, difference (blind minus assisted) 0.0; discordant pairs 2, blind wins 1, 95% Wilson on the discordant share [0.0945, 0.9055] (`gold.test.json` → `anchoring`).

## Gold assembly

- evaluation: 1272 blind primary packets; labels {'None': 13, 'intersects_with': 307, 'no_relationship': 951, 'subset_of': 1}; unresolved {'assisted': 0, 'double': 3, 'missed_link': 4, 'primary': 13}; last assembly: 0 council calls asked, 7068 reused, 0.0000 USD; the assemblies' whole spend is in the accounting table below (`gold.evaluation.json`).
- sealed test: 840 blind primary packets; labels {'None': 9, 'intersects_with': 218, 'no_relationship': 611, 'subset_of': 2}; unresolved {'assisted': 2, 'double': 0, 'missed_link': 3, 'primary': 9}; last assembly: 4760 council calls asked, 0 reused, 0.7740 USD; the assemblies' whole spend is in the accounting table below (`gold.test.json`).

## Conformance with Appendix C

- `conform.py`: 0 deviations across nodes, edges, card_ids, lanes, stores, decision_semantics for the run graph and the policy graph; baseline `baseline/appendix_c.md` sha256 `04eae5606ce53016` at paper commit `4c3a05ec6`; E1 present, exercised: False (`conformance.json`).
- `check.py --table`: 26 cards PASS, E1 present_unreachable, no failures; 11 of 11 GOAL.md scenarios PASS on fixtures (`expected/check_output.txt`).

## Policy change, replay, undos

- Replay: 2955 live decisions (r2 on both splits, r3 on the test split) re-derived on a copy with calls disabled, 2955 matched, 0 outbound attempts (`replay_decisions.json`). Not replayed: not published; an incomplete extraction attempt is not stored, so a walk that once took a later attempt is not reproducible; units with a later extraction attempt: ['ir-8@64ca6b4161de'].
- Publication reversal: complete public read under r1 (0 rows) equal before and after r2 was published and r1 reactivated: True; the r2 read (2114 rows) differs: True (`undo_report.json`). r1 carries no decisions in this run (calibration ran under cal-1..cal-6, the evaluation and test splits under r2): the complete read under r1 is empty before and after.
- Data recovery: footprint of the bad write 5 rows, exactly the targets: True; restored, untargeted rows changed 0, table equal after restore: True over 6482 rows (`undo_report.json`).

## Calibration

| revision | prompt | c_min | F1 (weak labels) | precision | recall | cost USD |
|---|---|---|---|---|---|---|
| cal-1 | propose-v1 | 0.7 | 0.328 | 0.381 | 0.288 | 0.0013 |
| cal-2 | propose-v1 | 0.5 | 0.319 | 0.390 | 0.270 | 0.0013 |
| cal-3 | propose-v1 | 0.6 | 0.312 | 0.370 | 0.270 | 0.0163 |
| cal-4 | propose-v1 | 0.8 | 0.323 | 0.400 | 0.270 | 0.0263 |
| cal-5 | propose-v1 | 0.9 | 0.199 | 0.375 | 0.135 | 0.0165 |
| cal-6 | propose-v2 | 0.7 | 0.237 | 0.388 | 0.171 | 0.1000 |

(`calibration.jsonl`; chosen cal-1, `config.frozen.json`). Observation for the method: under propose-v1 the evidence cards stop most pairs at E3 (quotes not verbatim) and under propose-v2 most pairs are voted no_relationship; the frozen choice is the best F1 against weak labels, not a judgment of either prompt.

## Accounting totals by evidence class (`accounting.jsonl`)

| evidence | provider | rows | charged USD |
|---|---|---|---|
| provider_reported | openrouter | 15686 | 3.2884 |
| usage_priced | anthropic | 6919 | 0.7470 |
| usage_priced | openai | 4827 | 0.1757 |

Total charged: 4.2112 USD of the 60 USD cap (`protocol.json` → `budget`; worst case 24.58 USD, 34.58 with the reserve). Missing cost evidence by provider (rows charged at the ceiling with no usage, class `unpriced`): anthropic: 0, openai: 0, openrouter: 0. Refused calls (class `refused`): 0.

## Deviations declared

- Gemini is reached through OpenRouter (Google's direct API returned 402 on every call on 2026-11-17; Dorian's instruction), against the plan's "only Mistral through OpenRouter"; the seat is still the Google family (`protocol.json` → `routing_note`).
- The council's Google voice is gemini-2.5-flash-lite (so the OpenRouter worst case stays under its 20 USD cap) and its Mistral voice is mistral-large-2512: mistral-large-4-0 reasons before it answers, cannot be told not to, and returned empty content on 2,353 of 2,357 evaluation packets; the first gold assembly under it is retired and its rows stay in `run.sqlite` → `council_rulings` under that voice id.
- The missed-link estimate is withheld while any required ruling is unresolved (Dorian, 2026-11-17: "withhold the estimate").
- r3 is recorded as a set and left unpublished: the delta's scope is the sealed-test split and a published r3 would drop the evaluation split's public read.
- The calibration revisions are not replayed: an incomplete extraction attempt is not stored (backlog: goal-sp800-53-to-csf2-2-follow-ups.md).
- Item 1's merge has no release record (noted at the time); every later item has one.
- The discover-command deviation declared at item 3 stands.

## Measures with no producing item

- None of the Table 6 measures lacks a producing item. The HT estimate's interval is `not produced`: the estimate is withheld, so no interval is computed.

