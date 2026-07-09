#!/usr/bin/env bash
# ======================================================================
# FaithOpt — one-command reproduction
#
#   bash reproduce.sh            # full pipeline (needs API key, see below)
#   NO_LLM=1 bash reproduce.sh   # verifier + benchmark + GT + pure-z3 results (no API)
#
# API: experiments use an OpenAI-compatible endpoint. Set:
#   export GLOBALAI_KEY=sk-...          # your key
# (call_real_llm in formulator.py reads base_url + this key from the env.)
# ======================================================================
set -euo pipefail

MODELS="claude-opus-4-7 gpt-5.4 qwen3-max gpt-4o-2024-11-20 deepseek-v3.2 claude-haiku-4-5-20251001"
LOOP_MODELS="gpt-5.4 deepseek-v3.2 claude-haiku-4-5-20251001"
HIGHDIM_MODELS="gpt-5.4 gpt-4o-2024-11-20 claude-haiku-4-5-20251001"
SPLITS="single multi identification multivariate"
WORKERS=10
ROUNDS=3
RUNS=5

echo "############################################################"
echo "# 0. Environment check"
echo "############################################################"
python3 -c "import z3, openpyxl; print('z3 + openpyxl OK')"

echo
echo "############################################################"
echo "# 1. (Re)generate all splits + verify ground truth"
echo "############################################################"
python3 generate_tier2.py
python3 generate_tier3.py
python3 generate_tier4_multivar.py
python3 gen_overdetermined.py            # append over-determined conflicts to multivariate
python3 generate_highdim.py              # high-dimensional stress split (k=5,8,10)

echo
echo "## 1a. Dataset integrity (counts, self-consistency, feasibility-vs-label)"
python3 - <<'PY'
import json, z3, faithopt_verifier as V
def mk(r):
    d=[V.Var(v["name"],v["kind"],lb=v["lb"],ub=v["ub"]) for v in r["var_decls"]]
    g=[V.LinCon(c["id"],{k:float(x) for k,x in c["terms"].items()},c["sense"],float(c["rhs"])) for c in r["gold_constraints"]]
    return d,g
def infeas(r,cons):
    s=z3.Solver(); zv={v["name"]:z3.Real(v["name"]) for v in r["var_decls"]}
    for c in cons:
        lhs=z3.Sum([co*zv[n] for n,co in c.terms.items()])
        s.add(lhs<=c.rhs if c.sense=="<=" else (lhs>=c.rhs if c.sense==">=" else lhs==c.rhs))
    return s.check()!=z3.sat
ok=True
for f in ["FaithConstraint-OR_single","FaithConstraint-OR_multi","FaithConstraint-OR_identification","FaithConstraint-OR_multivariate"]:
    recs=[r for r in (json.loads(l) for l in open(f+".jsonl",encoding="utf-8") if l.strip()) if not r.get("_manifest")]
    sf=lm=0
    for r in recs:
        d,g=mk(r)
        if g and any(not x["faithful"] for x in V.audit(d,g,g)): sf+=1
        is_conf=("empty-set" in r.get("note","")) or ("EMPTY" in r.get("gold_readable","")) or ("conflict" in r.get("constraint_family",""))
        if g and is_conf!=infeas(r,g): lm+=1
    flag="" if (sf==0 and lm==0) else "  <-- PROBLEM"
    print(f"  {f}: n={len(recs)} self_fail={sf} label_mismatch={lm}{flag}")
    ok = ok and sf==0 and lm==0
print("  integrity:", "PASS" if ok else "FAIL")
PY

echo
echo "## 1b. Theory backing (Path A: entailment-only incompleteness vs coverage)"
python3 verify_theory.py | sed -n '/PATH A/,/RESULT/p'

echo
echo "## 1c. Coverage ablation -- controlled (the 1,373 / 1,115 / 395 / 158 result). Pure z3, no API."
python3 coverage_ablation.py

echo
echo "## 1d. Exact (IIS, Prop 2) vs single-witness coverage -- controlled. Pure z3, no API."
python3 exact_coverage_ablation.py

echo
echo "## 1e. Verifier scaling benchmark (audit cost vs problem size). Pure z3, no API."
python3 scaling_bench.py

