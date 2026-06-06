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
  *c* ∈ *G*: (1) **soundness** — *M* admits no point violating *c*; and (2) **coverage
  (non-vacuity)** — *if M is infeasible*, some single constraint of *M* actually encodes *c*.
  Defined over the feasible region, not syntax. Coverage is gated on infeasibility: when *M* is
  feasible (the common case), soundness alone decides faithfulness, so a rule encoded jointly by
  several constraints is correctly accepted; the per-constraint witness is needed only when *M* is
  infeasible, to stop vacuous entailment from hiding a dropped rule.
- **A provably sound, reference-free verifier.** Realized as an SMT procedure (z3): soundness is
  decided by checking *M* ∧ ¬*c* for unsatisfiability; coverage by a per-constraint entailment
  test invoked only when *M* is infeasible. A "faithful" verdict is never a false positive.
- **Closes the empty-set loophole.** Under jointly infeasible rules, an infeasible model entails
  every rule vacuously, so entailment-only checking certifies a model even after a rule is
  silently dropped. Coverage is a lightweight condition that closes this gap.
- **FaithConstraint-OR benchmark.** Four splits — `single`, `multi`, `identification`,
  `multivariate` — that decompose the sources of failure. Ground truth is mechanically generated
  and z3-verified, never produced by an LLM.
- **A verify–repair loop.** Returns a concrete counterexample to the model (no gold values
  leaked) and asks it to fix the formulation, re-auditing until faithful or a round budget is hit.

## Key findings

All numbers below are produced by the scripts in this repo (`run_multigen.py`,
`per_rule_analysis.py`, `faithopt_loop.py`, `verify_theory.py`) on the four benchmark splits,
over six models spanning current frontier and lighter-weight systems. They are reproducible up to provider-side LLM nondeterminism.

### 1. Silent violation is high — and *which* model is safest flips by constraint type

Bare-LLM violation rate (%) on the hard + very-hard instances of each split, reported as
**mean ± sd over five independent generations** per instance (temperature 0). Lower is better; the
safest model in each column is **bold**, the worst is _italic_.

| Model | single (n=24) | multi (n=150) | identification (n=150) | multivariate (n=174) |
|---|---|---|---|---|
| claude-opus-4-7        | **9.2 ± 1.9** | 6.0 ± 1.1 | 24.7 ± 1.6 | 0.8 ± 0.3 |
| gpt-5.4                | 11.7 ± 1.9 | 4.3 ± 0.4 | 20.7 ± 1.6 | **0.2 ± 0.3** |
| qwen3-max              | 20.0 ± 3.5 | **1.5 ± 0.3** | **5.1 ± 1.3** | 0.5 ± 0.3 |
| deepseek-v3.2          | _21.7 ± 5.4_ | 12.8 ± 1.5 | 12.4 ± 1.9 | 5.9 ± 0.8 |
| gpt-4o-2024-11-20      | 20.0 ± 6.2 | 31.3 ± 1.8 | 19.3 ± 1.4 | _6.1 ± 0.9_ |
| claude-haiku-4-5       | 16.7 ± 3.0 | _34.9 ± 0.9_ | _50.4 ± 1.9_ | 2.1 ± 0.9 |

**The safety ranking is unstable across constraint types, and even the strongest model is not safe
enough.** `gpt-5.4` is the safest on `multivariate` (0.2%) yet among the worst on `identification` (20.7%); `claude-haiku-4-5`
is the reverse — worst on `identification` (50.4%) yet among the safest on `multivariate` (2.1%) — the order
inverts depending on the constraint type. A broadly strong model *does* exist — `qwen3-max` is the
safest of the six on two of the three failure-mode splits (`multi`, `identification`) and near-safest on the third — but it still mis-encodes a binding rule on
~5% of `identification` instances, which is not an acceptable operating point for a regulated
pricing model, and a deployer cannot know in advance which model is safest for *its* mix of
constraint types. Standard deviations are small relative to the gaps that define these effects, so they are robust to
generation variance. The point is not that no model can be chosen well, but that no available model
is reliable *enough* to make per-formulation verification unnecessary — so compliance cannot be
secured by model selection alone.
(Rates are produced by `run_multigen.py`; the `single` column reports its linear hard+very-hard
instances, excluding 3 out-of-scope marker instances, and the easy/medium tiers — a sanity check —
sit near 0%.)

