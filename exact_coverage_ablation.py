# -*- coding: utf-8 -*-
"""exact_coverage_ablation.py  --  controlled demonstration of Proposition 2.

The verifier's default coverage check is the single-witness RELAXATION: a rule c is
"covered" iff some *single* constraint of M entails c. On an infeasible model that
encodes c only DISTRIBUTIVELY (jointly across several constraints, no single one
entailing it), the relaxation raises a false coverage flag. Proposition 2's EXACT
criterion -- some *feasible subset* S of M entails c -- accepts the distributed
encoding and so raises no false alarm.

This script builds such infeasible-distributed models (and, as a control, genuine
drops) and reports, on each rule:
  * RELAXATION verdict (single-witness)   -- false-positive on distributed encodings
  * EXACT verdict (feasible-subset, Prop 2) -- correct

Pure z3, no API.  (Prop 2 / Remark on tractability shows the exact test is decidable
by one LP per rule via the IIS / Gleeson-Ryan alternative; on these small instances we
compute the *same* criterion by its definition -- a feasible subset entailing c -- which
is unambiguous and serves to exhibit the relaxation's false positives.)
"""
import itertools
from z3 import Real, Solver, sat, unsat, Or

# a constraint is (terms: {var: coef}, sense: '<='|'>='|'==', rhs: float)
def _lin(terms, X):
    e = 0
    for v, c in terms.items():
        e = e + c * X[v]
    return e

def _add_con(s, X, con):
    t, se, r = con; e = _lin(t, X)
    s.add(e <= r if se == '<=' else e >= r if se == '>=' else e == r)

def _add_neg(s, X, con):          # add NOT(con), strict, so closed-halfspace entailment is exact
    t, se, r = con; e = _lin(t, X)
    if se == '<=':   s.add(e > r)
    elif se == '>=': s.add(e < r)
    else:            s.add(Or(e > r, e < r))

def model_feasible(decls, cons):
    s = Solver(); X = {v: Real(v) for v in decls}
    for v, (lb, ub) in decls.items():
        if lb is not None: s.add(X[v] >= lb)
        if ub is not None: s.add(X[v] <= ub)
    for con in cons: _add_con(s, X, con)
    return s.check() == sat

def entails(decls, S, c):         # does subset S (+ var bounds) entail c?  S & not(c) unsat
    s = Solver(); X = {v: Real(v) for v in decls}
    for v, (lb, ub) in decls.items():
        if lb is not None: s.add(X[v] >= lb)
        if ub is not None: s.add(X[v] <= ub)
    for con in S: _add_con(s, X, con)
    _add_neg(s, X, c)
    return s.check() == unsat

def covers_relax(decls, M, c):    # single-witness relaxation: some SINGLE constraint entails c
    return any(entails(decls, [m], c) for m in M)

def covers_exact(decls, M, c):    # Proposition 2: some FEASIBLE subset entails c
    for k in range(1, len(M) + 1):
        for S in itertools.combinations(M, k):
            if model_feasible(decls, list(S)) and entails(decls, list(S), c):
                return True
    return False

# ----------------------------- controlled instances -----------------------------
# 2 prices in [0,100]; a hard conflict pair forces infeasibility independently of c,
# so coverage is always invoked. c is a SUM cap p1+p2 <= K.
DECLS = {"p1": (0.0, 100.0), "p2": (0.0, 100.0)}
CONFLICT = [({"p1": 1.0, "p2": -1.0}, ">=", 10.0),   # p1 - p2 >= 10
            ({"p1": -1.0, "p2": 1.0}, ">=", 10.0)]   # p2 - p1 >= 10   (jointly infeasible)

def make_instances():
    """Return (distributed, genuine) lists of (M, c) pairs."""
    dist, genu = [], []
    for K in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0):
        c = ({"p1": 1.0, "p2": 1.0}, "<=", K)        # the rule whose coverage we test
        for frac in (0.3, 0.5, 0.7):
            a = round(K * frac, 2); b = round(K - a, 2)
            if not (0 <= a <= 100 and 0 <= b <= 100):
                continue
            # drop c, re-encode it DISTRIBUTIVELY as p1<=a AND p2<=b (jointly => p1+p2<=K)
            M_dist = CONFLICT + [({"p1": 1.0}, "<=", a), ({"p2": 1.0}, "<=", b)]
            dist.append((M_dist, c))
        # genuine drop: c removed entirely, nothing re-encodes it
        genu.append((CONFLICT[:], c))
    return dist, genu

if __name__ == "__main__":
    dist, genu = make_instances()

    def tally(pairs, label):
        rl_cov = ex_cov = 0
        for M, c in pairs:
            assert not model_feasible(DECLS, M), "instance should be infeasible (coverage gated)"
            rl = covers_relax(DECLS, M, c)
            ex = covers_exact(DECLS, M, c)
            rl_cov += rl; ex_cov += ex
        n = len(pairs)
        # a model is FLAGGED (not_covered) when the test says NOT covered
        rl_flag = n - rl_cov; ex_flag = n - ex_cov
        print(f"\n{label}: {n} infeasible instances")
        print(f"  single-witness RELAXATION : covered {rl_cov}/{n}  -> flags(not_covered) {rl_flag}/{n}")
        print(f"  EXACT (Prop 2, feasible-subset): covered {ex_cov}/{n}  -> flags(not_covered) {ex_flag}/{n}")
        return n, rl_flag, ex_flag

    print("=" * 68)
    print("Exact-vs-relaxation coverage on infeasible models (Proposition 2)")
    print("=" * 68)
    nd, rl_fd, ex_fd = tally(dist, "DISTRIBUTED encoding (rule jointly encoded; truly covered)")
    ng, rl_fg, ex_fg = tally(genu, "GENUINE drop (rule absent; truly NOT covered)")

    print("\n" + "-" * 68)
    print("SUMMARY")
    print(f"  On distributed-encoding instances the relaxation FALSE-ALARMS on "
          f"{rl_fd}/{nd}; the exact test false-alarms on {ex_fd}/{nd}.")
    print(f"  On genuine drops both correctly flag ({rl_fg}/{ng} and {ex_fg}/{ng}).")
    if rl_fd > 0 and ex_fd == 0:
        print(f"  => Proposition 2 removes all {rl_fd} relaxation false positives, "
              f"with no change on genuine drops.")
