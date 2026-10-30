# Zenodo deposits

Research artifacts published to Zenodo from this repository. Each artifact gets its own Zenodo record and DOI: its folder holds a `.zenodo.json`, and `scripts/zenodo_upload.py` zips that folder and uploads it as a draft, then publishes it on a second run with `--publish`. The Zenodo GitHub integration is off for this repo on purpose, because a GitHub release would archive every artifact together as one record.

| Directory | What it is | Paper | DOI |
| --- | --- | --- | --- |
| [`runners/models-propose-rules-decide/`](runners/models-propose-rules-decide/) | Reference runner for the decision procedure: rule cards, a three-model vote gate, replay, policy re-evaluation and two kinds of undo, checked mechanically over synthetic fixtures | Cougias D. *Models Propose, Rules Decide: An Auditable Decision Procedure for AI-Assisted Mapping.* Manuscript, 2026. | [10.5281/zenodo.23819764](https://doi.org/10.5281/zenodo.23819764) |

Layout: one directory per artifact under a kind (`runners/` for executable reference implementations). Each artifact directory is self-contained, with its own README, LICENSE, `.zenodo.json`, inputs and the expected output of its checks under `expected/`.

Code is under the MIT License (`LICENSE`). Fixtures are synthetic and carry no real framework, occupational or personal data.