if [ "${NO_LLM:-0}" = "1" ]; then
  echo
  echo "NO_LLM=1 set -> skipping all API experiments (steps 2+)."
  echo "Verifier, benchmark, GT checks, both coverage ablations and the scaling"
  echo "benchmark above are complete and need no API."
  exit 0
fi

echo
echo "############################################################"
echo "# 2. MAIN results table: multi-generation bare-LLM (mean +/- sd)"
echo "#    all models x all splits  ->  paper Table 'bare'"
echo "############################################################"
python3 run_multigen.py --runs $RUNS --temperature 0 --workers $WORKERS --models $MODELS --splits $SPLITS

echo
echo "############################################################"
echo "# 2b. Per-rule vs per-instance breakdown  ->  paper Table 'per-rule'"
echo "############################################################"
python3 run_perrule.py --runs $RUNS --temperature 0 --workers $WORKERS --models $MODELS --splits $SPLITS

echo
echo "############################################################"
echo "# 3. Single-generation detail logs (needed by analyze_results for the"
echo "#    neutral-vs-full and status tables, and by exact_vs_relax_realdata)"
echo "############################################################"
for s in $SPLITS; do
  echo "## detail: split=$s"
  python3 formulator.py --model $MODELS --data FaithConstraint-OR_${s}.jsonl --workers $WORKERS
done
echo "## detail: high-dimensional stress split (paper Section on dimension)"
python3 formulator.py --model $HIGHDIM_MODELS --data FaithConstraint-OR_highdim.jsonl --workers $WORKERS

echo
echo "############################################################"
echo "# 4. Neutral-prompt ablation (identification split)  ->  paper Table 'neutral'"
echo "############################################################"
python3 formulator.py --model $LOOP_MODELS --data FaithConstraint-OR_identification.jsonl --neutral-prompt --workers $WORKERS

echo
echo "############################################################"
echo "# 5. Verify-repair loop: all models, multivariate + identification"
echo "#    ->  paper repair tables"
echo "############################################################"
python3 faithopt_loop.py --model $MODELS --data FaithConstraint-OR_multivariate.jsonl --max-rounds $ROUNDS --workers $WORKERS
python3 faithopt_loop.py --model $MODELS --data FaithConstraint-OR_identification.jsonl --max-rounds $ROUNDS --workers $WORKERS

echo
echo "## 5b. Coverage end-to-end: loop with vs without coverage, then compare  ->  paper Section 6.5"
python3 faithopt_loop.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_identification.jsonl --max-rounds $ROUNDS --workers $WORKERS --no-coverage
python3 compare_coverage_loop.py \
    runs/gpt-4o-2024-11-20__identification_faithopt_loop.txt \
    runs/gpt-4o-2024-11-20__identification_nocov_faithopt_loop.txt

echo
echo "############################################################"
echo "# 6. Exact (IIS) vs relaxation coverage on REAL LLM outputs  ->  paper Section 3.3"
echo "############################################################"
python3 exact_vs_relax_realdata.py \
    --detail runs/gpt-4o-2024-11-20__identification_detail.txt \
    --data FaithConstraint-OR_identification.jsonl

echo
echo "############################################################"
echo "# 7. LLM-as-judge baseline (judge LLM vs the formal verifier)  ->  paper Section 3.3"
echo "############################################################"
python3 llm_judge_baseline.py \
    --gen-model gpt-4o-2024-11-20 --judge-model gpt-5.4 \
    --data FaithConstraint-OR_identification.jsonl --workers $WORKERS

echo
echo "############################################################"
echo "# 8. Single-run tables (bare Wilson CIs, neutral-vs-full, loop, status)"
echo "############################################################"
python3 analyze_results.py --runs runs

echo
echo "Done."
echo "  Main table        -> run_multigen output (step 2)"
echo "  Per-rule table    -> run_perrule output (step 2b)"
echo "  Repair / neutral / status tables -> analyze_results (step 8)"
echo "  Coverage ablation + exact-vs-relax + scaling -> steps 1c-1e, 5b, 6"
echo "  LLM-judge baseline -> step 7"
