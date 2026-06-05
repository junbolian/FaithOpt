#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
per_rule_analysis.py  --  per-RULE violation rates for FaithConstraint-OR.

Why this exists
---------------
The main tables report an INSTANCE-level violation indicator: an instance counts as
violated if ANY of its gold rules is unfaithfully encoded. On very-hard instances that
carry up to k=8 rules, even a modest per-rule error compounds into a large instance-level
rate (eight rules at 5% each already exceeds 30%). A reviewer correctly asked us to
separate "instances are hard because they pack many rules" from "the model is poor at
encoding any single rule." This script answers that by scoring at the level of the
individual gold constraint.

For each (model, split, instance, generation) it asks the model for a formulation M,
runs the SAME verifier audit used everywhere else, and records, FOR EACH GOLD RULE c,
whether c was faithfully encoded. It then reports:

  - overall per-rule violation rate           (rule-instances violated / rule-instances total)
  - per-rule rate broken down by rule family   (e.g. winning-bid cap, gross-margin floor, ...)
  - per-rule rate broken down by tier          (Easy / Medium / Hard / Very hard)
  - for context, the instance-level rate on the same runs (so the two can be compared)

It reuses formulator.py for generation/parsing and faithopt_verifier.audit for scoring,
so the numbers are consistent with the rest of the pipeline. Multiple generations per
instance (default 5, temperature 0) are supported, exactly as in run_multigen.py.

Usage
-----
  export GLOBALAI_KEY=sk-...
  python per_rule_analysis.py --runs 5 --temperature 0 --workers 10 \
      --models claude-opus-4-7 gpt-5.4 deepseek-v3.2 claude-haiku-4-5-20251001 qwen3-max gpt-4o-2024-11-20 \
      --splits multi identification multivariate single

  # validate the pipeline with no API key:
  python per_rule_analysis.py --mock --runs 2 --splits multivariate

Outputs (written to CWD)
  per_rule_runs.csv        one row per (model, split, run, gold-rule-instance): faithful 0/1
  per_rule_summary.csv     aggregated mean +/- sd per (model, split) at the RULE level
  per_rule_by_family.csv   per (model, split, family): rule-level rate
  per_rule_summary.txt     human-readable table you can paste back
