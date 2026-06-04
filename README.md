# FaithOpt: Verifying the faithfulness of LLM-generated optimization models to regulatory constraints.

FaithOpt checks whether an optimization model produced by a large language model is *provably
faithful* to the hard constraints stated in regulatory or policy text — without any reference
answer. It pairs an SMT-based verifier (z3) with a benchmark, **FaithConstraint-OR**, and a
verify–repair loop.

> Companion code for the paper *"FaithOpt: Verifying the Faithfulness of LLM-Generated
> Optimization Models to Regulatory Constraints"*
## The problem

When an LLM turns a manager's request plus regulatory text into an optimization model, it can
**silently drop, loosen, or mis-encode** a hard constraint. The model still compiles, still
solves, and returns a plausible answer — whose optimum may nonetheless violate a binding rule.
In a regulated setting this is a compliance failure, and it is silent: nothing in the solver
output indicates a rule was lost. Detecting it is hard because there is **no reference answer** —
correctness is a relation between the model and the *rules*, with only the constraint text as
ground truth.

## What FaithOpt does

- **Faithfulness criterion.** A model *M* is faithful to a rule set *G* iff, for every rule
  *c* ∈ *G*: (1) **soundness** — *M* admits no point violating *c*; and (2) **coverage** — some
  single constraint of *M* actually encodes *c*. Defined over the feasible region, not syntax.
- **A provably sound, reference-free verifier.** Realized as an SMT procedure (z3): soundness is
  decided by checking *M* ∧ ¬*c* for unsatisfiability; coverage by a per-constraint entailment
  test. A "faithful" verdict is never a false positive.
- **Closes the empty-set loophole.** Under jointly infeasible rules, an infeasible model entails
  every rule vacuously, so entailment-only checking certifies a model even after a rule is
  silently dropped. Coverage is the minimal condition that closes this gap.
- **FaithConstraint-OR benchmark.** Four splits — `single`, `multi`, `identification`,
  `multivariate` — that decompose the sources of failure. Ground truth is mechanically generated
  and z3-verified, never produced by an LLM.
- **A verify–repair loop.** Returns a concrete counterexample to the model (no gold values
  leaked) and asks it to fix the formulation, re-auditing until faithful or a round budget is hit.

## Key findings

All numbers below are produced by the scripts in this repo (`formulator.py`,
`faithopt_loop.py`, `verify_theory.py`, `analyze_results.py`) on the four benchmark splits,
over six frontier models. They are reproducible up to provider-side LLM nondeterminism.

### 1. Silent violation is high — and *which* model is safest flips by constraint type

Bare-LLM violation rate on the hard + very-hard instances of each split (lower is better).
Per cell, the safest model in that column is **bold**; the worst is _italic_.

| Model | single (n=24) | multi (n=150) | identification (n=150) | multivariate (n=174) |
|---|---|---|---|---|
| claude-opus-4-7        | 16.7% | **7.3%** | **10.7%** | **1.1%** |
| gpt-5.4                | **0.0%** | 19.3% | 19.3% | _37.4%_ |
| qwen3-max              | 16.7% | **1.3%** | **5.3%** | **0.6%** |
| deepseek-v3.2          | _20.8%_ | 11.3% | 12.7% | 5.7% |
| gpt-4o-2024-11-20      | 16.7% | _34.7%_ | 21.3% | 7.5% |
| claude-haiku-4-5       | _25.0%_ | _30.7%_ | _52.7%_ | 1.7% |

**No model is uniformly safest.** `gpt-5.4` is the best on `single` (0.0%) yet the worst on
`multivariate` (37.4%); `claude-haiku-4-5` is the worst on `identification` (52.7%) yet near the
best on `multivariate` (1.7%); `claude-opus-4-7` and `qwen3-max` lead on the harder splits but
not on `single`. Reliability cannot be obtained by model selection — motivating a verification
layer that does not depend on which model is used. (95% Wilson CIs are reported by
`analyze_results.py`; the `single` column excludes 3 out-of-scope marker instances and the
Easy/Medium rows, which are a sanity check and sit near 0%.)

### 2. The verify–repair loop reveals where repair works — and where it cannot

Bare-LLM vs. FaithOpt violation rate after up to 3 repair rounds (lower is better). All six
models, on the two splits that isolate the two failure types.

**Multivariate (mis-encoding failures) — repair drives violation to near zero:**

| Model | Bare | FaithOpt | Repaired | Avg. rounds |
|---|---|---|---|---|
| claude-opus-4-7  | 0.0%  | **0.0%** | —     | —    |
| gpt-5.4          | 36.8% | **0.0%** | 64/64 | 1.00 |
| qwen3-max        | 0.6%  | **0.0%** | 1/1   | 1.00 |
| deepseek-v3.2    | 5.7%  | **0.0%** | 10/10 | 1.00 |
| gpt-4o-2024-11-20| 5.2%  | 1.1%     | 7/9   | 1.71 |
| claude-haiku-4-5 | 1.7%  | 0.6%     | 2/3   | 1.00 |



**Identification (the model never recognized the rule) — repair only partially helps:**

| Model | Bare | FaithOpt | Repaired | Avg. rounds |
|---|---|---|---|---|
| gpt-5.4          | 20.0% | **0.0%** | 30/30 | 1.00 |
| claude-opus-4-7  | 24.7% | 9.3%     | 23/37 | 1.43 |
| qwen3-max        | 6.0%  | 4.7%     | 2/9   | 1.00 |
| deepseek-v3.2    | 12.0% | 3.3%     | 13/18 | 1.38 |
| gpt-4o-2024-11-20| 19.3% | 16.0%    | 5/29  | 1.00 |
| claude-haiku-4-5 | 51.3% | 32.7%    | 28/77 | 1.39 |

