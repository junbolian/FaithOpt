# -*- coding: utf-8 -*-
"""
generate_tier4_multivar.py (v1, harder) - MULTI-VARIABLE faithfulness with real
identification difficulty. Two product prices p1, p2.

Difficulty levers the verifier actually catches (drop / mis-compute of real constraints):
  * cross-variable constraints phrased SUBTLY in prose (easy to miss the coupling),
  * DERIVED rhs values that must be COMPUTED (easy to mis-compute),
  * more constraints (up to 8) buried among heavier non-constraint noise.
GT is mechanical and z3-verified; numbers synthetic; structure real.
"""
import json, random

def MULTI():
    return [
 ("budget_cap", lambda P: ({"p1":1.0,"p2":1.0}, "<=", float(P["u"]*5),
    "When products A and B are dispensed together, the amount the patient pays for the pair is "
    "capped at five times the reference unit ({u}).")),
 ("basket_cap", lambda P: ({"p1":2.0,"p2":1.0}, "<=", float(P["K"]),
    "Under the combo-pack rule, two units of A bundled with one unit of B may be billed at no "
    "more than {K} in total.")),
 ("order_rel",  lambda P: ({"p1":1.0,"p2":-1.0}, "<=", 0.0,
    "Therapeutic-equivalence policy requires that the lower-tier product A never carry a higher "
    "shelf price than the reference product B.")),
 ("agg_margin", lambda P: ({"p1":float(P["q1"]),"p2":float(P["q2"])}, ">=", float(P["R"]),
    "Over the expected monthly mix ({q1} of A and {q2} of B), the pair must bring in combined "
    "revenue of at least {R} to clear the category margin floor.")),
 ("sum_floor",  lambda P: ({"p1":1.0,"p2":1.0}, ">=", float(P["sf"]),
    "Anti-dumping review flags the pair if their combined listed price falls under {sf}.")),
    ]
def SINGLE():
    return [
 ("a_cap",   lambda P: ({"p1":1.0}, "<=", float(P["ca"]), "Product A is itself subject to a standalone ceiling of {ca}.")),
 ("b_cap",   lambda P: ({"p2":1.0}, "<=", float(P["cb"]), "Product B may not be listed above {cb}.")),
 ("a_floor", lambda P: ({"p1":1.0}, ">=", float(P["fa"]), "Product A must not be sold below {fa}.")),
 ("b_floor", lambda P: ({"p2":1.0}, ">=", float(P["fb"]), "Product B carries a price floor of {fb}.")),
    ]
NOISE = [
 "This policy applies to prescription products in therapeutic class C7 across all outlets.",
 "Price filings are submitted via the provincial platform by the 15th of each month, template v3.0.",
 "'Reference unit' denotes the median pack size recorded in the Q2 catalog.",
 "Shelf facings for the pair must not exceed four units of width in the display planogram.",
 "Delivery lead time for restock is capped at three business days under the supplier SLA.",
 "Adjustments above 50 CNY require two-person sign-off recorded in the ERP.",
 "Audit responses are due to the compliance contact within five business days.",
 "Violations may trigger delisting and an administrative review within 30 business days.",
 "The category is reviewed for competitiveness on the first business day of each quarter.",
 "All monetary figures are in CNY, rounded to two decimal places.",
]

def params(rng):
    u=rng.choice([4,5,6,7])
    return dict(u=u, K=rng.choice([34,40,46]),
        q1=rng.choice([2,3,4]), q2=rng.choice([2,3,5]),
        R=rng.choice([60,76,90,109]), sf=rng.choice([12,14,16]),
        ca=rng.choice([12,15,18,20]), cb=rng.choice([14,16,20,24]),
        fa=rng.choice([5,6,7]), fb=rng.choice([6,7,8]))

def feasible(gold):
    import z3
    s=z3.Solver(); zv={"p1":z3.Real("p1"),"p2":z3.Real("p2")}
    for g in gold:
        lhs=z3.Sum([c*zv[n] for n,c in g["terms"].items()])
        s.add(lhs<=g["rhs"] if g["sense"]=="<=" else (lhs>=g["rhs"] if g["sense"]==">=" else lhs==g["rhs"]))
    return s.check()==z3.sat