"""
import os, sys, csv, json, argparse, statistics
from concurrent.futures import ThreadPoolExecutor

import formulator as F                      # reuse the bare-LLM harness
from faithopt_verifier import audit, Var, LinCon  # noqa: F401  (audit used below)

# ----------------------------------------------------------------------
SPLIT_FILE = {
    "single":        "FaithConstraint-OR_single.jsonl",
    "multi":         "FaithConstraint-OR_multi.jsonl",
    "identification":"FaithConstraint-OR_identification.jsonl",
    "multivariate":  "FaithConstraint-OR_multivariate.jsonl",
}
HARD_TIERS = {"Hard", "Very hard"}

def load_split(split):
    path = SPLIT_FILE[split]
    recs = [json.loads(l) for l in open(path, encoding="utf-8")
            if l.strip() and not json.loads(l).get("_manifest")]
    # keep statically-checkable instances; mirror formulator's filter
    return [r for r in recs if r.get("static_checkable", True)]

def gold_family(rec, cid):
    """Best-effort family label for a single gold rule.

    The benchmark stores a per-instance constraint_family; for multi-rule instances we
    additionally try to read a per-rule family from the gold entry if present, else fall
    back to the rule id prefix (e.g. 'vbp_cap' -> 'vbp_cap'), else the instance family.
    """
    for g in rec.get("gold_constraints", []):
        if str(g.get("id")) == str(cid):
            if g.get("family"):
                return g["family"]
            # id often encodes the family, e.g. 'margin_flr', 'vbp_cap'
            return str(g.get("id"))
    return rec.get("constraint_family", "unknown")

# ----------------------------------------------------------------------
def make_caller(model, mock, temperature=0.0):
    """Return a caller(prompt, rec) -> raw_text.

    Real path reuses formulator.call_real_llm(model, prompt) so the endpoint, headers,
    and parsing are identical to the rest of the pipeline. (call_real_llm pins
    temperature=0; if you need temperature>0, set it there or extend that function.)
    """
    if mock:
        return lambda prompt, rec: F.mock_llm(prompt, rec)
    def _call(prompt, rec, _model=model):
        return F.call_real_llm(_model, prompt)
    return _call

# ----------------------------------------------------------------------
def per_rule_outcomes_for_run(recs, model, caller, temperature, workers, neutral=False):
    """One generation pass over the split. Returns:
       rule_rows: list of dicts (id, tier, family, cid, faithful 0/1, status)
       inst_rows: list of dicts (id, tier, violated 0/1)  -- for instance-level context
    """
    # 1) network-bound generation in threads
    bound = lambda prompt, rec: caller(prompt, rec)
    raws = [None]*len(recs); errs=[None]*len(recs)
    def fetch(i):
        raw, err = F.fetch_raw(recs[i], bound, model, neutral=neutral, dump_dir=None)
        return i, raw, err
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, raw, err in ex.map(fetch, range(len(recs))):
            raws[i]=raw; errs[i]=err

    # 2) z3 scoring in the MAIN thread (z3 is not thread-safe)
    rule_rows=[]; inst_rows=[]
    for i, rec in enumerate(recs):
        decls, gold = F.gold_of(rec)
        api_err = errs[i]
        if api_err:
            # an API failure means we could not obtain a formulation; record every gold
            # rule for this instance as violated (consistent with score_raw treating
            # api_error as a failed instance), and the instance as violated.
            for g in gold:
                rule_rows.append({"id":rec["id"], "tier":rec["tier"],
                                  "family":gold_family(rec, g.cid), "cid":g.cid,
                                  "faithful":0, "status":"api_error"})
            inst_rows.append({"id":rec["id"], "tier":rec["tier"], "violated":1})
            continue
        M, status = F.parse_model(raws[i], rec)
        if status in ("format_error","empty_after_clean","symbolic_unresolved"):
            for g in gold:
                rule_rows.append({"id":rec["id"], "tier":rec["tier"],
                                  "family":gold_family(rec, g.cid), "cid":g.cid,
                                  "faithful":0, "status":status})
            inst_rows.append({"id":rec["id"], "tier":rec["tier"], "violated":1})
            continue
        try:
            report = audit(decls, M, gold)
        except Exception as e:
            for g in gold:
                rule_rows.append({"id":rec["id"], "tier":rec["tier"],
                                  "family":gold_family(rec, g.cid), "cid":g.cid,
                                  "faithful":0, "status":f"verify_error:{e}"})
            inst_rows.append({"id":rec["id"], "tier":rec["tier"], "violated":1})
            continue
        any_viol = False
        for r in report:
            faithful = 1 if r["faithful"] else 0
            if not faithful: any_viol = True
            rule_rows.append({"id":rec["id"], "tier":rec["tier"],
                              "family":gold_family(rec, r["cid"]), "cid":r["cid"],
                              "faithful":faithful, "status":r.get("reason","")})
        # forced-failure case (model dropped a rule under unknown status)
        if status == "ok_dropped_unknown":
            any_viol = True
        inst_rows.append({"id":rec["id"], "tier":rec["tier"], "violated":1 if any_viol else 0})
    return rule_rows, inst_rows

# ----------------------------------------------------------------------
def rate(rows, key_faithful="faithful"):
    """violation rate (%) = 1 - mean(faithful)."""
    if not rows: return float("nan")
    f = sum(r[key_faithful] for r in rows)/len(rows)
    return 100.0*(1.0-f)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["claude-opus-4-7","gpt-5.4","deepseek-v3.2",
                             "claude-haiku-4-5-20251001","qwen3-max","gpt-4o-2024-11-20"])
    ap.add_argument("--splits", nargs="+",
                    default=["multi","identification","multivariate","single"])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--hard-only", action="store_true", default=True,
                    help="restrict to Hard+Very-hard tiers (matches the main tables)")
    ap.add_argument("--all-tiers", dest="hard_only", action="store_false")
    ap.add_argument("--mock", action="store_true")
    args=ap.parse_args()

    runs_fp   = open("per_rule_runs.csv","w",newline="",encoding="utf-8")
    runs_w    = csv.writer(runs_fp)
    runs_w.writerow(["model","split","run","id","tier","family","cid","faithful","status"])

    summary={}   # (model,split) -> {"rule":[per-run rule rate], "inst":[per-run inst rate]}
    by_family={} # (model,split,family) -> [faithful flags pooled across runs]

    for split in args.splits:
        recs_all = load_split(split)
        recs = [r for r in recs_all if (r["tier"] in HARD_TIERS)] if args.hard_only else recs_all
        if not recs:
            print(f"[skip] {split}: no instances after tier filter"); continue
        for model in args.models:
            caller = make_caller(model, args.mock, args.temperature)
            rule_rate_runs=[]; inst_rate_runs=[]
            for run in range(1, args.runs+1):
                rule_rows, inst_rows = per_rule_outcomes_for_run(
                    recs, model, caller, args.temperature, args.workers)
                rr = rate(rule_rows); ir = rate(inst_rows, key_faithful="violated")
                # note: inst_rows store 'violated' not 'faithful'; convert:
                ir = 100.0*sum(x["violated"] for x in inst_rows)/len(inst_rows)
                rule_rate_runs.append(rr); inst_rate_runs.append(ir)
                for row in rule_rows:
                    runs_w.writerow([model,split,run,row["id"],row["tier"],
                                     row["family"],row["cid"],row["faithful"],row["status"]])
                    by_family.setdefault((model,split,row["family"]),[]).append(row["faithful"])
                print(f"[{split:14s}] {model} run {run}/{args.runs}: "
                      f"per-rule {rr:5.1f}%   instance {ir:5.1f}%   "
                      f"({len(rule_rows)} rule-instances)")
            summary[(model,split)]={"rule":rule_rate_runs,"inst":inst_rate_runs}
            mr=statistics.mean(rule_rate_runs)
            sr=statistics.pstdev(rule_rate_runs) if len(rule_rate_runs)>1 else 0.0
            mi=statistics.mean(inst_rate_runs)
            print(f"  -> {model} on {split}: per-rule {mr:.1f} +/- {sr:.1f}  "
                  f"| instance {mi:.1f}\n")
    runs_fp.close()

    # ---- summary csv + txt ----
    with open("per_rule_summary.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["model","split","per_rule_mean","per_rule_sd",
                                     "instance_mean","n_runs"])
        for (model,split),d in summary.items():
            r=d["rule"]; i=d["inst"]
            w.writerow([model,split,
                        round(statistics.mean(r),2),
                        round(statistics.pstdev(r) if len(r)>1 else 0.0,2),
                        round(statistics.mean(i),2), len(r)])
    with open("per_rule_by_family.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["model","split","family","per_rule_pct","n_rule_instances"])
        for (model,split,fam),flags in sorted(by_family.items()):
            vr=100.0*(1.0-sum(flags)/len(flags))
            w.writerow([model,split,fam,round(vr,2),len(flags)])

    with open("per_rule_summary.txt","w",encoding="utf-8") as f:
        tier_note = "Hard+Very-hard" if args.hard_only else "all tiers"
        f.write(f"FaithConstraint-OR per-RULE vs INSTANCE violation rate "
                f"(runs={args.runs}, temperature={args.temperature}, {tier_note})\n")
        f.write("per-rule = fraction of individual gold rules unfaithfully encoded; "
                "instance = 'any rule fails'.\n\n")
        f.write(f"{'model':30s}{'split':16s}{'per-rule (mean+/-sd)':24s}{'instance (mean)':16s}\n")
        f.write("-"*86+"\n")
        for (model,split),d in summary.items():
            r=d["rule"]; i=d["inst"]
            mr=statistics.mean(r); sr=statistics.pstdev(r) if len(r)>1 else 0.0
            f.write(f"{model:30s}{split:16s}{mr:6.1f} +/- {sr:<10.1f}{statistics.mean(i):6.1f}\n")
    print("Wrote per_rule_runs.csv, per_rule_summary.csv, per_rule_by_family.csv, per_rule_summary.txt")

if __name__=="__main__":
    main()
