# -*- coding: utf-8 -*-
"""exact_vs_relax_realdata.py -- run Proposition 2 on REAL LLM outputs.

Parses a formulator detail log (the model's `model_encoded` constraints per instance),
pulls gold + variable bounds from the matching benchmark .jsonl, and -- on every LLM
model that is INFEASIBLE (the only regime where coverage is invoked) -- compares the
single-witness coverage RELAXATION against the exact feasible-subset criterion of
Proposition 2, rule by rule. Reports how many genuine coverages the relaxation
false-flags (and on which instances) that the exact test accepts. Pure z3, no API.

  python exact_vs_relax_realdata.py \
      --detail runs/gpt-4o-2024-11-20__multivariate_detail.txt \
      --data   FaithConstraint-OR_multivariate.jsonl
"""
import json, argparse, re, ast
from exact_coverage_ablation import model_feasible, covers_relax, covers_exact

def load_gold_bounds(jsonl):
    by_id = {}
    for l in open(jsonl, encoding="utf-8"):
        if not l.strip():
            continue
        o = json.loads(l)
        if o.get("_manifest"):
            continue
        decls = {d["name"]: (d.get("lb"), d.get("ub")) for d in o["var_decls"]}
        gold = [(g["terms"], g["sense"], g["rhs"]) for g in o["gold_constraints"]]
        by_id[o["id"]] = {"decls": decls, "gold": gold}
    return by_id

def parse_detail(path):
    by_id, cur = {}, None
    for line in open(path, encoding="utf-8", errors="replace").read().splitlines():
        line = line.rstrip()
        m = re.match(r"\[([^\]]+)\]\s+tier=", line)
        if m:
            cur = m.group(1); continue
        m = re.search(r"model_encoded\s*\(\d+\):\s*(\[.*\])\s*$", line)
        if m and cur:
            raw = ast.literal_eval(m.group(1))          # list of (sense, terms, rhs)
            by_id[cur] = [(t, se, r) for (se, t, r) in raw]   # -> (terms, sense, rhs)
            cur = None
    return by_id

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", required=True)
    ap.add_argument("--data", required=True, help="matching benchmark .jsonl (for gold + bounds)")
    args = ap.parse_args()

    gb = load_gold_bounds(args.data)
    enc = parse_detail(args.detail)
    ids = [i for i in enc if i in gb]

    n_infeas = fp_rules = total_rules = 0
    div = []
    for i in ids:
        decls, gold, M = gb[i]["decls"], gb[i]["gold"], enc[i]
        if not M or model_feasible(decls, M):    # coverage only invoked on infeasible M
            continue
        n_infeas += 1
        inst_fp = 0
        for c in gold:
            total_rules += 1
            rl = covers_relax(decls, M, c)        # single-witness: covered?
            ex = covers_exact(decls, M, c)        # Proposition 2: covered?
            if (not rl) and ex:                   # relaxation flags not_covered, but it IS covered
                fp_rules += 1; inst_fp += 1
        if inst_fp:
            div.append((i, inst_fp))

    print(f"detail: {args.detail}")
    print(f"  instances matched to gold:                 {len(ids)}")
    print(f"  infeasible LLM models (coverage invoked):  {n_infeas}")
    print(f"  gold rules checked on infeasible models:   {total_rules}")
    print(f"  relaxation FALSE POSITIVES (flag not_covered, exact says covered): {fp_rules}")
    if div:
        print(f"  instances where exact fixes a relaxation false-flag ({len(div)}):")
        for i, k in div:
            print(f"    {i}: {k} rule(s)")
    else:
        print("  => NO divergence on these real outputs: the single-witness relaxation")
        print("     never false-alarms here; it agrees with the exact Prop-2 test everywhere,")
        print("     i.e. the LLM did not produce an infeasible model with a distributed encoding.")
