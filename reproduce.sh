#!/usr/bin/env bash
# ======================================================================
# FaithOpt — one-command reproduction
#
#   bash reproduce.sh            # full pipeline (needs API key, see below)
#   NO_LLM=1 bash reproduce.sh   # verifier + benchmark + GT checks only (no API)
#
# API: experiments use an OpenAI-compatible endpoint. Set:
#   export GLOBALAI_KEY=sk-...          # your key
# (call_real_llm in formulator.py reads base_url + this key from the env.)
#
# All steps are idempotent. Logs land in ./runs ; final numbers are printed
# by analyze_results.py and map onto the paper's placeholders.
# ======================================================================
set -euo pipefail

MODELS="claude-opus-4-7 gpt-5.4 qwen3-max gpt-4o-2024-11-20 deepseek-v3.2 claude-haiku-4-5-20251001"
LOOP_MODELS="gpt-5.4 deepseek-v3.2 claude-haiku-4-5-20251001"
SPLITS="single multi identification multivariate"
WORKERS=10
ROUNDS=3

echo "############################################################"
echo "# 0. Environment check"
echo "############################################################"
python3 -c "import z3, openpyxl; print('z3 + openpyxl OK')"

echo
echo "############################################################"
echo "# 1. (Re)generate the procedural splits + verify ground truth"
echo "############################################################"
python3 generate_tier2.py
python3 generate_tier3.py
python3 generate_tier4_multivar.py
python3 gen_overdetermined.py        # append over-determined conflicts to multivariate

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

if [ "${NO_LLM:-0}" = "1" ]; then
  echo
  echo "NO_LLM=1 set -> skipping all API experiments (steps 2-4)."
  echo "Verifier, benchmark, and GT checks above are complete and need no API."
  exit 0
fi

echo
echo "############################################################"
echo "# 2. Bare-LLM measurement: all models x all splits"
echo "############################################################"
for s in $SPLITS; do
  echo "## bare: split=$s"
  python3 formulator.py --model $MODELS --data FaithConstraint-OR_${s}.jsonl --workers $WORKERS
done

echo
echo "############################################################"
echo "# 3. Neutral-prompt ablation (subset of models, on identification split)"
echo "############################################################"
python3 formulator.py --model $LOOP_MODELS --data FaithConstraint-OR_identification.jsonl --neutral-prompt --workers $WORKERS

echo
echo "############################################################"
echo "# 4. Verify-repair loop: representative models, tier3 + tier4"
echo "############################################################"
python3 faithopt_loop.py --model $LOOP_MODELS --data FaithConstraint-OR_multivariate.jsonl --max-rounds $ROUNDS --workers $WORKERS
python3 faithopt_loop.py --model gpt-5.4 deepseek-v3.2 --data FaithConstraint-OR_identification.jsonl --max-rounds $ROUNDS --workers $WORKERS

echo
echo "############################################################"
echo "# 5. Compute paper tables (fill the \\todo placeholders from this)"
echo "############################################################"
python3 analyze_results.py --runs runs

echo
echo "Done. Numbers above map onto sections 4-6; see FaithOpt_fill_in_checklist.md."
