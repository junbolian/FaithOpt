"""
faithopt_verifier.py - FaithOpt verifier core

FAITHFULNESS (definition used throughout):
  A formulated model M is FAITHFUL to a set of hard constraints G iff, for every c in G:
    (1) SOUNDNESS  -- M does not permit violating c:   M  |=  c   (i.e. M and (not c) is UNSAT)
    (2) COVERAGE   -- c is actually represented in M:   some single constraint m in M has m |= c
  Faithfulness is defined over the FEASIBLE REGION, not over syntax: a gold constraint that is
  implied by a tighter constraint already in M is considered satisfied (e.g. encoding p<=6.25
  discharges a gold bound p<=12). M need not restate redundant/dominated constraints. What is
  forbidden is (a) permitting a point that violates some c (unsound), or (b) dropping a c whose
  bound is NOT implied by anything in M (incomplete). Coverage is feasibility-independent, so an
  infeasible (conflicting) model cannot vacuously "cover" a constraint it never encoded.
================================
Constraint-faithful NL->OR verification (decidable tier) for pharmacy retail.

What this gives you TODAY:
  1) A typed constraint IR (LinCon) for the decidable class L_dec.
  2) A SOUND entailment verifier: M |= c  <=>  (M and not c) is UNSAT  [via Z3 SMT].
     - "faithful"  -> proven: every feasible solution of M satisfies c.
     - "violated"  -> returns a concrete counterexample (a silent violation).
  3) Automated failure injection (drop / sense-flip / magnitude / hallucinate).
  4) The go/no-go harness: measure an LLM formulator's silent-violation rate
     against a gold hard-constraint set.

Swap `mock_buggy_formulator` for a real frontier-LLM call to run the actual
go/no-go measurement on YOUR regulatory tuples.

Dependencies: z3-solver  (pip install z3-solver)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional
from collections import Counter
import copy
import random
import z3


# ----------------------------------------------------------------------
# Typed IR  (the decidable class L_dec)
# ----------------------------------------------------------------------
@dataclass
class Var:
    name: str
    kind: str = "real"          # 'real' | 'int' | 'bool'
    lb: Optional[float] = None  # domain lower bound (e.g. price >= cost)
    ub: Optional[float] = None


@dataclass
class LinCon:
    """A linear (or logical-via-indicator) constraint, perturbable and Z3-compilable."""
    cid: str
    terms: dict                 # var_name -> coefficient
    sense: str                  # '<=' | '>=' | '=='
    rhs: float
    source_span: str = ""       # the regulatory text span this came from (audit trail)
    hard: bool = True
    cls: str = "L_dec"          # decidable-class tag


# ----------------------------------------------------------------------
# Z3 compilation
# ----------------------------------------------------------------------
def _make_vars(decls: list[Var]):
    zvars, domain = {}, []
    for d in decls:
        if d.kind == "real":
            v = z3.Real(d.name)
        elif d.kind == "int":
            v = z3.Int(d.name)
        elif d.kind == "bool":
            v = z3.Bool(d.name)
        else:
            raise ValueError(f"unknown kind {d.kind}")
        zvars[d.name] = v
        if d.lb is not None:
            domain.append(v >= d.lb)
        if d.ub is not None:
            domain.append(v <= d.ub)
    return zvars, domain


def _compile(c: LinCon, zvars: dict):
    lhs = z3.Sum([coef * zvars[name] for name, coef in c.terms.items()])
    if c.sense == "<=":
        return lhs <= c.rhs
    if c.sense == ">=":
        return lhs >= c.rhs
    if c.sense == "==":
        return lhs == c.rhs
    raise ValueError(f"unknown sense {c.sense}")


# ----------------------------------------------------------------------
# Core verifier:  M |= c  ?
# ----------------------------------------------------------------------
def entails(decls: list[Var], model: list[LinCon], c: LinCon):
    """
    Returns (verdict, counterexample):
      verdict True  -> faithful (M entails c); counterexample None
      verdict False -> violated; counterexample is a dict {var: value}
      verdict None  -> solver returned 'unknown'
    Soundness: a True verdict is never a false positive.
    """
    zvars, domain = _make_vars(decls)
    s = z3.Solver()
    for d in domain:
        s.add(d)
    for m in model:
        s.add(_compile(m, zvars))
    s.add(z3.Not(_compile(c, zvars)))      # try to satisfy the negation within M
    r = s.check()
    if r == z3.unsat:
        return True, None                  # no feasible solution violates c  -> faithful
    if r == z3.sat:
        mdl = s.model()
        ce = {name: str(mdl.eval(v, model_completion=True)) for name, v in zvars.items()}
        return False, ce                   # a feasible solution violates c    -> silent violation
    return None, None


def model_feasible(decls: list[Var], model: list[LinCon]) -> bool:
    """Is the model's own feasible region non-empty? Coverage is only needed when this is False
    (an infeasible model entails every rule vacuously, which is the empty-set loophole)."""
    zvars, domain = _make_vars(decls)
    s = z3.Solver()
    for d in domain:
        s.add(d)
    for m in model:
        s.add(_compile(m, zvars))
    return s.check() == z3.sat


def covers(decls: list[Var], model: list[LinCon], c: LinCon):
    """Completeness (per-rule witness): is gold constraint c REPRESENTED by some single model
    constraint m that, on its own, entails c (m at least as tight as c)? This per-singleton test
    never forms Feas(M), so an infeasible (empty-set) model cannot 'cover' a constraint it never
    encoded. A tighter dominating constraint (e.g. p<=6.25 covers gold p<=12) is fine."""
    for m in model:
        v, _ = entails(decls, [m], c)
        if v is True:
            return True
    return False

def audit(decls: list[Var], model: list[LinCon], gold_hard: list[LinCon]):
    """Verify a model against the full gold hard-constraint set; return per-constraint result.

    Coverage is gated on feasibility (matches Definition 2 in the paper): if the model is
    feasible, soundness alone decides each rule, so a rule encoded jointly by several constraints
    (distributed encoding) is correctly accepted with no false alarm. Coverage's per-rule witness
    is invoked only when the model is infeasible, to stop vacuous entailment from masking a drop."""
    report = []
    feasible = model_feasible(decls, model)
    for c in gold_hard:
        if not c.hard:
            continue
        sound, ce = entails(decls, model, c)          # M does not permit a point violating c
        if not sound:
            faithful, reason = False, "violated"
        elif feasible:
            faithful, reason = True, "ok"             # feasible M: soundness suffices
        elif covers(decls, model, c):
            faithful, reason = True, "ok"             # infeasible M but rule individually witnessed
        else:
            faithful, reason = False, "not_covered(dropped)"  # infeasible & unwitnessed: silent drop
        report.append({"cid": c.cid, "faithful": faithful, "counterexample": ce,
                       "reason": reason, "source_span": c.source_span})
    return report


# ----------------------------------------------------------------------
# Automated failure injection  (labeled negatives for the benchmark)
# ----------------------------------------------------------------------
def inject(model: list[LinCon], mode: str, target_cid: str | None = None, rng=random):
    M = copy.deepcopy(model)
    if mode == "drop":
        return [c for c in M if c.cid != target_cid]
    if mode == "sense":
        for c in M:
            if c.cid == target_cid:
                c.sense = {"<=": ">=", ">=": "<=", "==": "<="}[c.sense]
        return M
    if mode == "magnitude":            # unit / scale error
        for c in M:
            if c.cid == target_cid:
                c.rhs = c.rhs * 10
        return M
    if mode == "offbyone":
        for c in M:
            if c.cid == target_cid:
                c.rhs = c.rhs + 1
        return M
    if mode == "hallucinate":          # add a spurious constraint not in any source
        vname = list(M[0].terms.keys())[0]
        M.append(LinCon("HALLUC", {vname: 1.0}, "<=", 1e9, "(no source)"))
        return M
    raise ValueError(f"unknown mode {mode}")


# ----------------------------------------------------------------------
# GO / NO-GO harness:  silent-violation rate of a formulator
# ----------------------------------------------------------------------
def silent_violation_rate(tuples: list[dict], formulate: Callable):
    """
    tuples: list of dicts, each with keys:
        'intent'          : str  (manager NL request)
        'reg_text'        : str  (retrieved regulatory passages)
        'var_decls'       : list[Var]
        'gold_hard'       : list[LinCon]  (the constraints the model MUST entail)
    formulate(intent, reg_text, var_decls) -> list[LinCon]   (the candidate model)
        In production this CALLS A FRONTIER LLM. Here we plug a mock.
    Returns (rate, total_hard, violated, per_tuple).
    """
    total_hard = violated = 0
    per_tuple = []
    for t in tuples:
        M = formulate(t["intent"], t["reg_text"], t["var_decls"])
        rep = audit(t["var_decls"], M, t["gold_hard"])
        v = sum(1 for r in rep if r["faithful"] is False)
        total_hard += len(rep)
        violated += v
        per_tuple.append({"intent": t["intent"], "violated": v, "of": len(rep), "report": rep})
    rate = violated / total_hard if total_hard else 0.0
    return rate, total_hard, violated, per_tuple


# A mock "LLM" that silently drops one random hard constraint ~40% of the time,
# so the harness runs end-to-end. REPLACE with a real LLM call for the real measurement.
def mock_buggy_formulator(intent, reg_text, var_decls, _gold=None, rng=random):
    gold = _gold[:]                      # closure injected below
    if rng.random() < 0.4 and gold:
        drop = rng.choice(gold).cid
        return [c for c in gold if c.cid != drop]
    return gold


# ----------------------------------------------------------------------
# DEMO  (synthetic pharmacy pricing example - replace with real regulatory tuples)
# ----------------------------------------------------------------------
def _demo():
    print("=" * 68)
    print("FaithOpt verifier demo - pharmacy reimbursable-Rx pricing")
    print("=" * 68)

    # One decision: set retail price for a reimbursable Rx drug.
    decls = [Var("price", "real", lb=0.0)]   # price >= 0; cost handled by c_margin

    # GOLD hard constraints, each tied to a regulatory source span:
    gold = [
        LinCon("c_cap",    {"price": 1.0}, "<=", 18.0, "Reimbursement catalog: reimbursable-price cap 18"),
        LinCon("c_floor",  {"price": 1.0}, ">=", 12.0, "VBP: winning-bid / price floor 12"),
        LinCon("c_margin", {"price": 1.0}, ">=", 12.0, "Internal policy: cost 10 + min margin 2"),
    ]

    print("\n[1] Faithful model (= gold). Expect all faithful:")
    for r in audit(decls, gold, gold):
        print(f"    {r['cid']:9s} faithful={r['faithful']}")

    print("\n[2] DROP the reimbursement cap (silent violation). Expect c_cap violated + counterexample:")
    M_drop = inject(gold, "drop", "c_cap")
    for r in audit(decls, M_drop, gold):
        print(f"    {r['cid']:9s} faithful={r['faithful']}  ce={r['counterexample']}")

    print("\n[3] SENSE error on cap (<= became >=). Expect c_cap violated:")
    M_sense = inject(gold, "sense", "c_cap")
    for r in audit(decls, M_sense, gold):
        print(f"    {r['cid']:9s} faithful={r['faithful']}  ce={r['counterexample']}")

    print("\n[4] MAGNITUDE/unit error on cap (18 -> 180). Expect c_cap violated:")
    M_mag = inject(gold, "magnitude", "c_cap")
    for r in audit(decls, M_mag, gold):
        print(f"    {r['cid']:9s} faithful={r['faithful']}  ce={r['counterexample']}")

    # Logical/indicator example: shows L_dec covers Boolean/indicator constraints too.
    print("\n[5] Logical constraint (Rx implies prescription-required) via Z3 directly:")
    is_rx = z3.Bool("is_rx")
    needs_rx = z3.Bool("needs_rx")
    s = z3.Solver()
    s.add(z3.Implies(is_rx, needs_rx))      # MODEL enforces it
    s.add(z3.Not(z3.Implies(is_rx, needs_rx)))  # negation of the normative rule
    print(f"    model enforces rule -> entailment check: {s.check()} (unsat = faithful)")

    print("\n[6] GO/NO-GO harness on a tiny synthetic set (mock buggy 'LLM'):")
    tuples = [{
        "intent": "Set price for reimbursable Rx drug A at store cluster X.",
        "reg_text": "(reimbursement cap 18) (VBP floor 12) (min margin)",
        "var_decls": decls,
        "gold_hard": gold,
    } for _ in range(25)]
    rng = random.Random(42)
    formulate = lambda i, r, v: mock_buggy_formulator(i, r, v, _gold=gold, rng=rng)
    rate, total, viol, _ = silent_violation_rate(tuples, formulate)
    print(f"    silent-violation rate = {rate:.1%}  ({viol}/{total} hard constraints)")
    print(f"    -> in the REAL gate, replace the mock with GPT/Claude/DeepSeek/Qwen formulators.")
    print("\nDone.")


if __name__ == "__main__":
    _demo()