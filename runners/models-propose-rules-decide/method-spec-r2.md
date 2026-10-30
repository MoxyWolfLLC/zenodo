# Method spec, revision 2 (normative for the paper rewrite and the reference runner)

## Run graph: cards in fixed order, first match wins, last is always "otherwise"

Every unit and candidate ends in exactly one recorded decision. Decision records exist per candidate, or per unit when no candidate exists.

### Intake (per unit)
- **I0 Extract**: the proposer extracts the unit's content. Output stored (P7).
- **I1 Scope (deterministic)**: metadata-only scope rules (e.g. statement type). Out of scope → RECORD unit decision `out_of_scope_rule` (no model involved).
- **I3 Evidence gap**: extraction incomplete and gap_attempts < G_max → retry I0 (gap_attempts + 1).
- **I4 Gap exhausted**: extraction incomplete and gap_attempts >= G_max → REVIEW `gap_exhausted`.
- **I2 Scope (content-based)**: a scope judgment that depends on extracted content (e.g. "no work object"). Out of scope → RECORD unit decision `out_of_scope_content` under named single-source card `SS-scope`, with an audit sample rate a_scope (sampled items ALSO go to review queue `audit_scope`; the record stands until a ruling supersedes it).
- **I5 otherwise** → retrieve.

### Retrieval (per unit)
- **R0 Retrieve**: inherited candidates (if the Route parameter supplies them) plus top-k retrieval. Store the candidate set with retriever version, index version and hub revision (P7, P8).
- **R1 Nothing retrieved**: empty candidate set → RECORD unit decision `no_match_nothing_retrieved` under named single-source card `SS-retrieval`; audited by the evaluation's sample of non-retrieved hub entries (missed-link estimate).
- **R2 otherwise** → propose, per candidate.

### Proposal and evidence (per candidate)
- **P0 Propose**: proposer answers on the scale (null = no match), level, confidence, verbatim quotes. Stored with question version and evidence (P7).
- **E1 Needs new hub entry** and hub_attempts < H_max → HUB QUEUE. Curator: approve → hub revision + 1, unit reopens at R0 (hub_attempts + 1) and the unit's other in-flight candidates are dropped and re-derived; decline → RECORD `no_match_hub_declined` (curator decision stored as a ruling, P7).
- **E2 Needs new hub entry** and hub_attempts >= H_max → REVIEW `hub_loop_exhausted`.
- **E3 Unverifiable quotes, confidence < c_min, or conflict** → REVIEW `evidence_unclear`. (Placed before any negative so a low-confidence negative is never final.)
- **E4 otherwise** → TRIAD. Both positive AND negative proposals go to the triad. The triad does not see the proposer's answer, level or confidence; it sees the unit, the candidate and the spans the proposer quoted. The proposer's provider does not sit on the triad.

### Triad gate (per candidate), n = 3 votes from 3 providers
- **T1 Fewer than n valid votes** (missing, refused, off-scale) → REVIEW `incomplete_triad`.
- **T2 At least 2 votes = no match, and the no-match acceptance gate is open** → RECORD `no_match_voted`.
- **T2-gated At least 2 votes = no match** (gate closed) → REVIEW `nomatch_gate_closed`.
- **T3 At least 2 votes give the same non-null level, and that level's acceptance gate is open** → WRITE link at that level (`auto_accept`).
- **T3-gated At least 2 votes give the same non-null level** (gate closed) → REVIEW `level_gate_closed`.

Gates are set-revision parameters (the "closed" list): an outcome's gate opens only when the evaluation of Section 9 shows its precision lower bound clears the bar. Opening or closing one is a deterministic change: it re-asks no model output.
- **T4 At least 2 votes non-null, no level plurality** → REVIEW `level_split`.
- **T5 otherwise** → REVIEW `no_majority`. (Unreachable at n = 3 for every input, valid or not: T1 takes any invalid vote first, and an exhaustive enumeration of all 216 vote combinations puts none on T5. Kept because P3 requires it and n is a parameter.)