def compose(k, seed):
    rng=random.Random(seed); P=params(rng)
    multi, single = MULTI(), SINGLE()
    n_multi=rng.randint(2, min(4,k)); n_single=k-n_multi
    chosen=rng.sample(multi, n_multi)+rng.sample(single, min(n_single,len(single)))
    gold=[]; items=[]
    for name,fn in chosen:
        terms,sense,rhs,txt=fn(P)
        gold.append({"id":name,"terms":terms,"sense":sense,"rhs":round(rhs,4)})
        items.append(txt.format(**P))
    feas=feasible(gold)
    label="conflict/empty-set" if not feas else "multivar-coupled"
    n_noise=min(len(NOISE), k+4)
    allitems=items+rng.sample(NOISE,n_noise); rng.shuffle(allitems)
    numbered="\n".join(f"{i+1}. {t}" for i,t in enumerate(allitems))
    gt=", ".join(f"{g['terms']} {g['sense']} {g['rhs']}" for g in gold)
    return dict(
        id=f"T4-{k}c-{seed}", tier=("Hard" if k<=3 else "Very hard"),
        decision_type="Pricing (multi-product)",
        constraint_family=f"T4-multivar ({k} constraints incl. cross-variable, {label})",
        cls="L_dec", source="Tier-4 multivariate composition (v1, harder)",
        scope="China/internal/competitive (composed, 2-product)", noise_axis="Yes",
        reg_text="MULTI-PRODUCT PRICING POLICY (products A and B; let p1 = price of A, p2 = price of B). "
                 "The numbered items below mix genuine hard pricing constraints (some couple p1 and p2, "
                 "some require computing a value) with procedural, definitional, and non-price items. "
                 "Identify EVERY genuine price constraint and encode all of them:\n\n"+numbered,
        scenario="Set prices p1 (product A) and p2 (product B). Read every numbered item, compute any "
                 "derived bound to a number, encode every genuine price constraint (including ones that "
                 "couple p1 and p2), and ignore non-constraint items.",
        var_decls=[{"name":"p1","kind":"real","lb":None,"ub":None},
                   {"name":"p2","kind":"real","lb":None,"ub":None}],
        gold_constraints=gold,
        gold_readable=gt+f"  ({k} constraints, {label})"+("" if feas else "  -> EMPTY: must stay infeasible"),
        trap=("Cross-variable constraints phrased subtly in prose; some bounds derived (must compute); "
              "buried among more numerous non-price items. Missing the coupling, mis-computing a derived "
              "bound, or dropping a real constraint is a silent violation."),
        static_checkable=True, note=label,
    )

if __name__=="__main__":
    out="FaithConstraint-OR_multivariate.jsonl"
    recs=[]; sid=0
    for k in (3,):
        for _ in range(30): recs.append(compose(k, 9000+sid)); sid+=1
    for k in (5,6,7,8):
        for _ in range(30): recs.append(compose(k, 9000+sid)); sid+=1
    import datetime
    _manifest={"_manifest":True,"dataset":"FaithConstraint-OR","split":"multivariate","version":"v0","description":"Two prices p1,p2 with cross-variable constraints, derived bounds, noise; incl. over-determined conflicts.","n_instances":len(recs),"ground_truth":"mechanical, z3-verified","generated":datetime.date.today().isoformat()}
    with open(out,"w",encoding="utf-8") as f:
        f.write(json.dumps(_manifest,ensure_ascii=False)+"\n")
        for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    import collections
    print(f"wrote {len(recs)} harder Tier-4 scenarios -> {out}")
    print("by tier:", dict(collections.Counter(r['tier'] for r in recs)))
    print("conflict:", sum(1 for r in recs if 'empty-set' in r['note']))
    ex=[r for r in recs if r['tier']=='Very hard'][0]
    print("\n--- example ---\n"+ex["reg_text"][:850])
    print("\ngold:", [(g["terms"],g["sense"],g["rhs"]) for g in ex["gold_constraints"]])
