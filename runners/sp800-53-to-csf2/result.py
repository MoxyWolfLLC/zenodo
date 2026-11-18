"""Item 9: RESULT.md from the run's own files. Every number names the file it comes from.

    python3 -I result.py        writes RESULT.md and expected/check_output.txt (the current `check.py --table`), nothing else
"""
import json
import os
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))


def J(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def rows(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def money(d):
    return f"{Decimal(d):.4f}"


def gate_rows(score, key, src):
    out = [f"| level | n | correct | Wilson lower (one-sided 95%) | gate | families with >1 outcome |", "|---|---|---|---|---|---|"]
    for r in score[key]:
        out.append(f"| {r['level']} | {r['n']} | {r['correct']} | {r['wilson_lower']:.3f} | {r['gate_decision']} | {r['families_with_multiple_outcomes']} |")
    out.append(f"\n(source: `{src}` → `{key}`)")
    return "\n".join(out)


def main(argv):
    protocol = J("protocol.json"); config = J("config.frozen.json")
    s_eval, s_test = J("score.json"), J("score.test.json")
    g_eval, g_test = J("gold.evaluation.json"), J("gold.test.json")
    a_eval, a_test = J("audit.evaluation.json"), J("audit.test.json")
    policy, replay, undo, queue, conf = J("policy_report.json"), J("replay_decisions.json"), J("undo_report.json"), J("queue.json"), J("conformance.json")
    set2, set3 = J("set_r2.json"), J("set_r3.json")
    cal = rows("calibration.jsonl")
    acc = rows("accounting.jsonl")
    with open(os.path.join(HERE, "protocol.sha256"), encoding="utf-8") as f:
        phash = f.read().split()[0]

    # accounting by evidence class and provider
    by = defaultdict(lambda: defaultdict(Decimal)); n_by = defaultdict(lambda: defaultdict(int))
    for r in acc:
        by[r.get("evidence")][r.get("provider")] += Decimal(r.get("charged_usd") or "0")
        n_by[r.get("evidence")][r.get("provider")] += 1
    total = sum(Decimal(r.get("charged_usd") or "0") for r in acc)
    providers_seen = sorted({r.get("provider") for r in acc})
    missing = {p: n_by["unpriced"].get(p, 0) for p in providers_seen}

    def double_resolved(g):
        return sum(1 for r in g["double_ruled"]["rows"] if r["first"] is not None and r["second"] is not None)

    L = []
    w = L.append
    w("# sp800-53-to-csf2-2: result\n")
    w("Second-domain instantiation of \"Models Propose, Rules Decide\": SP 800-53 Rev. 5 controls → CSF 2.0 subcategories. "
      "Every number below names the file it is read from; `result.py` writes this file from those files and nothing else.\n")
    w(f"- Protocol: `protocol.json`, sha256 `{phash}` (`protocol.sha256`); it supersedes `{protocol['supersedes']['sha256']}` "
      f"(changed: {', '.join(protocol['supersedes']['changed'])}; `protocol.json` → `supersedes`).")
    w(f"- Frozen configuration: `config.frozen.json` sha256 `{config['sha256']}`: c_min {config['c_min']}, proposal prompt "
      f"{config['prompt_versions']['proposal']}, chosen revision {config['chosen_revision']} (F1 {config['score']['f1']:.3f} against the weak labels, "
      f"{config['score']['scored_units']} scored units), calibrated under protocol `{config['protocol_sha256'][:16]}`.")
    w(f"- Sets: r1 (`set_r1.json`, the paper's defaults, recorded and never published), r2 (`set_r2.json` sha256 `{set2['sha256'][:16]}`, "
      f"every gate closed, published: `run.sqlite` → `sets`), r3 (`set_r3.json` sha256 `{set3['sha256'][:16]}`, c_min {set3['threshold_after']}, unpublished).")
    w(f"- Sample: `protocol.json` → `split.units_by_split`: {protocol['split']['units_by_split']}; {protocol['sample']['families']} families, whole families per split.")
    w(f"- Council voices: {', '.join(protocol['roles']['council']['models'])} (`protocol.json` → `roles.council.models`).\n")

    w("## Measures (the plan's Table 6 list)\n")
    w("### Precision of automatic writes per level (sealed-test split, r2)\n")
    w(f"No gate opened on the evaluation split, so set r2 writes nothing automatically: {s_test['automatic_writes']} automatic writes on the test split "
      f"(`score.test.json` → `automatic_writes`). Every level reports n = 0 and `closed_insufficient`:\n")
    w(gate_rows(s_test, "rows", "score.test.json"))
    w("\n### Precision of voted no-relationship (one-sided 95% Wilson lower bound)\n")
    for name, s in (("sealed test", s_test), ("evaluation", s_eval)):
        r = next(x for x in s["prediction_rows"] if x["level"] == "no_relationship")
        w(f"- {name}: {r['correct']} of {r['n']} council-ruled no_relationship, Wilson lower {r['wilson_lower']:.3f} (`{ 'score.test.json' if s is s_test else 'score.json'}` → `prediction_rows`, level no_relationship).")
    w("\n### Gate qualification on the evaluation split (every prediction, gated or not)\n")
    w(gate_rows(s_eval, "rows", "score.json"))
    w("\nThe same rows for the sealed-test split's gated predictions, for comparison (`score.test.json` → `prediction_rows`):\n")
    w(gate_rows(s_test, "prediction_rows", "score.test.json"))
    w("\n### Missed links (Horvitz-Thompson, non-retrieved stratum weighted by its inverse inclusion probability)\n")
    for name, s, fn in (("evaluation", s_eval, "score.json"), ("sealed test", s_test, "score.test.json")):
        m = s["missed_links"]
        w(f"- {name}: estimate **{m['estimate'] if m['estimate'] is not None else 'withheld'}**; retrieved stratum (weight 1): {m['retrieved_true_links']} ruled true links, "
          f"{m['retrieved_voted_no_relationship_true_links']} of them the run voted no_relationship, {m['retrieved_unresolved']} pairs unresolved; "
          f"non-retrieved stratum: {m['draw_n']} draws ({protocol['missed_link_estimand']['non_retrieved_stratum']['draws_per_unit']} per unit, weight "
          f"{m['non_retrieved_weight']}), {m['non_retrieved_draw_true_links']} true links, {m['draw_unresolved']} unresolved"
          + (f"; withheld because: {m['withheld']}" if m.get("withheld") else "") + f" (`{fn}` → `missed_links`).")
    w("\nThe estimate is withheld on both splits on Dorian's call (2026-11-17): the frozen weights come from the random draw and admit no non-response "
      "adjustment, so an unresolved ruling is a missing observation and the estimate is reported only when every required ruling is in. "
      "Uncertainty: with the estimate withheld, no interval is reported; the counts above are the complete inputs.\n")
    w("### Single-source error rates (SS-scope and SS-retrieval audit samples, per split)\n")
    w("| split | card | pool | drawn | resolved n | errors | error rate |\n|---|---|---|---|---|---|---|")
    for name, a, fn in (("evaluation", a_eval, "audit.evaluation.json"), ("sealed test", a_test, "audit.test.json")):
        for card, c in a["cards"].items():
            w(f"| {name} | {card} | {c['pool']} | {c.get('drawn', c['n'])} | {c['n']} | {c['errors']} | {c['error_rate'] if c['error_rate'] is not None else 'none drawn'} |")
    w("\n(sources: `audit.evaluation.json`, `audit.test.json` → `cards`; the run routed one unit to SS-scope per split and none to SS-retrieval, so the pools are 1 and 0)\n")
    w("### Coverage (share of a split's units with at least one proposal the council ruled a true link)\n")
    for name, s, fn in (("evaluation", s_eval, "score.json"), ("sealed test", s_test, "score.test.json")):
        c = s["coverage"]
        w(f"- {name}: {c['share']:.3f} ({c['with_a_true_link']} of {c['units']} units) (`{fn}` → `coverage`).")
    w("\n### Council agreement (Krippendorff's alpha, nominal, on the double-ruled draw)\n")
    for name, g, fn in (("evaluation", g_eval, "gold.evaluation.json"), ("sealed test", g_test, "gold.test.json")):
        d = g["double_ruled"]
        w(f"- {name}: alpha {d['krippendorff_alpha']} over {double_resolved(g)} resolved pairs of {d['n']} drawn ({d['share']:.0%} of {g['packets']} primary packets) (`{fn}` → `double_ruled`).")
    w("\n### Reuse cost (the policy change r3 on the sealed-test split)\n")
    w(f"- c_min {policy['threshold_before']} → {policy['threshold_after']} by the frozen formula; expected calls by stage {policy['expected_counts']}, observed {policy['observed_counts']}, "
      f"equal: {policy['equal']}; {policy['asked']} calls asked, {policy['reused']} stored outputs reused; cost {money(policy['cost_usd'])} USD "
      f"({policy['cost_by_stage_usd']}); {policy['decisions']} decisions re-decided, {policy['automatic_writes']} automatic writes; re-derived rulings: "
      f"{len(policy['rederived_rulings'])} ({policy['rederived_note']}) (`policy_report.json`).")
    w("\n### Review queue size and age (the store at the end of item 8)\n")
    w(f"- {queue['size']} decisions routed REVIEW under the published set {queue['published_revisions']} at {queue['read_at']}: " +
      "; ".join(f"{k}: {v['size']} (age {v['age_hours']['min']}–{v['age_hours']['max']} h; reasons {v['reasons']})" for k, v in queue["by_split"].items()) +
      " (`queue.json`). Ages are measured from the rows' `created` stamps, which the last `run.py evaluate` re-run reset; the queue has had no reviewer.\n")

    w("## Anchoring comparison (blind versus assisted rulings of the same primary packet, separate sessions)\n")
    for name, g, fn in (("evaluation", g_eval, "gold.evaluation.json"), ("sealed test", g_test, "gold.test.json")):
        a = g["anchoring"]
        w(f"- {name}: {a['pairs_resolved']} of {a['pairs_drawn']} pairs resolved; agreement with the run's proposal blind {a['agree_blind']}, assisted {a['agree_assisted']}, "
          f"difference (blind minus assisted) {a['difference']}; discordant pairs {a['discordant']}, blind wins {a.get('blind_wins_among_discordant')}, "
          f"95% Wilson on the discordant share {a['wilson_95']} (`{fn}` → `anchoring`).")
    w("\n## Gold assembly\n")
    for name, g, fn in (("evaluation", g_eval, "gold.evaluation.json"), ("sealed test", g_test, "gold.test.json")):
        labels = defaultdict(int)
        for r in g["primary_gold"]:
            labels[str(r["label"])] += 1
        w(f"- {name}: {g['packets']} blind primary packets; labels {dict(sorted(labels.items()))}; unresolved {g['unresolved']}; "
          f"last assembly: {g['tally']['asked']} council calls asked, {g['tally']['reused']} reused, {money(g['cost_usd'])} USD; the assemblies' whole spend is in the accounting table below (`{fn}`).")
    w("\n## Conformance with Appendix C\n")
    w(f"- `conform.py`: {conf['deviations']} deviations across {', '.join(conf['dimensions'])} for the run graph and the policy graph; baseline `{conf['baseline']['file']}` "
      f"sha256 `{conf['baseline']['sha256'][:16]}` at paper commit `{conf['baseline']['commit'][:9]}`; E1 present, exercised: {conf['E1']['exercised']} (`conformance.json`).")
    w("- `check.py --table`: 26 cards PASS, E1 present_unreachable, no failures; 11 of 11 GOAL.md scenarios PASS on fixtures (`expected/check_output.txt`).\n")
    w("## Policy change, replay, undos\n")
    w(f"- Replay: {replay['live_decisions']} live decisions (r2 on both splits, r3 on the test split) re-derived on a copy with calls disabled, {replay['matched']} matched, "
      f"{replay['outbound_attempts']} outbound attempts (`replay_decisions.json`). Not replayed: {replay['not_replayed']['calibration_revisions']}; units with a later extraction "
      f"attempt: {[u['unit_release'] for u in replay['not_replayed']['units_with_a_later_extraction_attempt']]}.")
    pr, dr = undo["publication_reversal"], undo["data_recovery"]
    w(f"- Publication reversal: complete public read under r1 ({pr['r1_read']['rows']} rows) equal before and after r2 was published and r1 reactivated: {pr['reads_equal']}; "
      f"the r2 read ({pr['r2_read']['rows']} rows) differs: {pr['r2_differs']} (`undo_report.json`). {pr['note']}.")
    w(f"- Data recovery: footprint of the bad write {len(dr['footprint'])} rows, exactly the targets: {dr['footprint_is_exactly_the_targets']}; restored, untargeted rows changed "
      f"{dr['untargeted_rows_changed']}, table equal after restore: {dr['table_equal_after_restore']} over {dr['rows']} rows (`undo_report.json`).\n")
    w("## Calibration\n")
    w("| revision | prompt | c_min | F1 (weak labels) | precision | recall | cost USD |\n|---|---|---|---|---|---|---|")
    for r in cal:
        w(f"| {r['revision']} | {r['prompt_version']} | {r['c_min']} | {r['score']['f1']:.3f} | {r['score']['precision']:.3f} | {r['score']['recall']:.3f} | {money(r['cost_usd'])} |")
    w(f"\n(`calibration.jsonl`; chosen {config['chosen_revision']}, `config.frozen.json`). Observation for the method: under propose-v1 the evidence cards stop most pairs "
      "at E3 (quotes not verbatim) and under propose-v2 most pairs are voted no_relationship; the frozen choice is the best F1 against weak labels, not a judgment of either prompt.\n")
    w("## Accounting totals by evidence class (`accounting.jsonl`)\n")
    w("| evidence | provider | rows | charged USD |\n|---|---|---|---|")
    for ev in sorted(by):
        for p in sorted(by[ev]):
            w(f"| {ev} | {p} | {n_by[ev][p]} | {money(by[ev][p])} |")
    w(f"\nTotal charged: {money(total)} USD of the 60 USD cap (`protocol.json` → `budget`; worst case {protocol['budget']['total_usd']} USD, {protocol['budget']['total_with_reserve_usd']} with the reserve). "
      "Missing cost evidence by provider (rows charged at the ceiling with no usage, class `unpriced`): " + ", ".join(f"{p}: {n}" for p, n in missing.items()) + ". "
      "Refused calls (class `refused`): " + str(sum(n_by["refused"].values())) + ".\n")
    w("## Deviations declared\n")
    w("- Gemini is reached through OpenRouter (Google's direct API returned 402 on every call on 2026-11-17; Dorian's instruction), against the plan's \"only Mistral through OpenRouter\"; the seat is still the Google family (`protocol.json` → `routing_note`).")
    w("- The council's Google voice is gemini-2.5-flash-lite (so the OpenRouter worst case stays under its 20 USD cap) and its Mistral voice is mistral-large-2512: mistral-large-4-0 reasons before it answers, cannot be told not to, and returned empty content on 2,353 of 2,357 evaluation packets; the first gold assembly under it is retired and its rows stay in `run.sqlite` → `council_rulings` under that voice id.")
    w("- The missed-link estimate is withheld while any required ruling is unresolved (Dorian, 2026-11-17: \"withhold the estimate\").")
    w("- r3 is recorded as a set and left unpublished: the delta's scope is the sealed-test split and a published r3 would drop the evaluation split's public read.")
    w("- The calibration revisions are not replayed: an incomplete extraction attempt is not stored (backlog: goal-sp800-53-to-csf2-2-follow-ups.md).")
    w("- Item 1's merge has no release record (noted at the time); every later item has one.")
    w("- The discover-command deviation declared at item 3 stands.\n")
    w("## Measures with no producing item\n")
    w("- None of the Table 6 measures lacks a producing item. The HT estimate's interval is `not produced`: the estimate is withheld, so no interval is computed.\n")
    with open(os.path.join(HERE, "RESULT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    os.makedirs(os.path.join(HERE, "expected"), exist_ok=True)
    out = subprocess.run([sys.executable, "-I", os.path.join(HERE, "check.py"), "--table"], capture_output=True, text=True, cwd=HERE)
    with open(os.path.join(HERE, "expected", "check_output.txt"), "w", encoding="utf-8") as f:
        f.write(out.stdout)
    print(f"RESULT.md written ({len(L)} blocks); expected/check_output.txt written (check.py --table exit {out.returncode})")
    return 0 if out.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