**The contrast is the point.** A counterexample localizes a *mis-encoded* coupling, so
multivariate failures are repaired almost completely (worst residual 1.1%), often in about one
round. An *identification* failure offers no localizing witness — a counterexample cannot point
at a constraint the model never recognized — so large residuals remain (claude-haiku 51.3% →
32.7%, gpt-4o 19.3% → 16.0%). Detection is unconditional; repair is conditional on
localizability.

### 3. The effect is not an artifact of the prompt

Neutral-prompt ablation on `identification` (looser instructions → violation *rises*, ruling
out a prompt-induced effect):

| Model | Full prompt | Neutral prompt |
|---|---|---|
| gpt-5.4          | 19.3% | 62.7% |
| deepseek-v3.2    | 12.7% | 73.3% |
| claude-haiku-4-5 | 52.7% | 78.7% |

### 4. Soundness alone is incomplete; coverage closes the gap

`verify_theory.py` constructs, on the benchmark's conflict scenarios, models with a silently
dropped rule and checks them two ways: **across 225 conflict scenarios, entailment-only
checking is fooled into certifying a model with a dropped rule in 158 cases — all 158 of which
the coverage-augmented audit catches.** This is the empirical backing for the completeness
result.

## Quick start

```bash
pip install -r requirements.txt

# Validate the whole pipeline with no API key (mock LLM):
python faithopt_loop.py --mock --data FaithConstraint-OR_multivariate.jsonl

# Regenerate + verify the benchmark and run the theory checks (no API):
NO_LLM=1 bash reproduce.sh
```

Minimal verifier usage:
```python
import faithopt_verifier as V
decls = [V.Var("p1","real",lb=0,ub=100), V.Var("p2","real",lb=0,ub=100)]
gold  = [V.LinCon("budget", {"p1":1.0,"p2":1.0}, "<=", 30.0),
         V.LinCon("rel",    {"p1":1.0,"p2":-1.0}, "<=", 0.0)]   # p1 <= p2
model = [V.LinCon("m1", {"p1":1.0,"p2":1.0}, "<=", 30.0)]       # dropped the relative rule
for r in V.audit(decls, model, gold):
    print(r["cid"], r["faithful"], r["reason"])   # 'rel' -> False, not_covered(dropped)
```

## Running the experiments

The LLM experiments use an OpenAI-compatible endpoint:
```bash
export GLOBALAI_KEY=sk-...                 # your API key (never commit this)
export LLM_BASE_URL=https://...            # optional; defaults to the bundled endpoint
bash reproduce.sh                          # full pipeline: bare-LLM, ablation, loop, tables

# or run a single step, e.g. bare-LLM on one split:
python formulator.py --model gpt-4o-2024-11-20 --data FaithConstraint-OR_multivariate.jsonl --workers 10
python analyze_results.py --runs runs      # recompute the tables above (with 95% CIs)
```

See **[CODE_README.md](CODE_README.md)** for the full file-by-file guide, dataset schema, and
how to run individual steps, and **[DATA_CARD.md](DATA_CARD.md)** for benchmark metadata.

## Repository layout

```
faithopt_verifier.py        SMT verifier core (Var, LinCon, entails, covers, audit)
formulator.py               bare-LLM harness
faithopt_loop.py            verify-repair loop
verify_theory.py            empirical backing for the theory (Path A / Path B)
analyze_results.py          computes the result tables (with Wilson CIs) from runs/
generate_tier2.py           generator: multi split
generate_tier3.py           generator: identification split
generate_tier4_multivar.py  generator: multivariate split
gen_overdetermined.py       appends over-determined conflicts to multivariate
make_data_card.py           (re)generates DATA_CARD.md
reproduce.sh                one-command reproduction
requirements.txt            dependencies
FaithConstraint-OR_*.jsonl  the four benchmark splits (44 / 150 / 150 / 174)
DATA_CARD.md                benchmark metadata
CODE_README.md              full code & reproducibility guide
```

## Notes on the benchmark

- **Ground truth is mechanical, not labeled.** The `multi`, `identification`, and
  `multivariate` splits are produced by deterministic generators (fixed seeds → byte-for-byte
  reproducible); gold constraints are computed by formula and independently re-checked with z3.
  `single` is a hand-written baseline. This is *stronger* than human or LLM labeling: anyone can
  regenerate the exact data and verify the answers.
- **Real structure, synthetic numbers.** Constraint *structures* are real regulated
  pharmacy-operations rules (pricing, reimbursement, procurement, assortment); numeric values
  are synthetic, for privacy.
- **Scope.** Linear constraints, including multivariate cross-variable coupling. Non-linear and
  logical/conditional constraints are out of scope.

## Citation

If you use FaithOpt or FaithConstraint-OR, please cite:

```bibtex
@article{lian2026faithopt,
  title   = {FaithOpt: Verifying the Faithfulness of LLM-Generated Optimization Models to Regulatory Constraints},
  author  = {Lian, Junbo Jacob and Qin, Hanzhang and Teo, Chung-Piaw},
  year    = {2026}
}
```

## License

Released under the MIT License — see [LICENSE](LICENSE).