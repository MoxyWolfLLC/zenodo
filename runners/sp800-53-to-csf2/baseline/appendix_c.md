## Appendix C: Full Mermaid Source

These two graphs are the canonical figures. The overviews and panels in Sections 4 and 5 are cut from them, and their card ids match the reference runner. They’re shown as plain code because they’re too wide to render at page width. Paste either into a Mermaid renderer to see it whole.

**C.1 The run graph**

```text
%%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%
%% Figure 1 (canonical). The run graph, revision 2: one unit from extraction to recorded decision.
%% Lanes = who acts. Diamonds = rule cards in the active set revision, in a fixed normative order;
%% first match wins; the last card of every list is "otherwise". Models propose and vote; cards decide.
%% Card ids match the reference runner (Appendix D). Panels 1a to 1c are literal cuts of this graph.
%% SSS, CA and HR condense the audit, curator and ruling lists of Table 4 (A1 to A2, K1 to K3, H1 to H4);
%% a sampled SSS record also enters RQ, and a pending curator item (K3) stays queued.
%% M is the panel size (three by default); a majority is a strict majority of M.
flowchart LR
 subgraph DB["Store: state, stored outputs, ledger"]
  WI[("Work items<br/>(gap and hub attempts)")]
  SO[("Stored outputs: extraction, candidate set<br/>with retriever, index and hub versions,<br/>proposals, votes, each with question<br/>version and evidence")]
  RQ[("Review queue<br/>(reason code)")]
  LED[("Ledger: decisions and links<br/>(set revision, level, route, reason)")]
 end
 subgraph RUN["Runner and active card set (set rN)"]
  START(["Pick next unit"])
  I1{"I1 Out of scope<br/>by metadata?"}
  I3{"I3 Incomplete,<br/>retries left?"}
  I4{"I4 Incomplete,<br/>retries spent?"}
  SSS{"SS-scope Out of<br/>scope by content?"}
  I5{"I5 otherwise"}
  RET["Retrieve candidates"]
  SSR{"SS-retrieval<br/>Nothing retrieved?"}
  R2{"R2 otherwise"}
  E1{"E1 New hub entry,<br/>loops left?"}
  E2{"E2 New hub entry,<br/>loops spent?"}
  E3{"E3 Unverifiable, low<br/>confidence, conflict?"}
  E4{"E4 otherwise"}
  T1{"T1 Fewer than<br/>M valid votes?"}
  T2{"T2 Majority no match,<br/>gate open?"}
  T2G{"T2-gated<br/>Majority no match?"}
  T3{"T3 Majority same level,<br/>gate open?"}
  T3G{"T3-gated<br/>Majority same level?"}
  T4{"T4 Majority link,<br/>no majority level?"}
  T5{"T5 otherwise"}
  REC["Record decision and<br/>provenance (one transaction)"]
 end
 subgraph PRO["Proposer model"]
  EM["Extract the unit's content"]
  PR["Propose: answer (null = no match),<br/>level, confidence, verbatim quotes"]
 end
 subgraph TRI["Decision triad (three providers, none the proposer's)"]
  TV["Each answers the same question about<br/>the unit, candidate and quoted spans;<br/>never sees the proposer's answer"]
 end
 subgraph HUM["Reviewer and assessor"]
  HR["Rule on a queued item: accept at<br/>a level, no match, or out of scope"]
  CH["Challenge any ledger decision"]
 end
 subgraph CUR["Hub curator"]
  CA{"Approve new<br/>hub entry?"}
 end
 WI --> START --> I1
 I1 -- "yes: out of scope (rule)" --> REC
 I1 -- "otherwise" --> EM --> I3
 EM -- "stored" --> SO
 I3 -- "yes: attempts + 1" --> EM
 I3 -- "otherwise" --> I4
 I4 -- "yes: gap exhausted" --> RQ
 I4 -- "otherwise" --> SSS
 SSS -- "yes: out of scope (content),<br/>sampled for audit" --> REC
 SSS -- "otherwise" --> I5 --> RET
 RET -- "stored with versions" --> SO
 RET --> SSR
 SSR -- "yes: no match, nothing retrieved;<br/>audited by hub sample" --> REC
 SSR -- "otherwise" --> R2 --> PR
 PR -- "stored" --> SO
 PR --> E1
 E1 -- "yes" --> CA
 CA -- "yes: hub revision + 1,<br/>hub attempts + 1, reopen unit;<br/>prior pass superseded" --> RET
 CA -- "otherwise: entry declined,<br/>no match" --> REC
 E1 -- "otherwise" --> E2
 E2 -- "yes: hub loop exhausted" --> RQ
 E2 -- "otherwise" --> E3
 E3 -- "yes: evidence unclear" --> RQ
 E3 -- "otherwise" --> E4 --> TV
 TV -- "votes stored" --> SO
 TV --> T1
 T1 -- "yes: incomplete triad" --> RQ
 T1 -- "otherwise" --> T2
 T2 -- "yes: voted no match" --> REC
 T2 -- "otherwise" --> T2G
 T2G -- "yes: no-match gate closed" --> RQ
 T2G -- "otherwise" --> T3
 T3 -- "yes: write at agreed level" --> REC
 T3 -- "otherwise" --> T3G
 T3G -- "yes: level gate closed" --> RQ
 T3G -- "otherwise" --> T4
 T4 -- "yes: level split" --> RQ
 T4 -- "otherwise" --> T5
 T5 -- "tie or no majority" --> RQ
 RQ --> HR -- "ruling decides; stored with what<br/>was shown and who ruled" --> REC
 LED --> CH -- "challenge, with reason" --> RQ
 REC --> LED
```

