# -*- coding: utf-8 -*-
"""
verify_theory.py - empirical backing for the two theoretical results.

PATH A (Prop. incompleteness + Thm. coverage-completes):
  On real conflict scenarios, an ENTAILMENT-ONLY verifier certifies a model that has
  silently DROPPED a gold rule (the empty-set loophole), while the coverage-augmented
  audit() correctly flags it. This is the constructive counterexample, run on actual data.

PATH B (Prop. repairability-follows-localizability):
  Classify every bare-LLM failure as localizable (non-empty parse admitting a violating
  point) vs non-localizable (empty/unconstrained parse), then report repair rate per class
  from the loop detail files. Prediction: localizable repairs high, non-localizable ~0.

Run:  python verify_theory.py
Needs: faithopt_verifier.py + the tier datasets in the same folder (Path A),
       and runs/<model>_faithopt_loop.txt files (Path B, optional).
"""
import json, re, glob, os
import faithopt_verifier as V


# ---------- shared helpers ----------
def load(path):
    return [r for r in (json.loads(l) for l in open(path, encoding="utf-8") if l.strip()) if not r.get("_manifest")]

def mk(rec):
    decls = [V.Var(v["name"], v["kind"], lb=v["lb"], ub=v["ub"]) for v in rec["var_decls"]]
    gold = [V.LinCon(g["id"], {k: float(c) for k, c in g["terms"].items()}, g["sense"], float(g["rhs"]))
            for g in rec["gold_constraints"]]
    return decls, gold


# ---------- PATH A: entailment-only is incomplete; coverage completes ----------
def entailment_only_faithful(decls, model, gold):
    """A naive verifier: check ONLY soundness (entails), skip coverage."""
    for c in gold:
        sound, _ = V.entails(decls, model, c)
        if not sound:
            return False
    return True

def path_A(datasets):
    print("=" * 70)
    print("PATH A: entailment-only verification is INCOMPLETE (empty-set loophole)")
    print("=" * 70)
    import z3
    def infeasible(rec, cons):
        s = z3.Solver(); zv = {v["name"]: z3.Real(v["name"]) for v in rec["var_decls"]}
        for g in cons:
            lhs = z3.Sum([co * zv[n] for n, co in g.terms.items()])
            s.add(lhs <= g.rhs if g.sense == "<=" else (lhs >= g.rhs if g.sense == ">=" else lhs == g.rhs))
        return s.check() != z3.sat

    loophole_hits = 0
    coverage_catches = 0
    total_conflict = 0
    for ds in datasets:
        if not os.path.exists(ds):
            continue
        for rec in load(ds):
            decls, gold = mk(rec)
            if not infeasible(rec, gold):
                continue  # loophole needs jointly infeasible gold
            total_conflict += 1
            # find a drop that keeps M infeasible AND genuinely drops a rule
            for i in range(len(gold)):
                M = [g for j, g in enumerate(gold) if j != i]
                if not infeasible(rec, M):
                    continue
                # only a genuine drop: the removed rule must NOT be covered by a remaining
                # single constraint (else dropping it is legitimately faithful)
                if V.covers(decls, M, gold[i]):
                    continue
                naive = entailment_only_faithful(decls, M, gold)              # buggy verifier
                proper = all(r["faithful"] for r in V.audit(decls, M, gold))  # coverage-augmented
                if naive and not proper:
                    loophole_hits += 1
                    coverage_catches += 1
                    break
                if naive and proper:
                    loophole_hits += 1   # bug present but coverage missed it (should not happen)
                    break
    print(f"conflict scenarios examined:                         {total_conflict}")
    print(f"cases where entailment-only WRONGLY certifies (bug):  {loophole_hits}")
    print(f"  ...of which coverage-augmented audit() CATCHES:     {coverage_catches}")
    if loophole_hits and coverage_catches == loophole_hits:
        print("RESULT: entailment-only is incomplete on real data; coverage closes ALL of them.")
    elif loophole_hits == 0:
        print("RESULT: no infeasible drop available in these splits (need conflict records).")
    else:
        print("RESULT: WARNING - coverage missed some; investigate.")
    print()


# ---------- PATH B: repairability follows localizability ----------
def classify_localizable(detail_body):
    """A bare failure is localizable iff the model parsed to a non-empty constraint set
    that admits a violating point (status 'ok' with a violation), and non-localizable iff
    the parse was empty/unconstrained (status empty/format_error)."""
    # bare (round 0) status
    m = re.search(r"round 0: status=(\S+)", detail_body)
    if not m:
        return None
    st = m.group(1)
    if st in ("empty_after_clean", "format_error", "no_constraints"):
        return "non_localizable"
    return "localizable"

def path_B(loop_glob="runs/*_faithopt_loop.txt"):
    print("=" * 70)
    print("PATH B: repairability follows localizability")
    print("=" * 70)
    files = glob.glob(loop_glob)
    if not files:
        print("(no loop detail files found; run faithopt_loop.py first, then re-run)")
        print()
        return
    from collections import defaultdict
    tally = defaultdict(lambda: {"n": 0, "fixed": 0})
    for f in files:
        txt = open(f, encoding="utf-8").read()
        blocks = re.split(r'\n\[(T\d+-\d+c-\d+)\]', txt)
        for k in range(1, len(blocks), 2):
            body = blocks[k + 1]
            if "bare_faithful=True" in body or "initially faithful" in body:
                continue  # only count initially-bad cases
            cls = classify_localizable(body)
            if cls is None:
                continue
            fixed = "fixed=True" in body
            tally[cls]["n"] += 1
            tally[cls]["fixed"] += int(fixed)
    print(f"{'failure class':<18}{'n':>6}{'repaired':>10}{'repair rate':>14}")
    for cls in ("localizable", "non_localizable"):
        d = tally[cls]
        rate = (d["fixed"] / d["n"] * 100) if d["n"] else float("nan")
        print(f"{cls:<18}{d['n']:>6}{d['fixed']:>10}{rate:>13.1f}%")
    print("PREDICTION (Prop.): localizable repairs HIGH, non_localizable ~0.")
    print()


if __name__ == "__main__":
    path_A([
        "FaithConstraint-OR_multivariate.jsonl",
        "FaithConstraint-OR_identification.jsonl",
        "FaithConstraint-OR_multi.jsonl",
    ])
    path_B()
