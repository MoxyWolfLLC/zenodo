# Zenodo deposits

Research artifacts published to Zenodo from this repository. Each release of the repository is archived by Zenodo and given a DOI; `.zenodo.json` at the root carries the record's metadata.

| Directory | What it is | Paper |
| --- | --- | --- |
| [`runners/models-propose-rules-decide/`](runners/models-propose-rules-decide/) | Reference runner for the decision procedure: rule cards, a three-model vote gate, replay, policy re-evaluation and two kinds of undo, checked mechanically over synthetic fixtures | Cougias D. *Models Propose, Rules Decide: An Auditable Decision Procedure for AI-Assisted Mapping.* Manuscript, 2026. |

Layout: one directory per artifact under a kind (`runners/` for executable reference implementations). Each artifact directory is self-contained, with its own README, inputs and the expected output of its checks under `expected/`.

Code is under the MIT License (`LICENSE`). Fixtures are synthetic and carry no real framework, occupational or personal data.
