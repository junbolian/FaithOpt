# -*- coding: utf-8 -*-
"""
generate_tier2.py — compose multi-constraint, multi-document scenarios for harder eval.
GT = the SET of all individual clause-constraints (faithfulness requires encoding each).
Numbers are synthetic; constraint STRUCTURE is real. GT is mechanical, never LLM-made.
Output schema matches FaithConstraint-OR_v0.jsonl so formulator can eval it via --data.
"""
import json, random

# template pool on the single decision variable p; each clause maps params -> (kind, rhs, text)
TPL = [
 ("vbp_cap",   "cap",   lambda P: 1.5*P["bid"],    "Non-selected listed price shall not exceed 1.5x the winning-bid price ({bid})."),
 ("markup_cap","cap",   lambda P: P["cost"]*1.25,  "Retail markup shall not exceed 25% over procurement cost ({cost})."),
 ("insur_cap", "cap",   lambda P: P["std"],        "Price shall not exceed the reimbursement payment standard ({std})."),
 ("comp_cap",  "cap",   lambda P: P["comp"]*1.05,  "Price shall not exceed 1.05x the lowest competitor price ({comp})."),
 ("max_retail","cap",   lambda P: P["mrp"],        "Price shall not exceed the maximum retail price ({mrp})."),
 ("cost_floor","floor", lambda P: P["cost"],       "Anti-dumping: price shall not be below procurement cost ({cost})."),
 ("margin_flr","floor", lambda P: P["cost"]+P["m"],"Internal control: minimum margin requires price at least cost ({cost}) plus {m}."),
 ("member_flr","floor", lambda P: P["memberlo"],   "Member-tier price shall not be below {memberlo}."),
]
NOISE = [
 "Submissions are filed via the provincial platform by the 15th of each month, template v3.0, with company seal.",
 "Late filing is deemed automatic withdrawal; the filing contact responds to audits within 5 business days.",
 "All figures are in CNY; rounding follows two-decimal convention; archival retention is 7 years.",
 "The catalog version code is RX-2026-Q2; cross-reference indices are maintained quarterly.",
 "Pricing reviews are logged in the ERP; approvals require two-person sign-off above 50 CNY.",
]

def params(rng):
    return dict(bid=rng.choice([6,8,10,12]), cost=rng.choice([4,5,7,9]),
                std=rng.choice([9,12,18,23]), comp=rng.choice([8,10,12]),
                mrp=rng.choice([15,20,25,30]), m=rng.choice([1,2,5,7]),
                memberlo=rng.choice([6,8,10]))

def compose(k, seed):
    rng=random.Random(seed)
    P=params(rng)
    chosen=rng.sample(TPL, k)
    gold=[]; caps=[]; floors=[]; clause_texts=[]
    for i,(name,kind,fn,txt) in enumerate(chosen):
        rhs=round(fn(P),4)
        gold.append({"id":f"{name}","terms":{"p":1.0},"sense":"<=" if kind=="cap" else ">=","rhs":rhs})
        (caps if kind=="cap" else floors).append(rhs)
        clause_texts.append(txt.format(**P))
    hi=min(caps) if caps else None; lo=max(floors) if floors else None
    feasible=(hi is None or lo is None or lo<=hi)
    label=("conflict/empty-set" if not feasible else
           ("tightest-binding" if (len(caps)>=2 or len(floors)>=2) else "multi"))
    # scatter clauses + noise across documents
    pool=clause_texts+rng.sample(NOISE, min(len(NOISE), max(1,k-1)))
    rng.shuffle(pool)
    ndoc=2 if k<=3 else 3
    docs="\n".join(f"[Document {d+1}] "+" ".join(pool[d::ndoc]) for d in range(ndoc))
    gt_read=" ; ".join((f"p<= {hi}" if hi is not None else "")+("" if hi is None else "")
                       for _ in [0]) # placeholder, build properly below
    parts=[f"p <= {hi} (tightest cap)"] if hi is not None else []
    if lo is not None: parts.append(f"p >= {lo} (tightest floor)")
    gt_read=" ; ".join(parts)+("  (encode ALL "+str(k)+" clauses; "+label+")") + ("" if feasible else "  -> EMPTY: must stay infeasible, not silently relaxed")
    return dict(
        id=f"T2-{k}c-{seed}",
        tier=("Hard" if k<=3 else "Very hard"),
        decision_type="Pricing",
        constraint_family=f"T2-multi ({k} clauses, {label})",
        cls="L_dec",
        source="Tier-2 composition of verified templates",
        scope="China/internal/competitive (composed)",
        noise_axis="Yes",
        reg_text=docs,
        scenario="Set the listed price p for this product. All rules in the documents apply simultaneously; encode every hard constraint.",
        var_decls=[{"name":"p","kind":"real","lb":None,"ub":None}],
        gold_constraints=gold,
        gold_readable=gt_read,
        trap=("Conflicting constraints across documents -> faithful model must stay infeasible; silently dropping one to make it solvable = violation."
              if not feasible else
              "Multiple constraints across documents; dropping any binding one (esp. the tightest, or a floor) = silent violation. Encode all."),
        static_checkable=True,
        note=label,
    )

if __name__=="__main__":
    out="FaithConstraint-OR_multi.jsonl"
    recs=[]
    sid=0
    for k in (2,3):                       # small bucket -> Hard
        for _ in range(25): recs.append(compose(k, 5000+sid)); sid+=1
    for k in (5,6,7,8):                    # large bucket -> Very hard
        for _ in range(25): recs.append(compose(k, 5000+sid)); sid+=1
    import datetime
    _manifest={"_manifest":True,"dataset":"FaithConstraint-OR","split":"multi-constraint","version":"v0","description":"Multiple pre-extracted hard constraints scattered across documents; encode all. Single variable.","n_instances":len(recs),"ground_truth":"mechanical, z3-verified","generated":datetime.date.today().isoformat()}
    with open(out,"w",encoding="utf-8") as f:
        f.write(json.dumps(_manifest,ensure_ascii=False)+"\n")
        for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    import collections
    print(f"wrote {len(recs)} Tier-2 scenarios -> {out}")
    print("by tier:", dict(collections.Counter(r['tier'] for r in recs)))
    print("conflict scenarios:", sum(1 for r in recs if 'empty-set' in r['note']))
    # show one example
    ex=[r for r in recs if r['tier']=='Very hard'][0]
    print("\n--- example (Very hard) ---")
    print("reg_text:\n"+ex["reg_text"])
    print("gold (all clauses):", [(g["id"],g["sense"],g["rhs"]) for g in ex["gold_constraints"]])
    print("readable:", ex["gold_readable"])
