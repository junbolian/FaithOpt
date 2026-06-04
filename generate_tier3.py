# -*- coding: utf-8 -*-
"""
generate_tier3.py — single NUMBERED POLICY LIST where the model must self-identify
which items are hard constraints on price p (the rest are procedural/definition noise),
then formalize ALL of them. Closer to real deployment: nobody pre-extracts the clauses.

GT = the constraint items only (mechanical, z3-verifiable). Missing/misreading any
real constraint item (or encoding a noise item as a constraint) is a faithfulness failure.
Schema matches the other splits so formulator can eval via --data.
"""
import json, random

# constraint templates on the single decision variable p -> (kind, rhs, full clause text)
TPL = [
 ("vbp_cap",   "cap",   lambda P: 1.5*P["bid"],    "The listed price of a non-selected product shall not exceed 1.5 times the winning-bid price ({bid})."),
 ("markup_cap","cap",   lambda P: P["cost"]*1.25,  "Retail price shall not exceed procurement cost ({cost}) by more than a 25% markup."),
 ("insur_cap", "cap",   lambda P: P["std"],        "The sale price shall not exceed the reimbursement payment standard ({std})."),
 ("comp_cap",  "cap",   lambda P: P["comp"]*1.05,  "The sale price shall not exceed 1.05 times the lowest competitor price ({comp})."),
 ("max_retail","cap",   lambda P: P["mrp"],        "The sale price shall not exceed the maximum retail price ({mrp})."),
 ("cost_floor","floor", lambda P: P["cost"],       "The sale price shall not be set below procurement cost ({cost}) (anti-dumping)."),
 ("margin_flr","floor", lambda P: P["cost"]+P["m"],"Internal control requires a minimum gross margin: price at least procurement cost ({cost}) plus {m}."),
 ("member_flr","floor", lambda P: P["memberlo"],   "The member-tier price shall not be below {memberlo}."),
]

# NON-constraint items: definitions, scope, procedure, penalties, dates. The model must IGNORE these.
NOISE = [
 "This policy applies to all prescription pharmaceutical products sold at company retail outlets.",
 "Price filings are submitted via the provincial platform by the 15th of each calendar month using template v3.0.",
 "Late filing is deemed an automatic withdrawal of the price application.",
 "All monetary figures in this document are denominated in CNY and rounded to two decimal places.",
 "The product catalog version code is RX-2026-Q2; cross-reference indices are maintained on a quarterly basis.",
 "Pricing changes are recorded in the ERP system; adjustments above 50 CNY require two-person sign-off.",
 "'Procurement cost' is defined as the most recent invoiced unit purchase price net of rebates.",
 "Violations of this policy may result in delisting and an administrative review within 30 business days.",
 "The pricing committee meets on the first business day of each quarter to review exceptions.",
 "Audit responses must be provided to the compliance contact within 5 business days of a request.",
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
    gold=[]; caps=[]; floors=[]; constraint_items=[]
    for name,kind,fn,txt in chosen:
        rhs=round(fn(P),4)
        gold.append({"id":name,"terms":{"p":1.0},"sense":"<=" if kind=="cap" else ">=","rhs":rhs})
        (caps if kind=="cap" else floors).append(rhs)
        constraint_items.append(txt.format(**P))
    hi=min(caps) if caps else None; lo=max(floors) if floors else None
    feasible=(hi is None or lo is None or lo<=hi)
    label=("conflict/empty-set" if not feasible else
           ("tightest-binding" if (len(caps)>=2 or len(floors)>=2) else "multi"))
    # interleave constraint items with MORE noise than constraints, as one numbered list
    n_noise=min(len(NOISE), k+2)            # noise outnumbers constraints -> identification is real
    items=constraint_items+rng.sample(NOISE, n_noise)
    rng.shuffle(items)
    numbered="\n".join(f"{i+1}. {t}" for i,t in enumerate(items))
    parts=[f"p <= {hi} (tightest cap)"] if hi is not None else []
    if lo is not None: parts.append(f"p >= {lo} (tightest floor)")
    gt_read=" ; ".join(parts)+f"  (identify & encode the {k} real constraint items among {len(items)} numbered items; {label})"+("" if feasible else "  -> EMPTY: must stay infeasible")
    return dict(
        id=f"T3-{k}c-{seed}",
        tier=("Hard" if k<=3 else "Very hard"),
        decision_type="Pricing",
        constraint_family=f"T3-fulllist ({k} of {len(items)} items are constraints, {label})",
        cls="L_dec",
        source="Tier-3 numbered-policy-list composition",
        scope="China/internal/competitive (composed)",
        noise_axis="Yes",
        reg_text="PRICING POLICY (excerpt). Read every numbered item and identify which are hard pricing constraints:\n\n"+numbered,
        scenario="Set the listed price p. The policy below is a numbered list mixing hard constraints with procedural and definitional items. Identify EVERY item that constrains the price and encode all of them; ignore non-constraint items.",
        var_decls=[{"name":"p","kind":"real","lb":None,"ub":None}],
        gold_constraints=gold,
        gold_readable=gt_read,
        trap=("Real constraints are mixed with more numerous non-constraint items (definitions, procedures, penalties, dates). "
              "Failing to recognize an item as a constraint, or encoding a noise item as one, is a silent faithfulness failure. "
              + ("Constraints conflict -> a faithful model stays infeasible; silently dropping one to make it solvable is a violation." if not feasible else "")),
        static_checkable=True,
        note=label,
    )

if __name__=="__main__":
    out="FaithConstraint-OR_tier3_v0.jsonl"
    recs=[]; sid=0
    for k in (2,3):
        for _ in range(8): recs.append(compose(k, 7000+sid)); sid+=1
    for k in (5,6,7,8):
        for _ in range(6): recs.append(compose(k, 7000+sid)); sid+=1
    with open(out,"w",encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    import collections
    print(f"wrote {len(recs)} Tier-3 numbered-list scenarios -> {out}")
    print("by tier:", dict(collections.Counter(r['tier'] for r in recs)))
    print("conflict:", sum(1 for r in recs if 'empty-set' in r['note']))
    ex=[r for r in recs if r['tier']=='Very hard'][0]
    print("\n--- example (Very hard) ---")
    print(ex["reg_text"])
    print("\ngold (the real constraints):", [(g["sense"],g["rhs"]) for g in ex["gold_constraints"]])
