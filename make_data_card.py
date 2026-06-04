# -*- coding: utf-8 -*-
"""make_data_card.py - scan the FaithConstraint-OR splits and emit DATA_CARD.md,
a human-readable identity record (instance counts, distribution, conflicts, manifest)."""
import json, glob, os, datetime

SPLITS = ["single","multi","identification","multivariate"]

def load(fn):
    recs=[]; man=None
    for l in open(fn,encoding="utf-8"):
        if not l.strip(): continue
        r=json.loads(l)
        if r.get("_manifest"): man=r
        else: recs.append(r)
    return man, recs

def card():
    lines=["# FaithConstraint-OR - Data Card","",
           f"Generated: {datetime.date.today().isoformat()}","",
           "One benchmark, four splits, evaluated separately (do **not** merge). All gold",
           "constraints are mechanically generated and z3-verified; numbers are synthetic,",
           "constraint **structure** is real (regulated pharmacy pricing). Each `.jsonl` begins",
           "with a `_manifest` line carrying the split's identity; data readers skip it.","",
           "| Split (file) | n | #vars | conflicts | cross-var | what it tests |",
           "|---|---|---|---|---|---|"]
    for sp in SPLITS:
        fn=f"FaithConstraint-OR_{sp}.jsonl"
        if not os.path.exists(fn): continue
        man,recs=load(fn)
        nvar=sorted({len(r["var_decls"]) for r in recs})
        nconf=sum(1 for r in recs if "conflict" in r.get("constraint_family","") or "empty-set" in r.get("note",""))
        ncross=sum(1 for r in recs if any(len(g["terms"])>1 for g in r["gold_constraints"]))
        tests={"single":"single pre-extracted constraint (baseline)",
               "multi":"multiple pre-extracted constraints; encode all",
               "identification":"identify constraints among numbered noise, then encode",
               "multivariate":"two prices p1,p2 with cross-variable constraints + conflicts"}[sp]
        lines.append(f"| `{fn}` | {len(recs)} | {nvar} | {nconf} | {ncross} | {tests} |")
    lines += ["","## Per-split manifest",""]
    for sp in SPLITS:
        fn=f"FaithConstraint-OR_{sp}.jsonl"
        if not os.path.exists(fn): continue
        man,_=load(fn)
        lines.append(f"### {sp}  (`{fn}`)")
        if man:
            lines.append("```json"); lines.append(json.dumps(man,ensure_ascii=False,indent=2)); lines.append("```")
        else:
            lines.append("_(no manifest line found)_")
        lines.append("")
    lines += ["## Notes",
              "- `single` includes 3 intentional out-of-scope marker instances (missing-value /",
              "  temporal / discrete-ladder) with empty gold; exclude them from main violation stats.",
              "- `multivariate` includes over-determined-conflict instances probing the empty-set loophole.",
              "- Scope: linear constraints (incl. multivariate). Non-linear / logical = future work.",
              "- To scale a split: raise the loop counts in the matching `generate_*.py` and re-run;",
              "  ground truth is re-verified automatically and the manifest count updates."]
    open("DATA_CARD.md","w",encoding="utf-8").write("\n".join(lines))
    print("wrote DATA_CARD.md")

if __name__=="__main__":
    card()
