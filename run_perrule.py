# -*- coding: utf-8 -*-
"""
run_perrule.py - PER-RULE violation rate for FaithOpt
=====================================================================
This script reports, alongside the instance-level rate, the PER-RULE violation rate:

    per_rule_rate = (# gold rules unfaithfully encoded) / (# gold rules checked)

which separates "instances are hard because they carry many rules" from
"the model is bad at each individual rule."


Usage
  # smoke test, no API key (built-in mock LLM):
  python run_perrule.py --mock --runs 3 --splits identification multivariate

  # real run, mirrors the main experiment (note the Haiku model string has a date suffix):
  python run_perrule.py --runs 5 --temperature 0 --workers 10 \
      --models claude-opus-4-7 gpt-5.4 deepseek-v3.2 claude-haiku-4-5-20251001 qwen3-max gpt-4o-2024-11-20 \
      --splits multi identification multivariate single

Outputs
  perrule_runs.csv       one row per (model, split, run): instance_rate, per_rule_rate, n_rules, ...
  perrule_summary.csv    one row per (model, split): mean/sd for instance AND per-rule rates
  perrule_byfamily.csv   one row per (model, split, family): per-rule rate by constraint family
  perrule_summary.txt    human-readable table (paste this back)

Notes
  * Hard+Very-hard only, matching the main tables.
  * z3 verification runs in the main thread (z3 is not thread-safe); only the
    network fetch is parallelized, exactly as in formulator.py / run_multigen.py.
"""
import os, sys, csv, json, argparse, statistics
from collections import defaultdict

import formulator as F                 # reuse the existing harness unchanged
from faithopt_verifier import audit     # noqa: F401  (used indirectly via the per-rule audit below)

# ----------------------------------------------------------------------
SPLIT_FILES = {
    "single":         "FaithConstraint-OR_single.jsonl",
    "multi":          "FaithConstraint-OR_multi.jsonl",
    "identification": "FaithConstraint-OR_identification.jsonl",
    "multivariate":   "FaithConstraint-OR_multivariate.jsonl",
}
HARD_TIERS = {"Hard", "Very hard"}


def load_split(path):
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("_manifest"):
                continue
            recs.append(r)
    return recs


def make_caller(use_mock, temperature):
    """Return a caller matching formulator's call signature (same as run_multigen.py)."""
    if use_mock:
        return lambda prompt, rec: F.mock_llm(prompt, rec)
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["GLOBALAI_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://globalai.vip/v1"),
    )

    def caller(prompt, rec, _model):
        msgs = [{"role": "user", "content": prompt}]
        if _model.startswith(("gpt-5", "o1", "o3", "o4")):   # reasoning models: big budget, no temperature
            r = client.chat.completions.create(model=_model, messages=msgs, max_completion_tokens=8192)
        else:
            r = client.chat.completions.create(model=_model, messages=msgs, max_tokens=1024, temperature=temperature)
        return r.choices[0].message.content or ""
    return caller


# ----------------------------------------------------------------------
# PER-RULE scoring. We mirror formulator.score_raw exactly up to the audit call,
# then keep the rule-level verdicts instead of collapsing to a single boolean.
# Returns a dict with both instance-level and rule-level tallies for ONE generation.
# ----------------------------------------------------------------------
def score_raw_perrule(rec, raw, api_err, parse_fail_excludes):
    decls, gold = F.gold_of(rec)
    k = len(gold)                                   # gold rules in this instance
    fam = rec.get("constraint_family", "?")

    # ---- generation could not produce an auditable model ----
    if api_err:
        status = "api_error"
        if parse_fail_excludes:
            return dict(ok=False, status=status, k=k, fam=fam,
                        inst_viol=True, rules_checked=0, rules_viol=0, fam_rows=[])
        # conservative: all k rules count as failed (matches instance-level convention)
        return dict(ok=True, status=status, k=k, fam=fam,
                    inst_viol=True, rules_checked=k, rules_viol=k,
                    fam_rows=[(fam, False)] * k)

    M, status = F.parse_model(raw, rec)
    if status in ("format_error", "empty_after_clean", "symbolic_unresolved"):
        if parse_fail_excludes:
            return dict(ok=False, status=status, k=k, fam=fam,
                        inst_viol=True, rules_checked=0, rules_viol=0, fam_rows=[])
        return dict(ok=True, status=status, k=k, fam=fam,
                    inst_viol=True, rules_checked=k, rules_viol=k,
                    fam_rows=[(fam, False)] * k)

    try:
        report = audit(decls, M, gold)             # one verdict PER gold rule
    except Exception as e:
        if parse_fail_excludes:
            return dict(ok=False, status=f"verify_error:{e}", k=k, fam=fam,
                        inst_viol=True, rules_checked=0, rules_viol=0, fam_rows=[])
        return dict(ok=True, status=f"verify_error:{e}", k=k, fam=fam,
                    inst_viol=True, rules_checked=k, rules_viol=k,
                    fam_rows=[(fam, False)] * k)

    # ---- normal path: rule-level verdicts ----
    rules_viol = sum(1 for r in report if r["faithful"] is False)
    # ok_dropped_unknown: parser flagged a dropped rule whose status z3 could not settle;
    # formulator counts the INSTANCE as violated. For the per-rule tally we add one failed
    # rule (the dropped one) so the rule count is consistent with the instance verdict.
    forced = (status == "ok_dropped_unknown")
    if forced:
        rules_viol += 1
    inst_viol = (rules_viol > 0)
    # per-family rows: attribute each gold rule's verdict to this instance's family.
    # (Families are per-instance in this benchmark; for finer per-rule-type analysis
    #  the cid carries the rule type, exposed in the byfamily file via reasons.)
    fam_rows = [(fam, (r["faithful"] is not False)) for r in report]
    if forced:
        fam_rows.append((fam, False))
    return dict(ok=True, status=status, k=k, fam=fam,
                inst_viol=inst_viol, rules_checked=len(report) + (1 if forced else 0),
                rules_viol=rules_viol, fam_rows=fam_rows)


