# -*- coding: utf-8 -*-
"""generate_highdim.py  --  high-dimensional split with NOISE + CONFLICTS (reviewer #5).

Generates k-variable (k = 5/8/10) instances in the FaithConstraint-OR .jsonl format whose
gold is mechanically generated and z3-verified. Each instance:
  * presents a NUMBERED policy list that MIXES real coupling constraints with procedural/
    definitional NOISE items (the model must identify which items are constraints), and
  * a configurable fraction are OVER-DETERMINED CONFLICTS (jointly infeasible gold), to probe
    the empty-set loophole at high dimension.
Tests whether the identification- and conflict-driven failure modes (not just clean coupling)
persist as the number of coupled variables grows.

  python generate_highdim.py --out FaithConstraint-OR_highdim.jsonl --per-k 20 --seed 0
  # then (needs API), run several models, e.g.:
  python formulator.py --model claude-haiku-4-5-20251001 --data FaithConstraint-OR_highdim.jsonl --workers 12
  python formulator.py --model gpt-4o-2024-11-20          --data FaithConstraint-OR_highdim.jsonl --workers 12
  python formulator.py --model gpt-5.4-2026-03-05         --data FaithConstraint-OR_highdim.jsonl --workers 12

Pure z3 + random, no API.
"""
import json, argparse, random
from z3 import Real, Solver, sat

NOISE = [
    "prices are reviewed on a quarterly basis",
    "all monetary figures are stated in CNY",
    "the product basket is defined by the merchandising team",
    "promotional periods follow the corporate calendar",
    "unit prices are rounded to two decimals at checkout",
    "the pricing committee convenes monthly to review performance",
    "historical transaction records are archived for five years",
    "store managers may submit exception requests in writing",
    "the assortment is refreshed at the start of each season",
    "price tags must display the applicable tax category",
]

def feasible(k, cons):
    s = Solver(); X = [Real(f"p{i+1}") for i in range(k)]
    for terms, se, r in cons:
        e = sum(c * X[i] for i, c in terms.items())
        s.add(e <= r if se == "<=" else e >= r if se == ">=" else e == r)
    return s.check() == sat

def make_instance(k, idx, rng, n_noise, conflict):
    for _ in range(400):
        cons, readable = [], []
        B = rng.randint(8 * k, 14 * k)                       # budget cap: sum p_i <= B
        F = rng.randint(3 * k, 6 * k)                        # sum floor:  sum p_i >= F
        if conflict:
            F = B + rng.randint(2, 6)                        # force F > B  => infeasible
        cons.append(({i: 1.0 for i in range(k)}, "<=", float(B)))
        readable.append(f"the total of all {k} unit prices must not exceed {B}")
        cons.append(({i: 1.0 for i in range(k)}, ">=", float(F)))
        readable.append(f"the total of all unit prices must be at least {F}")
        w = {i: float(rng.randint(1, 4)) for i in range(k)}  # weighted basket margin
        Mrhs = rng.randint(int(2.0 * sum(w.values())), int(5.0 * sum(w.values())))
        cons.append((dict(w), ">=", float(Mrhs)))
        readable.append("the weighted basket "
                        + " + ".join(f"{int(w[i])}*p{i+1}" for i in range(k))
                        + f" must be at least {Mrhs}")
        pairs = rng.sample([(a, b) for a in range(k) for b in range(k) if a != b],
                           min(k // 2 + 1, 4))               # pairwise relative: p_a <= p_b
        for (a, b) in pairs:
            cons.append(({a: 1.0, b: -1.0}, "<=", 0.0))
            readable.append(f"p{a+1} must not exceed p{b+1}")

        feas = feasible(k, cons)
        if (conflict and not feas) or ((not conflict) and feas):
            ids = (["budget_cap", "sum_floor", "basket_margin"]
                   + [f"rel_{a+1}_{b+1}" for (a, b) in pairs])
            gold = [{"id": ids[j], "terms": {f"p{i+1}": c for i, c in terms.items()},
                     "sense": se, "rhs": r}
                    for j, (terms, se, r) in enumerate(cons)]
            var_decls = [{"name": f"p{i+1}", "kind": "real", "lb": None, "ub": None}
                         for i in range(k)]
            noise_items = rng.sample(NOISE, min(n_noise, len(NOISE)))
            items = [("rule", s) for s in readable] + [("noise", s) for s in noise_items]
            rng.shuffle(items)
            numbered = "\n".join(f"  ({n+1}) {txt}." for n, (kind, txt) in enumerate(items))
            reg = (f"A retailer sets unit prices {', '.join(f'p{i+1}' for i in range(k))} (in CNY) "
                   f"for a basket of products. The pricing policy lists the items below; identify "
                   f"which are hard pricing constraints and encode ALL of them (some items are "
                   f"procedures or definitions and are not constraints):\n{numbered}")
            return {
                "id": f"HD-{k}v-{('cf' if conflict else 'cl')}-{idx:04d}",
                "tier": "Very hard",
                "constraint_family": f"HD-{k}var-{'conflict' if conflict else 'coupled'}",
                "scope": "highdim",
                "noise_axis": f"{len(noise_items)} distractor items",
                "reg_text": reg,
                "scenario": f"{k}-product basket pricing; coupled rules amid procedural noise"
                            + ("; over-determined (infeasible) gold" if conflict else ""),
                "var_decls": var_decls,
                "gold_constraints": gold,
                "gold_readable": "; ".join(readable),
                "trap": ("over-determined conflict at high dimension" if conflict
                         else "cross-variable coupling amid noise at high dimension"),
                "static_checkable": True,
                "note": "conflict" if conflict else "highdim-coupled",
            }
    raise RuntimeError("could not build the requested instance")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="FaithConstraint-OR_highdim.jsonl")
    ap.add_argument("--ks", nargs="+", type=int, default=[5, 8, 10])
    ap.add_argument("--per-k", type=int, default=20)
    ap.add_argument("--noise", type=int, default=3, help="distractor items per instance")
    ap.add_argument("--conflict-frac", type=float, default=0.25,
                    help="fraction of instances with over-determined (infeasible) gold")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    instances, n_conf = [], 0
    for k in args.ks:
        for j in range(args.per_k):
            conflict = (rng.random() < args.conflict_frac)
            n_conf += int(conflict)
            nz = max(1, args.noise + rng.randint(-1, 1))
            instances.append(make_instance(k, j, rng, nz, conflict))

    manifest = {
        "_manifest": True, "dataset": "FaithConstraint-OR", "split": "highdim",
        "version": "hd-1",
        "description": f"high-dimensional coupling + noise + conflicts, k in {args.ks}",
        "n_instances": len(instances), "ground_truth": "mechanical + z3-verified",
        "generated": "generate_highdim.py", "n_conflict_instances": n_conf,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        for inst in instances:
            f.write(json.dumps(inst, ensure_ascii=False) + "\n")

    by_k = {k: sum(1 for i in instances if i["constraint_family"].startswith(f"HD-{k}var"))
            for k in args.ks}
    print(f"wrote {len(instances)} instances to {args.out}")
    print(f"  per k: {by_k}   conflicts: {n_conf}/{len(instances)}   noise: ~{args.noise}/instance")
    print("  sanity: clean gold verified FEASIBLE, conflict gold verified INFEASIBLE by z3.")
