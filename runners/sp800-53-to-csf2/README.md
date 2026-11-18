# Runner: SP 800-53 Rev. 5 controls → CSF 2.0 subcategories

The second-domain instantiation of "Models Propose, Rules Decide" (the paper's method, run on real sources with live models). Stdlib-only Python 3.13. Everything the run produced is in this folder; `RESULT.md` reads the measures back from the files and names each file beside each number.

```
python3 -I check.py                  # the offline checks: the card table (26 PASS, E1 present_unreachable) and the 11 GOAL.md scenarios on fixtures (--table is the same)
python3 -I conform.py                # the run and policy graphs against Appendix C (exit 6 if the pinned baseline's hash moved)
python3 -I result.py                 # RESULT.md and expected/check_output.txt from the run's files
python3 -I tests_cards.py; python3 -I tests_adapter.py; python3 -I tests_run.py; python3 -I tests_gold.py; python3 -I tests_item8.py; python3 -I tests_conform.py
```

The live passes, in the order they ran (each stores every model output under its stage identity and reuses it on any rerun):

```
python3 -I fetch_sources.py          # item 1: the three pinned sources into data/, hashes into sources.json
python3 -I freeze.py                 # item 2: the sample, the splits, the budget and the roles into protocol.json, hash into protocol.sha256
python3 -I retrieve.py               # item 4: the hub index (embeddings) into index.json, pinned to the protocol
python3 -I run.py calibrate          # item 6: the calibration split, six revisions, config.frozen.json
python3 -I run.py evaluate           # item 7: the evaluation split under set r2 (set_r2.json), r2 published
python3 -I gold.py assemble --split evaluation; python3 -I audit.py --split evaluation; python3 -I score.py --split evaluation
python3 -I run.py test               # item 8: the sealed-test split under r2, before any council ruling for it
python3 -I gold.py assemble --split test; python3 -I audit.py --split test; python3 -I score.py --split test; python3 -I queue.py
python3 -I run.py policy             # item 8: r3 (the predeclared threshold delta) on the test split; policy_report.json
python3 -I run.py replay             # item 8: every live decision re-derived with calls disabled; replay_decisions.json
python3 -I undo.py --store <copy taken after r2 and before r3>   # item 8: both undo drills on copies; undo_report.json
```

## Files

| File | What it holds |
|---|---|
| `cards.py` | The seven card lists from the paper's runner, verbatim, with the levels, the null, the gates and the engine hash; `set_r1.json` is the paper's defaults. |
| `engine.py` | `run_case`: the walk through intake, retrieval, evidence, triad and ruling cards; `panel_violations` fails closed on a missing, duplicate or proposer-seated voter. |
| `providers.py` | The one adapter for every model call: prices with families, ceilings per role, the budget guard, reservations, retries only on a 429, every attempt accounted (`accounting.jsonl`), every call event (`events.jsonl`), replay mode, the prompt templates. |
| `store.py` | The SQLite schema: units, extractions, retrievals, proposals, votes, rulings, decisions, sets, council_rulings. |
| `retrieve.py` | The hub index and cosine retrieval; `protocol_checked()` (exit 6 unless `protocol.json` matches `protocol.sha256`). |
| `run.py` | The live passes: calibrate, evaluate, test, policy, replay, reconcile. |
| `gold.py`, `audit.py`, `score.py` | The reference standard (blind council packets, double-ruled draw, assisted pairs, missed-link draw), the single-source audit, the scores and gate decisions. |
| `queue.py`, `undo.py`, `conform.py`, `result.py` | The review queue, the two undo drills, graph conformance, RESULT.md. |
| `fixture_ops.py`, `check.py` | The fixture harness the goal's black-box tests drive (seed, replay, policy, conformance, gates, protocol, gold, provider_call, triad) and the card table. |
| `freeze.py`, `fetch_sources.py`, `sources.json`, `data/`, `reference_semantics.json` | The pinned sources, the sample draw, the budget and the NIST informative references (associations only). |
| `protocol.json`, `protocol.sha256`, `index.json`, `config.frozen.json`, `set_r*.json` | The frozen protocol, the pinned hub index, the calibrated configuration and the three sets. |
| `run.sqlite`, `accounting.jsonl`, `events.jsonl`, `calibration.jsonl` | The store and the ledgers. |
| `gold.*.json`, `audit.*.json`, `score.json`, `score.test.json`, `queue.json`, `policy_report.json`, `replay_decisions.json`, `undo_report.json`, `conformance.json` | The outputs RESULT.md reads. |
| `baseline/`, `graphs/` | Appendix C at the paper commit with its pinned hash and the allowed noun normalizations; the same two graphs in this domain's nouns. |
| `expected/check_output.txt`, `package/` | The expected `check.py --table` output; the publication package, left unpublished. |

## Choices not fixed by the plan

1. **Provider routing.** Google's direct API was unavailable (402) throughout, so Gemini is reached through OpenRouter; the seat is still the Google family, and the OpenRouter cap holds the Mistral and Gemini spend together. Declared in `protocol.json` → `routing_note`.
2. **Council voices.** gemini-2.5-flash-lite (so the OpenRouter worst case stays under its cap) and mistral-large-2512 (mistral-large-4-0 reasons through the 250-token council ceiling and answers nothing). The protocol records what it supersedes and why.
3. **Reasoning models.** gpt-6-luna takes `reasoning_effort: none`; Claude Haiku 5.5 takes `thinking: disabled`; both are on every request and every accounting row (`decoding`).
4. **A reply that is not a vote.** A council reply that does not parse or names no level is re-asked once; a panel still short of three valid votes is unresolved: no label, outside every draw and every score row, counted. A voice asked twice without a vote is final and reused on reruns.
5. **The missed-link estimate under non-response.** The frozen weights come from the random draw and admit no non-response adjustment, so the Horvitz-Thompson estimate is reported only when every required ruling is in; otherwise `null` with the counts (Dorian's call).
6. **Publication of r3.** r3 is recorded as a set and left unpublished: its scope is the sealed-test split, and a published r3 would drop the evaluation split's public read. The undo drills run on a copy taken after r2 and before r3.
7. **Replay scope.** Live decisions are the published set's and r3's. The calibration revisions are not replayed: an incomplete extraction attempt is not stored, so one unit's early revisions are not reproducible from the store (backlog).
8. **Weak labels for calibration.** The pinned NIST informative references, link versus no link only; an enhancement inherits its base control's links; absence is never a negative.
9. **Index pins.** When the protocol was re-frozen for a council voice, `index.json` was re-pinned to the new hash with its vectors unchanged, so every stored retrieval stands.
10. **The sets table.** Publication is the `active` flag on `run.sqlite` → `sets`; `run.py evaluate` records r1 (inactive) and publishes r2; `run.py policy` records r3 inactive.

## Deposit

Archived on Zenodo as its own record, version 1.0.0, under the MIT License (`LICENSE`). DOI: https://doi.org/10.5281/zenodo.24353215. `data/` is excluded from git (fetched by `fetch_sources.py` and pinned by `sources.json`) but included in the archive, so the archive runs without a fetch.

What runs from the archive alone: the card table (`python3 -I check.py`: 26 cards PASS, E1 present_unreachable), `conform.py` (zero deviations), `tests_adapter.py`, `tests_run.py`, `tests_gold.py` and `tests_item8.py`. The 11 scenario rows of `check.py --table` read the goal suite from the repository root (`goals/sp800-53-to-csf2-2/tests/`), and `tests_cards.py` and `tests_conform.py` read the sibling runner and that suite, so from the archive alone the scenario rows print `not run (goal suite absent)`. From a fresh clone of the repository with `data/` in place, the output is deterministic: `python3 -I check.py --table | diff - expected/check_output.txt` prints nothing, and every `tests_*.py` ends OK.