**C.2 The policy graph**

```text
%%{init: {"flowchart": {"defaultRenderer": "elk"}}}%%
%% Figure 2 (canonical). The policy graph, revision 2: reproduction, policy evaluation, activation, undo.
%% Held-out units are predicted; held-out rulings only score those predictions, afterward.
%% Panels 2a to 2d are literal cuts of this graph. Card ids match the reference runner (Appendix D).
flowchart LR
 subgraph OWN["Policy owner"]
  PROP["Propose a card<br/>revision (set rN+1)"]
  ACT{"Activate<br/>set rN+1?"}
 end
 subgraph RUN["Runner"]
  REP["Reproduce a past decision:<br/>its own set over its own stored<br/>outputs, candidate set and ruling"]
  SAME{"Same route,<br/>level and reason?"}
  EVAL["Evaluate the changed policy,<br/>one candidate at a time"]
  REUSE{"Same question and<br/>same evidence as the<br/>stored output or ruling?"}
  CARDS["Predict: run set rN+1<br/>on held-out units"]
  SCORE["Score per level against<br/>held-out rulings, after predicting"]
  RPT["Report per level: precision and<br/>Wilson lower bound, missed links,<br/>no matches, coverage, re-asked<br/>outputs and their cost"]
  RD["Re-decide every live decision:<br/>replay the active set over stored<br/>outputs, ask only what it never had"]
  REV["Publication reversal:<br/>reactivate rN"]
  RCV["Data recovery: restore the<br/>rows in the bad write's<br/>recorded footprint"]
 end
 subgraph DB["Store"]
  SO[("Stored outputs, candidate sets,<br/>rulings (question version,<br/>evidence shown)")]
  DEC[("Decisions (set revision;<br/>superseded rows kept)")]
  ACR[("Activation records<br/>(who, when, report seen)")]
  WR[("Write footprints<br/>(rows, before, after)")]
  PUB[("Public reads<br/>(per level)")]
 end
 subgraph MOD["Proposer, triad and reviewers"]
  ASK["Asked again and stored;<br/>a ruling whose evidence<br/>changed goes back to review"]
 end
 subgraph EV["Evaluation split (fixed before calibration; never tuned on)"]
  HS["Held-out units"]
  HRL["Held-out rulings"]
 end
 DEC --> REP
 SO --> REP
 REP -- "zero model calls,<br/>no one asked to rule" --> SAME
 SAME -- "yes" --> OK(["Reproduced"])
 SAME -- "otherwise" --> BUG(["Replay defect:<br/>investigate"])
 PROP --> EVAL --> REUSE
 REUSE -- "yes: reuse" --> SO
 SO -- "operational rulings only;<br/>held-out rulings never<br/>enter prediction" --> CARDS
 REUSE -- "otherwise" --> ASK --> CARDS
 HS -- "units to predict" --> CARDS
 CARDS -- "predictions" --> SCORE
 HRL -- "answers, for<br/>scoring only" --> SCORE
 SCORE --> RPT --> ACT
 ACT -- "yes" --> ACR
 ACR --> RD --> DEC
 SO --> RD
 RD -- "what it never had" --> ASK
 DEC --> PUB
 ACT -- "otherwise" --> KEEP(["Set rN stays active"])
 PUB -. "undo 1. Pass: reads equal<br/>a fresh replay of rN" .-> REV
 REV --> RD
 WR -. "undo 2. Pass: only footprint<br/>rows change, to pre-write values" .-> RCV
 RCV --> DEC
```

