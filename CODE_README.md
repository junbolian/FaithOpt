# FaithOpt — Code & Reproducibility

Code release for FaithOpt: a provably sound SMT verifier for the *faithfulness* of
LLM-generated optimization models to regulatory constraints, the **FaithConstraint-OR**
benchmark, and a verify–repair loop. This file covers installation, reproduction, and usage;
see `README.md` for the project overview.

## Install
```
pip install -r requirements.txt
```
`z3-solver` is enough for the verifier and benchmark generation. `openai` is only needed to run
the LLM experiments; `openpyxl` is optional (human-readable spreadsheet export).

## Reproduce
```
# verifier + benchmark + ground-truth checks + theory backing (NO API needed):
NO_LLM=1 bash reproduce.sh

# full pipeline incl. all LLM experiments (needs an OpenAI-compatible endpoint):
export GLOBALAI_KEY=sk-...                 # your API key
export LLM_BASE_URL=https://...            # optional; defaults to the bundled endpoint
bash reproduce.sh
```
`reproduce.sh` regenerates and validates the benchmark, runs the Path-A theory check, runs the
bare-LLM measurement (all models × all splits), the neutral-prompt ablation, and the
verify–repair loop, then prints the result tables via `analyze_results.py`.

## Files

### Core (the system)
| File | Role |
|------|------|
| `faithopt_verifier.py` | Verifier core: `Var`, `LinCon`, `entails`, `covers`, `audit`. Pure SMT (z3); the module header states the formal faithfulness definition. |
| `formulator.py` | Bare-LLM harness: prompt → LLM → parse → `audit`; violation rate by tier × family. Per-model `runs/<model>__<split>_detail.txt` logs the model's encoded constraints + fail reasons. Flags: `--model`, `--data`, `--workers`, `--neutral-prompt`, `--mock`. |
| `run_multigen.py` | Multi-generation harness: runs the bare-LLM measurement R times per instance (default 5) and reports **mean ± sd** of the per-split violation rate across generations, capturing provider-side nondeterminism. Writes `multigen_runs.csv`, `multigen_summary.csv`, `multigen_summary.txt`. Flags: `--models`, `--splits`, `--runs`, `--temperature`, `--workers`, `--mock`. This produces the numbers in the main results table. |
| `per_rule_analysis.py` | Same generation pipeline, but scores at the level of the individual gold rule: reports **per-rule** vs **per-instance** violation rate (mean ± sd) and a per-rule-family breakdown, to separate "instances pack many rules" from "poor per-rule fidelity." Writes `per_rule_runs.csv`, `per_rule_summary.csv`, `per_rule_by_family.csv`, `per_rule_summary.txt`. Same flags as `run_multigen.py`. |
| `run_perrule.py` | Per-rule vs per-instance harness used for the paper's per-rule table: like `per_rule_analysis.py` with conflict/parse-fail handling refinements; writes `perrule_runs.csv`, `perrule_summary.csv`, `perrule_by_family.csv`, `perrule_summary.txt`. Same flags as `run_multigen.py`. |
| `faithopt_loop.py` | Verify–repair loop: counterexample + missing-count feedback (no gold leakage) → re-query → re-audit, up to `--max-rounds`. Logs each round's raw output. `--no-coverage` ablates the coverage condition (soundness-only verdict) and writes to a `*_nocov_*` detail file. |

### Benchmark generation + checking
| File | Role |
|------|------|
| `generate_tier2.py` / `generate_tier3.py` / `generate_tier4_multivar.py` | Procedural generators for the `multi`, `identification`, and `multivariate` splits; ground truth is mechanical and z3-verified. Scale a split by raising the loop counts. |
| `generate_highdim.py` | Procedural generator for the high-dimensional stress split (`FaithConstraint-OR_highdim.jsonl`): k=5,8,10 coupled prices with procedural-noise distractors and over-determined conflicts; ground truth z3-verified. |
| `gen_overdetermined.py` | Appends over-determined-conflict instances to the `multivariate` split (the empty-set-loophole probe). |
| `verify_theory.py` | Empirical backing for the theory: Path A (entailment-only is incomplete; coverage closes it) and Path B (failure-status decomposition for the repairability account). |
| `analyze_results.py` | Computes single-run result tables (bare-LLM with Wilson CIs, neutral-vs-full, loop bare→repaired with round distribution, status decomposition) from `runs/`. For the multi-generation main table use `run_multigen.py`. |
| `scaling_bench.py` | Verifier scaling benchmark: times `audit()` versus problem size (variables × constraints, real/int). **Pure z3, no API.** Writes `scaling_runs.csv`, `scaling_summary.txt`. |
| `coverage_ablation.py` | Controlled coverage ablation: on the conflicting instances, drops each gold rule and audits soundness-only vs soundness+coverage, counting how many silent drops coverage flips from accept→flag. **Pure z3, no API.** |
| `exact_coverage_ablation.py` | Controlled comparison of the single-witness coverage *relaxation* vs the *exact* IIS-based coverage test (Proposition 2) on synthetic distributed-encoding infeasible instances. **Pure z3, no API.** |
| `exact_vs_relax_realdata.py` | Applies the relaxation-vs-exact coverage comparison to the real LLM-formulated models in `runs/` detail logs, reporting when (and on which instances) the two diverge. |
| `compare_coverage_loop.py` | Compares two `faithopt_loop.py` detail files (with-coverage vs `--no-coverage`) and reports the per-instance verdicts coverage changes — flagged/repaired with it, shipped silently without. |
| `llm_judge_baseline.py` | LLM-as-judge baseline: a generator LLM produces M, the formal `audit` is taken as ground truth, and a judge LLM verdicts the same M; reports judge miss-rate, false-alarm rate, and agreement vs the verifier. |