def eval_once(records, model, caller, workers, use_mock, parse_fail_excludes):
    """One full pass over the split for one model (one generation per instance).
    Returns aggregate tallies for both instance-level and rule-level rates."""
    if use_mock:
        bound = caller
    else:
        bound = lambda prompt, rec: caller(prompt, rec, model)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    hard = [r for r in records if r.get("tier") in HARD_TIERS and r.get("static_checkable", True)]
    raws = [None] * len(hard)
    errs = [None] * len(hard)

    def task(i):
        raw, err = F.fetch_raw(hard[i], bound, model, neutral=False, dump_dir=None)
        return i, raw, err

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(task, i) for i in range(len(hard))]
        for fut in as_completed(futs):
            i, raw, err = fut.result()
            raws[i] = raw
            errs[i] = err

    # serial verify (z3 main thread)
    n_inst = n_inst_viol = 0
    n_rules = n_rules_viol = 0
    k_list = []
    fam_checked = defaultdict(int)
    fam_viol = defaultdict(int)
    for i, rec in enumerate(hard):
        s = score_raw_perrule(rec, raws[i], errs[i], parse_fail_excludes)
        if not s["ok"]:
            # excluded generation (parse_fail_excludes): does not enter rule denominator,
            # but we still count it as a violated instance for the instance-rate column.
            n_inst += 1
            n_inst_viol += 1 if s["inst_viol"] else 0
            continue
        n_inst += 1
        n_inst_viol += 1 if s["inst_viol"] else 0
        n_rules += s["rules_checked"]
        n_rules_viol += s["rules_viol"]
        k_list.append(s["k"])
        for fam, faithful in s["fam_rows"]:
            fam_checked[fam] += 1
            if not faithful:
                fam_viol[fam] += 1
    return dict(n_inst=n_inst, n_inst_viol=n_inst_viol,
                n_rules=n_rules, n_rules_viol=n_rules_viol,
                mean_k=(statistics.fmean(k_list) if k_list else float("nan")),
                fam_checked=dict(fam_checked), fam_viol=dict(fam_viol))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["claude-opus-4-7", "gpt-5.4", "deepseek-v3.2",
                             "claude-haiku-4-5-20251001", "qwen3-max", "gpt-4o-2024-11-20"])
    ap.add_argument("--splits", nargs="+",
                    default=["multi", "identification", "multivariate", "single"])
    ap.add_argument("--runs", type=int, default=5, help="generations per instance")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--mock", action="store_true", help="built-in mock LLM (no API key)")
    ap.add_argument("--parse-fail-excludes", action="store_true",
                    help="exclude unparseable/api-error generations from the per-rule denominator "
                         "(default: count all their gold rules as failed, the conservative choice)")
    ap.add_argument("--out-prefix", default="perrule")
    args = ap.parse_args()

    for s in args.splits:
        if s not in SPLIT_FILES:
            sys.exit(f"unknown split '{s}'; choose from {list(SPLIT_FILES)}")
        if not os.path.exists(SPLIT_FILES[s]):
            sys.exit(f"missing data file {SPLIT_FILES[s]} (run from the folder with the .jsonl files)")

    caller = make_caller(args.mock, args.temperature)

    rows = []        # per (model, split, run)
    summary = []     # per (model, split)
    fam_summary = [] # per (model, split, family)

    for split in args.splits:
        recs = load_split(SPLIT_FILES[split])
        for model in args.models:
            inst_rates, rule_rates, ks = [], [], []
            fam_acc_checked = defaultdict(int)
            fam_acc_viol = defaultdict(int)
            for run in range(1, args.runs + 1):
                a = eval_once(recs, model, caller, args.workers, args.mock, args.parse_fail_excludes)
                inst_rate = (100.0 * a["n_inst_viol"] / a["n_inst"]) if a["n_inst"] else float("nan")
                rule_rate = (100.0 * a["n_rules_viol"] / a["n_rules"]) if a["n_rules"] else float("nan")
                inst_rates.append(inst_rate)
                rule_rates.append(rule_rate)
                ks.append(a["mean_k"])
                for fam, c in a["fam_checked"].items():
                    fam_acc_checked[fam] += c
                for fam, v in a["fam_viol"].items():
                    fam_acc_viol[fam] += v
                rows.append({"model": model, "split": split, "run": run,
                             "n_instances": a["n_inst"], "n_inst_violated": a["n_inst_viol"],
                             "instance_rate_pct": round(inst_rate, 2),
                             "n_rules_checked": a["n_rules"], "n_rules_violated": a["n_rules_viol"],
                             "per_rule_rate_pct": round(rule_rate, 2),
                             "mean_rules_per_instance": round(a["mean_k"], 2),
                             "temperature": args.temperature})
                print(f"[{split:14s}] {model:26s} run {run}/{args.runs}: "
                      f"instance {inst_rate:5.1f}%  |  per-rule {rule_rate:5.1f}%  "
                      f"(k~{a['mean_k']:.1f}, {a['n_rules_viol']}/{a['n_rules']} rules)", flush=True)

            def ms(xs):
                m = statistics.fmean(xs)
                sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
                return round(m, 2), round(sd, 2)
            im, isd = ms(inst_rates)
            rm, rsd = ms(rule_rates)
            km, _ = ms(ks)
            summary.append({"model": model, "split": split, "runs": args.runs,
                            "instance_mean_pct": im, "instance_sd_pct": isd,
                            "per_rule_mean_pct": rm, "per_rule_sd_pct": rsd,
                            "mean_rules_per_instance": km,
                            "temperature": args.temperature})
            print(f"  -> {model} on {split}: instance {im} +/- {isd}%  |  "
                  f"per-rule {rm} +/- {rsd}%  (mean k={km})\n", flush=True)

            for fam in sorted(fam_acc_checked):
                c = fam_acc_checked[fam]; v = fam_acc_viol.get(fam, 0)
                fam_summary.append({"model": model, "split": split, "family": fam,
                                    "rules_checked_total": c, "rules_violated_total": v,
                                    "per_rule_rate_pct": round(100.0 * v / c, 2) if c else float("nan")})

    # ---- write CSVs ----
    with open(f"{args.out_prefix}_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    with open(f"{args.out_prefix}_summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys())); w.writeheader(); w.writerows(summary)
    if fam_summary:
        with open(f"{args.out_prefix}_byfamily.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fam_summary[0].keys())); w.writeheader(); w.writerows(fam_summary)

    # ---- human-readable table ----
    with open(f"{args.out_prefix}_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(f"FaithOpt per-rule vs instance-level violation  (runs={args.runs}, "
                 f"temperature={args.temperature}, Hard+Very-hard)\n")
        fh.write("mean +/- sd over independent generations. instance = any rule fails; "
                 "per-rule = fraction of gold rules unfaithful.\n\n")
        hdr = f"{'model':26s} {'split':14s} {'instance %':>16s} {'per-rule %':>16s} {'mean k':>8s}\n"
        fh.write(hdr); fh.write("-" * len(hdr) + "\n")
        for r in summary:
            fh.write(f"{r['model']:26s} {r['split']:14s} "
                     f"{r['instance_mean_pct']:6.1f} +/- {r['instance_sd_pct']:<5.1f} "
                     f"{r['per_rule_mean_pct']:6.1f} +/- {r['per_rule_sd_pct']:<5.1f} "
                     f"{r['mean_rules_per_instance']:8.1f}\n")
    print(f"Wrote {args.out_prefix}_runs.csv, {args.out_prefix}_summary.csv, "
          f"{args.out_prefix}_byfamily.csv, {args.out_prefix}_summary.txt")


if __name__ == "__main__":
    main()
