# -*- coding: utf-8 -*-
"""Generate over-determined conflict scenarios (>=2 independent infeasibility sources)
so the empty-set loophole is probeable on the benchmark itself. Appends to tier4."""
import json, random, z3

def infeasible(decls_json, cons):
    s=z3.Solver(); zv={v["name"]:z3.Real(v["name"]) for v in decls_json}
    for g in cons:
        lhs=z3.Sum([co*zv[n] for n,co in g["terms"].items()])
        s.add(lhs<=g["rhs"] if g["sense"]=="<=" else (lhs>=g["rhs"] if g["sense"]==">=" else lhs==g["rhs"]))
    return s.check()!=z3.sat

def make(seed):
    rng=random.Random(seed)
    a=rng.choice([20,22,26,30]); b=a+rng.choice([12,16,20])   # p1+p2<=a and >=b conflict
    d=rng.choice([8,10,12])                                    # p1-p2>=d and p2-p1>=d conflict
    gold=[
        {"id":"sum_hi","terms":{"p1":1.0,"p2":1.0},"sense":">=","rhs":float(b)},
        {"id":"sum_lo","terms":{"p1":1.0,"p2":1.0},"sense":"<=","rhs":float(a)},
        {"id":"gap_a","terms":{"p1":1.0,"p2":-1.0},"sense":">=","rhs":float(d)},
        {"id":"gap_b","terms":{"p1":-1.0,"p2":1.0},"sense":">=","rhs":float(d)},
    ]
    decls=[{"name":"p1","kind":"real","lb":0.0,"ub":100.0},
           {"name":"p2","kind":"real","lb":0.0,"ub":100.0}]
    assert infeasible(decls,gold)
    # confirm over-determined: dropping gap_b keeps it infeasible
    assert infeasible(decls,[g for g in gold if g["id"]!="gap_b"])
    items=[f"The combined price of A and B must be at least {b}.",
           f"The combined price of A and B must not exceed {a}.",
           f"A must exceed B by at least {d}.",
           f"B must exceed A by at least {d}.",
           "Filings use the provincial template v3.0 by the 15th.",
           "All amounts are in CNY, two decimals."]
    rng.shuffle(items)
    numbered="\n".join(f"{i+1}. {t}" for i,t in enumerate(items))
    return dict(
        id=f"T4-OD-{seed}", tier="Very hard",
        decision_type="Pricing (multi-product)",
        constraint_family="T4-multivar (4 constraints, over-determined conflict/empty-set)",
        cls="L_dec", source="Tier-4 over-determined conflict (loophole probe)",
        scope="China/internal (composed, 2-product)", noise_axis="Yes",
        reg_text="MULTI-PRODUCT PRICING POLICY (p1=price of A, p2=price of B). Identify EVERY "
                 "genuine price constraint and encode all of them:\n\n"+numbered,
        scenario="Encode every genuine price constraint; the policy is jointly infeasible and "
                 "must stay infeasible (do not silently drop a rule to make it solvable).",
        var_decls=decls, gold_constraints=gold,
        gold_readable="OVER-DETERMINED conflict (2 independent sources) -> EMPTY: must stay infeasible",
        trap="Two independent conflicts; dropping one rule leaves it infeasible, hiding the drop "
             "from entailment-only checking (empty-set loophole). Coverage must catch it.",
        static_checkable=True, note="conflict/empty-set")

if __name__=="__main__":
    new=[make(7700+i) for i in range(6)]
    with open("FaithConstraint-OR_multivariate.jsonl","a",encoding="utf-8") as f:
        for r in new: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(f"appended {len(new)} over-determined conflict records to multivariate")