### Data (`FaithConstraint-OR`, four splits — evaluate separately, do **not** merge)
| File | n | What it tests |
|------|---|---------------|
| `FaithConstraint-OR_single.jsonl` | 44 | Baseline: constraints stated plainly (no identification challenge), mostly single-variable; one to three gold constraints per instance (mean about 1.8). Includes 3 intentional out-of-scope marker instances with empty gold — exclude from main violation stats. |
| `FaithConstraint-OR_multi.jsonl` | 150 | Multiple pre-extracted constraints across documents; encode all. |
| `FaithConstraint-OR_identification.jsonl` | 150 | A numbered policy list mixing constraints with procedural/definitional noise; identify which items are constraints, then encode all. |
| `FaithConstraint-OR_multivariate.jsonl` | 174 | Two prices p1, p2 with cross-variable constraints (combined budget, weighted basket, relative pricing, aggregate margin), plus over-determined conflicts. |
| `FaithConstraint-OR_highdim.jsonl` | 60 | High-dimensional stress test: k=5,8,10 coupled prices with procedural-noise distractors and over-determined conflicts (13 conflict / 47 clean); probes whether dimension itself drives failure. |

Each `.jsonl` begins with a `_manifest` line carrying the split's identity; readers skip it.
See `DATA_CARD.md` for full per-split metadata. Ground truth is mechanically generated and
z3-verified — never produced by an LLM. The constraint **structures and scenarios** are drawn from
real regulated pharmacy operations (pricing, reimbursement, procurement, assortment); numeric
values are abstracted from realistic ranges for commercial confidentiality and to prevent any
instance being solved from memorized data.

## Using the verifier on your own model
```python
import faithopt_verifier as V
decls = [V.Var("p1","real",lb=0,ub=100), V.Var("p2","real",lb=0,ub=100)]
gold  = [V.LinCon("budget", {"p1":1.0,"p2":1.0}, "<=", 30.0),
         V.LinCon("rel",    {"p1":1.0,"p2":-1.0}, "<=", 0.0)]   # p1 <= p2
model = [V.LinCon("m1", {"p1":1.0,"p2":1.0}, "<=", 30.0)]       # dropped the relative rule
for r in V.audit(decls, model, gold):
    print(r["cid"], r["faithful"], r["reason"])   # 'rel' -> False, violated (M admits p1=30,p2=0)
```

## Dataset schema (one JSON object per line; first line is `_manifest`)
- `id`, `tier` (Easy/Medium/Hard/Very hard), `constraint_family`, `scope`
- `reg_text` (the natural-language policy), `scenario`
- `var_decls`: list of `{name, kind, lb, ub}`
- `gold_constraints`: list of `{id, terms:{var:coef}, sense:"<="|">="|"==", rhs}`
- `gold_readable`, `note` (e.g. conflict/empty-set), `static_checkable`

## Run individual steps
```
# bare-LLM on one split (single generation):
python formulator.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_multivariate.jsonl --workers 10
# multi-generation main measurement (5 runs/instance -> mean +/- sd):
python run_multigen.py --runs 5 --temperature 0 --workers 10 \
    --models claude-opus-4-7 gpt-5.4-2026-03-05 deepseek-v3.2 claude-haiku-4-5-20251001 qwen3-max gpt-4o-2024-11-20 \
    --splits multi identification multivariate single
# per-rule vs per-instance breakdown (same models/splits):
python run_perrule.py --runs 5 --temperature 0 --workers 8 \
    --models claude-opus-4-7 gpt-5.4-2026-03-05 deepseek-v3.2 claude-haiku-4-5-20251001 qwen3-max gpt-4o-2024-11-20 \
    --splits multi identification multivariate single
# verify-repair loop:
python faithopt_loop.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_multivariate.jsonl --max-rounds 3 --workers 10
# coverage ablation -- controlled, no API (the 1,373 / 1,115 / 395 / 158 result):
python coverage_ablation.py
# coverage ablation -- end-to-end (loop with vs without coverage, then compare):
python faithopt_loop.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_identification.jsonl --max-rounds 3 --workers 10
python faithopt_loop.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_identification.jsonl --max-rounds 3 --workers 10 --no-coverage
python compare_coverage_loop.py runs/gpt-4o-2024-11-20__identification_faithopt_loop.txt runs/gpt-4o-2024-11-20__identification_nocov_faithopt_loop.txt
# result tables:
python analyze_results.py --runs runs
# verifier scaling benchmark (no API):
python scaling_bench.py
```
Run with `--mock` (no API key) to validate the pipeline end-to-end.