Relationship agreement (T2 vs T3/T4) now routes differently from level agreement. Level is set only by T3 or by a reviewer.

### Review (per queued item)
- Reviewer rules: accept at a level (RECORD `human_accept`, never counted as automatic), no match (RECORD `no_match_ruled`), or out of scope. Stored with what was shown and the reviewer's confirmed identity.

### Challenge (any ledger decision, any time)
- An assessor or reviewer queues any decision for re-ruling with a reason code → REVIEW `challenge`. The new ruling supersedes; both stay in the ledger. (Answers "how do we take this one link back?")

## Policy graph

- **Reproduce**: decision d under its own set revision over its own stored outputs (extraction, candidate set, proposal, votes, ruling). Zero model calls, zero new rulings. Same outcome = same route, same level, same ledger row content apart from timestamps. Different → replay defect.
- **Evaluate rN+1**: per candidate, reuse a stored output only if same question (template id and version, rendered prompt hash, model id and provider, decoding parameters, scale version) AND same evidence (unit release, candidate-set hash, hub revision, quoted spans). Reuse a ruling only if the item and the evidence shown are unchanged. Otherwise ask again / queue again. Report reused vs asked counts. A deterministic change reuses everything it can and pays only for outputs it needs and never had (e.g. candidates newly routed to the triad).
- **Activate rN+1**: activation record stored (who, the evaluation report they saw, time). Every live decision is re-decided by replaying rN+1 over stored outputs, asking only what's missing; prior decisions stay in history marked superseded. (Answers "would it still exist today?")
- **Undo 1, publication reversal**: re-activate rN. Pass criterion: after reversal, public reads equal a fresh replay of rN over stored outputs (for units first decided under rN+1, rN is replayed over their stored outputs; anything rN needs and never had is asked/queued and reported).
- **Undo 2, data recovery**: every write records its footprint (rows touched, before and after values). Pass criterion: after recovery, exactly the footprint rows equal their pre-write values and no row outside the footprint changed.

## Engine
The shared helpers every card calls (vote validity, plurality, null count, quote check, audit sampling) are hashed into each set revision. Running a set whose engine hash differs from the current engine is refused; a helper change is a new set revision.

## Properties (revised)
- P1 No terminal outcome rests on the output of a single model or retriever unless a named single-source card accepts that risk; every such card is reported with its volume and audited error rate. (P1a: no model output writes the ledger directly.)
- P2 Retrieval only proposes.
- P3 Every branch is a card; fixed normative order; first match wins; last is "otherwise".
- P4 One agreement gate with distinct routes: voted no match (>= 2 null), automatic write (>= 2 same non-null level), level split, incomplete triad, no majority. A level is never derived from a non-plurality (no averaging, median or max).
- P5 Every route terminates: retry and hub loops are bounded by attempt counters.
- P6 No match is a result: every no-match is recorded with its reason and route.
- P7 Every output keeps its question and evidence: extraction, candidate set (retriever and index versions), proposal, vote, ruling, curator decision, set activation.
- P8 Units, hub entries and the retrieval index are versioned and append-only.
- P9 Reproduction costs nothing (including retrieval-dependent decisions).
- P10 A new policy reuses only what it may, and reports what it re-asked and the cost.
- P11 Levels survive every rollup, including the auto-accept gate; every figure declares its denominator and duplicate handling.
- P12 Two undos, two pass criteria (as above).

## Parameters
Domain parameters (need domain knowledge): unit, hub, answer scale, scope rules, route, question templates.
Method constants (domain-independent defaults, tuned on calibration only): panel size and providers, G_max, H_max, c_min, conflict definition, evidence-sufficiency test, single-source audit rates, per-level gate (bar, minimum n), split design.
Card structure (fixed by the method): the graphs, the card order, the outcome types.
