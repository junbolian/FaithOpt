# FaithOpt

**Verifying the faithfulness of LLM-generated optimization models to regulatory constraints.**

FaithOpt checks whether an optimization model produced by a large language model is *provably
faithful* to the hard constraints stated in regulatory or policy text — without any reference
answer. It pairs an SMT-based verifier (z3) with a benchmark, **FaithConstraint-OR**, and a
verify–repair loop.

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
```

See **[CODE_README.md](CODE_README.md)** for the full file-by-file guide, dataset schema, and
how to run individual steps, and **[DATA_CARD.md](DATA_CARD.md)** for benchmark metadata.

## Repository layout

```
faithopt_verifier.py        SMT verifier core (Var, LinCon, entails, covers, audit)
formulator.py               bare-LLM harness
faithopt_loop.py            verify-repair loop
verify_theory.py            empirical backing for the theory
analyze_results.py          computes result tables from runs/
generate_tier2.py           generator: multi split
generate_tier3.py           generator: identification split
generate_tier4_multivar.py  generator: multivariate split
gen_overdetermined.py       appends over-determined conflicts to multivariate
make_data_card.py           (re)generates DATA_CARD.md
reproduce.sh                one-command reproduction
requirements.txt            dependencies
FaithConstraint-OR_*.jsonl  the four benchmark splits
DATA_CARD.md                benchmark metadata
CODE_README.md              full code & reproducibility guide
```

## Scope

FaithOpt handles **linear** constraints, including multivariate ones with cross-variable
coupling. Non-linear and logical/conditional constraints are out of scope. The benchmark uses
real regulatory rule *structures* with synthetic numeric values.

## License

Released under the MIT License — see [LICENSE](LICENSE).