We also report **per-rule** violation rates (`per_rule_analysis.py`). Because an instance counts as
violated if *any* of its several rules is, the instance rate compounds a smaller per-rule error:
for most models the instance figure is partly this aggregation (e.g. gpt-4o on `multi` is ~8%
per-rule vs ~32% per instance), while for `claude-haiku` the per-rule rate is itself high (genuine
poor fidelity, not just compounding). On `single`, with mostly one rule per instance, the two rates are close.

### 2. The verify–repair loop reveals where repair works well — and where it is incomplete

Bare-LLM vs. FaithOpt violation rate after up to 3 repair rounds (lower is better). All six
models, on the two splits that isolate the two failure types. (These are single loop-run numbers;
the *Bare* column is the one generation entering the loop and can differ from the five-generation
means above by generation variance.)

**Multivariate (mis-encoding failures) — repair drives violation to near zero:**

| Model | Bare | FaithOpt | Repaired | Avg. rounds |
|---|---|---|---|---|
| claude-opus-4-7  | 0.0%  | **0.0%** | —     | —    |
| gpt-5.4          | 1.7% | **0.0%** | 3/3 | 1.00 |
| qwen3-max        | 0.6%  | **0.0%** | 1/1   | 1.00 |
| deepseek-v3.2    | 5.7%  | **0.0%** | 10/10 | 1.00 |
| gpt-4o| 5.2%  | 1.1%     | 7/9   | 1.71 |
| claude-haiku-4-5 | 1.7%  | 0.6%     | 2/3   | 1.00 |



**Identification (rules the model did not recognize) — repair is real but incomplete and model-dependent:**

| Model | Bare | FaithOpt | Repaired | Avg. rounds |
|---|---|---|---|---|
| claude-opus-4-7  | 24.7% | 9.3%     | 23/37 | 1.43 |
| gpt-5.4          | 21.3% | 4.0% | 26/32 | 1.12 |
| qwen3-max        | 6.0%  | 4.7%     | 2/9   | 1.00 |
| deepseek-v3.2    | 12.0% | **3.3%**     | 13/18 | 1.38 |
| gpt-4o| 19.3% | 16.0%    | 5/29  | 1.00 |
| claude-haiku-4-5 | 51.3% | 32.7%    | 28/77 | 1.39 |

**The contrast is the point.** A counterexample points at a *mis-encoded* coupling, so multivariate
failures are repaired almost completely (worst residual 1.1%), often in one round. For
*identification* failures the outcome is model-dependent: some models recover well (gpt-5.4
21.3% → 4.0%, deepseek 12.0% → 3.3%), while others leave large residuals (claude-haiku 51.3% → 32.7%, gpt-4o
19.3% → 16.0%). The reason is the *kind* of feedback available — a counterexample flags that a
rule is missing but cannot point at a line to fix (no line was ever written for it), so the model
must re-derive the rule from the policy, and whether it succeeds varies by model. Detection is
unconditional; automatic repair is reliable for mis-encodings and only partial for unrecognized
rules.

### 3. The effect is not an artifact of the prompt

Neutral-prompt ablation on `identification` (looser instructions → violation *rises*, ruling
out a prompt-induced effect):

| Model | Full prompt | Neutral prompt |
|---|---|---|
| gpt-5.4          | 21.3% | 54.0% |
| deepseek-v3.2    | 12.7% | 73.3% |
| claude-haiku-4-5 | 52.7% | 78.7% |

### 4. Soundness alone is incomplete; coverage closes the gap

