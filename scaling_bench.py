#!/usr/bin/env python3
"""
scaling_bench.py  --  Verifier scaling benchmark for FaithOpt.

Times the SMT audit (Algorithm 1) as a function of problem size, to show the
verifier is not the bottleneck. NO API / LLM is used -- this is pure Z3 timing,
so it runs locally in minutes at essentially zero cost.

It reuses faithopt_verifier.audit / model_feasible, so the timing reflects the
exact procedure used in the paper.

Two regimes are measured:
  * FEASIBLE  (typical deployment): the model is feasible, so audit does |G|
    soundness queries (the coverage path is not entered). This is the realistic
    per-instance cost.
  * INFEASIBLE (worst case): the model is infeasible, so audit additionally runs
    the per-rule coverage test -- up to |G|.|M| single-constraint queries. This
    upper-bounds the cost.

Each cell is timed over a few repetitions; we report the minimum (least noisy).

Usage (defaults run in a few minutes):
  python scaling_bench.py
  python scaling_bench.py --kind int --constraints 10 50 100 200 --variables 2 10 50
  python scaling_bench.py --reps 5 --max-coverage-constraints 300

Outputs:
  scaling_runs.csv      one row per (regime, kind, N_vars, M_constraints): audit_seconds, n_queries, verdict_ok
  scaling_summary.txt   human-readable table (paste this back)
"""
import argparse
import csv
import random
import time

import faithopt_verifier as V


# ----------------------------------------------------------------------
# Synthetic instance generators
# ----------------------------------------------------------------------
def build_feasible(n_vars, m_cons, kind="real", seed=0, density=4):
    """A feasible linear system: every constraint is satisfied by a planted
    interior point x*, so the conjunction is feasible by construction.
    Returns (decls, gold_constraints, x_star)."""
    rng = random.Random(seed)
    decls = [V.Var(f"x{i}", kind, lb=0, ub=100) for i in range(n_vars)]
    xstar = {f"x{i}": rng.randint(10, 90) for i in range(n_vars)}
    names_all = [f"x{i}" for i in range(n_vars)]
    gold = []
    for j in range(m_cons):
        k = min(n_vars, rng.randint(2, max(2, density)))
        names = rng.sample(names_all, k)
        terms = {nm: rng.choice([-2, -1, 1, 1, 2]) for nm in names}
        lhs = sum(terms[nm] * xstar[nm] for nm in names)
        sense = rng.choice(["<=", ">="])
        slack = rng.randint(1, 20)
        rhs = lhs + slack if sense == "<=" else lhs - slack  # x* strictly satisfies
        gold.append(V.LinCon(f"c{j}", terms, sense, float(rhs)))
    return decls, gold, xstar


def make_infeasible_model(decls, gold):
    """The same constraints plus one that contradicts the domain (x0 <= -1000 with
    x0 >= 0), making the model's feasible region empty -> forces the coverage path."""
    bad = V.LinCon("INF", {decls[0].name: 1.0}, "<=", -1000.0)
    return list(gold) + [bad]


# ----------------------------------------------------------------------
# Timing
# ----------------------------------------------------------------------
def time_audit(decls, model, gold, reps=3):
    best = float("inf")
    report = None
    for _ in range(reps):
        t0 = time.perf_counter()
        report = V.audit(decls, model, gold)
        dt = time.perf_counter() - t0
        best = min(best, dt)
    return best, report


def n_queries(m_cons, feasible):
    """Soundness queries (|G|) plus, in the infeasible regime, up to |G|.|M| coverage queries.
    |M| here = m_cons + 1 (the added contradiction)."""
    if feasible:
        return m_cons                      # |G| soundness checks only
    return m_cons + m_cons * (m_cons + 1)   # |G| soundness + up to |G|.|M| coverage


def run(args):
    rows = []
    regimes = []
    if not args.only_infeasible:
        regimes.append(("feasible", args.constraints))
    if not args.only_feasible:
        cov = [m for m in args.constraints if m <= args.max_coverage_constraints]
        regimes.append(("infeasible", cov))

    for regime, cons_list in regimes:
        for kind in args.kind:
            for n in args.variables:
                for m in cons_list:
                    decls, gold, _ = build_feasible(n, m, kind=kind, seed=args.seed)
                    if regime == "feasible":
                        model = list(gold)            # faithful, feasible
                    else:
                        model = make_infeasible_model(decls, gold)
                    secs, report = time_audit(decls, model, gold, reps=args.reps)
                    ok = all(r["faithful"] for r in report) if regime == "feasible" \
                        else all((not r["faithful"]) for r in report)
                    rows.append({
                        "regime": regime, "kind": kind, "n_vars": n, "m_constraints": m,
                        "audit_seconds": round(secs, 4),
                        "n_queries_upper": n_queries(m, regime == "feasible"),
                        "verdict_as_expected": ok,
                    })
                    print(f"[{regime:10} {kind:4}] vars={n:4d} cons={m:5d}  "
                          f"audit={secs*1000:9.1f} ms  (expected verdict: {ok})")

    with open("scaling_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lines = []
    lines.append("FaithOpt verifier scaling  (audit runtime; pure Z3, no LLM/API)")
    lines.append(f"reps={args.reps}, seed={args.seed}; time is the min over reps.")
    lines.append("")
    lines.append(f"{'regime':12}{'kind':6}{'vars':>6}{'constraints':>13}{'audit (ms)':>13}{'queries<=':>12}")
    lines.append("-" * 62)
    for r in rows:
        lines.append(f"{r['regime']:12}{r['kind']:6}{r['n_vars']:>6}{r['m_constraints']:>13}"
                     f"{r['audit_seconds']*1000:>13.1f}{r['n_queries_upper']:>12}")
    txt = "\n".join(lines)
    with open("scaling_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    print("\n" + txt)
    print("\nWrote scaling_runs.csv, scaling_summary.txt  (paste scaling_summary.txt back)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variables", type=int, nargs="+", default=[2, 5, 10, 20, 50],
                    help="numbers of decision variables to sweep")
    ap.add_argument("--constraints", type=int, nargs="+",
                    default=[10, 25, 50, 100, 250],
                    help="numbers of gold constraints to sweep")
    ap.add_argument("--kind", nargs="+", default=["real", "int"],
                    choices=["real", "int"], help="variable domain(s) to test")
    ap.add_argument("--reps", type=int, default=3, help="timing repetitions per cell")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-coverage-constraints", type=int, default=100,
                    help="cap M for the infeasible/coverage regime (|G|.|M| grows quadratically)")
    ap.add_argument("--only-feasible", action="store_true")
    ap.add_argument("--only-infeasible", action="store_true")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
