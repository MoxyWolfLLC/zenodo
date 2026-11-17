# sp800-53-to-csf2

1. Sources pinned: fetch SP 800-53 Rev. 5 and CSF 2.0 from NIST's OSCAL/CPRT JSON and the CSF 2.0 informative references; write `sources.json` with release, URL, retrieval date and SHA-256 per file; count active base controls and enhancements from the pinned catalog; record which reference entries carry five-class semantics and which are untyped associations
    - `python3 -I fetch_sources.py` writes `data/` and `sources.json`, prints each file's name, bytes and SHA-256 to stdout, exits 0; a hash mismatch on re-run prints the two hashes to stderr and exits 2
    - `sources.json` names the active base-control count and enhancement count computed from the catalog, never typed in
    - `reference_semantics.json` tags every informative-reference entry `five_class`, `association` or `absent` with the field that justifies the tag
2. Protocol frozen: `protocol.json` holds the exact sample size within 300 to 400, the strata and selection probabilities, the family-preserving three-way split, the adjudication policy and rubric, the missed-link estimand and inclusion probabilities, the anchoring comparison, the 20% double-ruled draw, r3's change, and the budget calculation with pinned model ids, prices and token ceilings; `protocol.sha256` written before any model call
    - `python3 -I freeze.py` writes `protocol.json` and `protocol.sha256`, prints the hash to stdout, exits 0; refuses with exit 3 and a stderr line if any unit's family is split across splits
    - the budget calculation shows calls × max tokens × price per provider, totals under $60 with a stated reserve for validation and reporting
3. Provider adapter: `providers.py` calls OpenAI (proposer), Anthropic, Google and Mistral via OpenRouter (triad) with versioned prompt templates for extraction, proposal and vote; every call stores usage, provider-reported cost where returned, and the question and evidence identities of Section 3; a budget guard refuses a call whose maximum expected charge exceeds the remaining allowance
    - a refused call prints `refused: budget` with the remaining allowance to stderr, writes an accounting row, and returns no output; the runner exits 4 if a pass stops on it
    - `accounting.jsonl` distinguishes `provider_reported`, `usage_priced` and `unpriced` per row
    - unit tests run the adapter against a recording stub with no network and pass under `python3 -I -m unittest`
4. Retriever and store: embeddings over the 106 subcategories, cosine top-8, versioned index with its hash; SQLite store with per-stage identities; `--replay` mode that disables all outbound calls and counts attempts
    - `store.py` creates the schema; `retrieve.py` writes `index.json` with model id, dimension and hash
    - any outbound attempt in replay mode raises, is counted, and the runner exits 5
5. Domain parameters and cards: Table 5 filled for this domain as set r1 with every gate closed; direction 800-53 → CSF; five labels with "no relationship" as the null; the paper's card lists reused unchanged from runner 1.1.0 with domain predicates supplied through the structured outputs
    - `cards.py` imports the 1.1.0 card lists by copy, not by edit; `python3 -I check.py` reports every card id reachable on fixtures
    - `set_r1.json` lists every gate closed and the engine hash
6. Calibration pass: run the calibration split, tune prompts, threshold and k with every revision recorded in `calibration.jsonl`, freeze the configuration as `config.frozen.json` with its hash
    - `run.py calibrate` prints per-revision cost and the chosen configuration to stdout, exits 0; exits 4 on budget refusal
    - no evaluation or test unit id appears in `calibration.jsonl`
7. Evaluation pass and gold: `run.py evaluate` stores predictions for the evaluation split; `gold.py` assembles the reference standard under the frozen policy, NIST where eligible, blind primary council elsewhere, the double-ruled draw, and the blind-versus-assisted matched pairs in separate sessions; `score.py` reports per level and qualifies gates; set r2 frozen
    - every primary gold packet is stored and `gold.py audit` exits 0 only when none contains a proposal, vote, confidence, route or prediction
    - `score.json` reports per level: n, correct, Wilson lower bound, gate decision, and `insufficient evidence` where n is below 25; within-family dependence reported as counts of families contributing more than one outcome at a level
8. Sealed-test pass under r2, then r3: `run.py test` stores predictions before any ruling is read; `run.py policy r3` applies the predeclared threshold change, derives expected invalidations from the stage-identity rules, compares to observed, and reports dollars; replay of every live decision with the network disabled; both undos in isolated local stores
    - `policy_report.json` lists expected and observed re-asked calls per stage and their equality
    - `undo_report.json` shows complete public reads equal after reactivation, and five footprint rows restored with zero untargeted rows changed
9. Graph conformance: `graphs/run.mmd` and `graphs/policy.mmd` redrawn with the domain's nouns; `conform.py` diffs them against the pinned Appendix C baseline copied from the paper at a named commit
    - `python3 -I conform.py` prints zero deviations or one line per deviation to stdout, exits 0 either way, exits 6 if the baseline file's hash doesn't match the pinned one
    - E1 reported as present and unexercised in the same output
10. Deliverables: `RESULT.md` with every Table 6 measure, the HT missed-link estimate with weights and uncertainty, the anchoring comparison, the conformance result, the policy-change and undo reports, and the accounting totals by evidence class; `README.md` with the runner's open choices; `expected/check_output.txt`; a publication package under `package/` left unpublished
    - `python3 -I check.py` prints a PASS/FAIL table covering every goal-test scenario on fixtures and exits 0
    - `RESULT.md` carries no number that isn't traceable to a file in the folder, and names every missing cost evidence