`verify_theory.py` constructs, on the benchmark's conflicting instances, models with a silently
dropped rule and checks them two ways. Jointly infeasible gold sets arise naturally as harder
instances accumulate constraints — **225 conflicting instances across the `multi`,
`identification`, and `multivariate` splits** (the 24 purpose-built over-determined instances are
a curated subset). On constructed drops from these, **entailment-only checking is fooled into
certifying a model with a dropped rule in 158 cases — all 158 of which the feasibility-gated audit
catches.** This is the empirical backing for the completeness result.

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

# (a) A feasible model that drops the relative-pricing rule: soundness catches it directly.
gold  = [V.LinCon("budget", {"p1":1.0,"p2":1.0}, "<=", 30.0),
         V.LinCon("rel",    {"p1":1.0,"p2":-1.0}, "<=", 0.0)]   # p1 <= p2
model = [V.LinCon("m1", {"p1":1.0,"p2":1.0}, "<=", 30.0)]       # dropped the relative rule
for r in V.audit(decls, model, gold):
    print(r["cid"], r["faithful"], r["reason"])   # 'rel' -> False, violated (admits p1=30,p2=0)

# (b) The empty-set loophole: gold is over-determined (infeasible), and the model stays
#     infeasible after a rule is dropped. Entailment alone is fooled; coverage catches it.
gold2 = [V.LinCon("cap",    {"p1":1.0,"p2":1.0}, "<=", 20.0),
         V.LinCon("floor",  {"p1":1.0,"p2":1.0}, ">=", 36.0),    # this rule is dropped
         V.LinCon("digap",  {"p1":1.0,"p2":-1.0}, ">=", 10.0),
         V.LinCon("digap2", {"p1":-1.0,"p2":1.0}, ">=", 10.0)]
model2 = [c for c in gold2 if c.cid != "floor"]                  # still infeasible (digap vs digap2)
for r in V.audit(decls, model2, gold2):
    print(r["cid"], r["faithful"], r["reason"])   # 'floor' -> False, not_covered(dropped)

# (c) Distributed encoding is NOT a false alarm: a feasible model whose constraints jointly
#     entail the gold rule passes by soundness.
gold3  = [V.LinCon("sum", {"p1":1.0,"p2":1.0}, "<=", 30.0)]
model3 = [V.LinCon("a", {"p1":1.0}, "<=", 10.0), V.LinCon("b", {"p2":1.0}, "<=", 20.0)]
for r in V.audit(decls, model3, gold3):
    print(r["cid"], r["faithful"], r["reason"])   # 'sum' -> True, ok (jointly entailed)
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
formulator.py               bare-LLM harness (single generation per instance)
run_multigen.py             multi-generation harness: 5 runs/instance -> mean ± sd
faithopt_loop.py            verify-repair loop
verify_theory.py            empirical backing for the theory (Path A / Path B)
analyze_results.py          single-run result tables (Wilson CIs) from runs/
per_rule_analysis.py        per-rule vs per-instance breakdown
scaling_bench.py            verifier scaling benchmark (pure z3 timing; no API)
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
- **Real operations, abstracted numbers.** The constraint *structures*, *clause wording*, and
  *distractor items* are drawn from the real price-governance practice of a pharmacy retail chain
  (winning-bid/procurement caps, reimbursement caps, gross-margin floors, member-tier floors,
  relative-pricing rules), written in authentic regulatory phrasing; `single` is hand-authored from
  real cases. Only the numeric values are abstracted from realistic ranges (commercial
  confidentiality + no memorization), and for the multi-rule splits these real clauses are composed
  into policy excerpts so the gold set is known exactly. It is neither invented synthetic text nor a
  transcript of one in-force document.
- **Human-checked gold.** On 60 instances from the two failure-mode splits (`multivariate`,
  `identification`), two annotators independently reconstructed the gold *from text alone* (without
  seeing it); recovery matched the gold on 119/120 instance-reconstructions, the lone miss a
  transcription slip — indicating the gold is recoverable from the text, not idiosyncratic.
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