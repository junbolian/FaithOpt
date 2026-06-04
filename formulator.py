# -*- coding: utf-8 -*-
"""
formulator.py - FaithOpt bare-LLM evaluation harness
=====================================================================
Per dataset record:  build prompt -> LLM returns JSON model M -> parse M to LinCon
  -> verify each gold constraint c via entails(M, c)  [faithopt_verifier]
  -> aggregate silent-violation rate by tier x family, across models.

This is the BARE-LLM measurement (problem-exists evidence). The FaithOpt
verify->repair LOOP is a separate step (proves the method fixes it).

Measurement notes
  * dumps every raw LLM output to  runs/<model>/<id>.txt  (human-checkable)
  * classifies parse failures and COUNTS real failures as violations
        - format_error      : unparseable JSON / wrong shape           -> counts as FAIL
        - empty_after_clean : parsed but no usable constraints left     -> counts as FAIL
        - symbolic_unresolved: bounds left symbolic (e.g. 'PARAM:..') not computed -> FAIL
        - undeclared_var    : model used a variable not in var_decls    -> counts as FAIL
    (the prompt requires every rhs to be a concrete number; the text supplies all params)
  * --workers N        : concurrent API calls (default 1)
  * --neutral-prompt   : minimal prompt (no var list, no 'encode EVERY constraint' hint)
  * --model A B C ...  : run several models in one go -> cross-model summary table
  * --mock             : built-in mock LLM, no API key

Usage
  set GLOBALAI_KEY env var first.
  python formulator.py --mock
  python formulator.py --model claude-opus-4-7 deepseek-v3.2 gpt-4o-2024-11-20 --workers 8
  python formulator.py --model claude-opus-4-7 --neutral-prompt --workers 8
"""
import json, argparse, collections, random, os, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from faithopt_verifier import Var, LinCon, audit

import ast as _ast, operator as _op
_OPS={_ast.Add:_op.add,_ast.Sub:_op.sub,_ast.Mult:_op.mul,_ast.Div:_op.truediv,
      _ast.USub:_op.neg,_ast.UAdd:_op.pos,_ast.Pow:_op.pow}
def _safe_num(x):
    """Accept a number, or a STRING arithmetic expression like '24*1.50' or '120+0.05*30'.
    Only numbers and + - * / ( ) ** allowed. Returns float, or None if not numeric."""
    if isinstance(x,(int,float)): return float(x)
    if not isinstance(x,str): return None
    s=x.strip()
    if s.lower().startswith("param:") or s=="" : return None   # symbolic / empty -> not numeric
    try:
        node=_ast.parse(s,mode="eval").body
        def ev(n):
            if isinstance(n,_ast.Num): return n.n
            if isinstance(n,_ast.Constant) and isinstance(n.value,(int,float)): return n.value
            if isinstance(n,_ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left),ev(n.right))
            if isinstance(n,_ast.UnaryOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.operand))
            raise ValueError("disallowed")
        return float(ev(node))
    except Exception:
        return None

import re as _re
def _repair_arith_json(text):
    """LLMs sometimes emit arithmetic in JSON value positions, e.g. "rhs": 24 * 1.50,
    which makes json.loads fail. Replace any  :<arith-expr>  (before , } ]) with its value."""
    def repl(mm):
        pre, expr, post = mm.group(1), mm.group(2), mm.group(3)
        val=_safe_num(expr.strip())
        return f"{pre}{val}{post}" if val is not None else mm.group(0)
    # value tokens made of digits/operators/space/parens, ending before , } ]
    return _re.sub(r'(:\s*)([0-9][0-9eE.\s+\-*/()]*?)(\s*[,}\]])', repl, text)



DATA = "FaithConstraint-OR_single.jsonl"   # override with --data
RUNDIR = "runs"
DATA_TAG = ""   # set in __main__ from --data filename; appended to output names to avoid cross-split overwrite

# ----------------------------- prompts -----------------------------
PROMPT_FULL = """You are an optimization-modeling assistant for a pharmacy chain.
Given a manager's request and the relevant regulatory/policy text, output the
optimization model as JSON only (no prose, no markdown).

[REQUEST]
{scenario}

[REGULATORY/POLICY TEXT]
{reg_text}

[DECISION VARIABLES]
{vars}

Rules:
1) Output ONE JSON object: {{"constraints":[{{"id","terms","sense","rhs"}}]}}
   - terms is {{var_name: coefficient}} using ONLY the decision variables above,
     sense in "<=" / ">=" / "==". Parameters (cost, etc.) are NOT variables.
2) Encode EVERY hard constraint implied by the text. Do not add ungrounded constraints.
3) rhs MUST be a concrete number. The text provides every parameter you need; compute
   each bound to its final numeric value (e.g. a 25% markup over a cost of 8 gives rhs 10).
   Do NOT leave a bound symbolic and do NOT output placeholder strings for rhs.
"""

PROMPT_NEUTRAL = """A pharmacy manager asks: {scenario}

Relevant text:
{reg_text}

Write the pricing/decision model as a JSON object:
{{"constraints":[{{"id","terms","sense","rhs"}}]}}  (terms maps variable->coefficient; sense in "<=",">=","==").
Output JSON only.
"""

def build_prompt(rec, neutral):
    if neutral:
        return PROMPT_NEUTRAL.format(scenario=rec["scenario"], reg_text=rec["reg_text"])
    vlines = "\n".join(f"- {v['name']} ({v['kind']}"
                       + (f", lb={v['lb']}" if v['lb'] is not None else "")
                       + (f", ub={v['ub']}" if v['ub'] is not None else "") + ")"
                       for v in rec["var_decls"]) or "- (none specified)"
    return PROMPT_FULL.format(scenario=rec["scenario"], reg_text=rec["reg_text"], vars=vlines)

# ----------------------------- parse + classify -----------------------------
def parse_model(text, rec):
    """Return (cons, status). status in:
       ok | ok_dropped_unknown | empty_after_clean | format_error"""
    declared = {v["name"] for v in rec["var_decls"]}
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json": t = t[4:]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        return None, "format_error"
    blob = t[i:j+1]
    try:
        obj = json.loads(blob)
    except Exception:
        try:
            obj = json.loads(_repair_arith_json(blob))   # rescue arithmetic-in-JSON
        except Exception:
            return None, "format_error"
    raw_cons = obj.get("constraints", [])
    if not isinstance(raw_cons, list):
        return None, "format_error"
    cons = []; dropped_unknown = False; had_any = False; symbolic_unresolved = False
    for c in raw_cons:
        rhs = _safe_num(c.get("rhs"))     # accepts numbers AND arithmetic-expression strings (e.g. "24*1.50")
        if rhs is None:                   # symbolic (PARAM:) / non-numeric: model failed to compute the bound
            if str(c.get("rhs", "")).strip():   # it did try to state a constraint, just not numerically
                had_any = True
                symbolic_unresolved = True
            continue                      # not added to cons -> audit will flag the missing constraint
        try:
            terms = {k: _safe_num(v) for k, v in c["terms"].items()}
        except Exception:
            continue
        if any(v is None for v in terms.values()):
            continue
        had_any = True
        if not declared or any(name not in declared for name in terms):
            dropped_unknown = True        # used a non-existent variable -> formulation error
            continue
        try:
            cons.append(LinCon(str(c.get("id", "c")), terms, c["sense"], rhs))
        except Exception:
            continue
    if dropped_unknown:
        return cons, "ok_dropped_unknown"
    if not cons:
        # nothing numeric usable. Separate "tried but left bounds symbolic" from "truly empty".
        return cons, ("symbolic_unresolved" if symbolic_unresolved else "empty_after_clean")
    if symbolic_unresolved:
        # some constraints numeric, but at least one left symbolic -> partial; audit flags the gap
        return cons, "ok_symbolic_partial"
    return cons, "ok"

def gold_of(rec):
    decls = [Var(v["name"], v["kind"], lb=v["lb"], ub=v["ub"]) for v in rec["var_decls"]]
    gold  = [LinCon(g["id"], {k: float(v) for k,v in g["terms"].items()}, g["sense"], float(g["rhs"]))
             for g in rec["gold_constraints"]]
    return decls, gold

# ----------------------------- LLM callers -----------------------------
def call_real_llm(model, prompt):
    """GlobalAI (OpenAI-compatible). Set:  export GLOBALAI_KEY=sk-xxxxx"""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["GLOBALAI_KEY"],
                    base_url=os.environ.get("LLM_BASE_URL", "https://globalai.vip/v1"))
    r = client.chat.completions.create(
        model=model, max_tokens=1024, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.choices[0].message.content

def mock_llm(prompt, rec, fail_bias=0.45):
    decls, gold = gold_of(rec)
    p = {"Easy":0.02,"Medium":0.15,"Hard":fail_bias,"Very hard":fail_bias+0.2}[rec["tier"]]
    kept = []
    for g in gold:
        r = random.random()
        if r < p*0.5: continue
        elif r < p:
            rhs = g.rhs*(1.3 if g.sense=="<=" else 0.7)
            kept.append({"id":g.cid,"terms":g.terms,"sense":g.sense,"rhs":rhs})
        else:
            kept.append({"id":g.cid,"terms":g.terms,"sense":g.sense,"rhs":g.rhs})
    return json.dumps({"constraints":kept}, ensure_ascii=False)

# ----------------------------- eval one record (split: fetch vs score) -----------------------------
def fetch_raw(rec, caller, model, neutral, dump_dir):
    """Network-bound; safe to run in threads. Returns raw text + api error."""
    prompt = build_prompt(rec, neutral)
    try:
        raw = caller(prompt, rec); api_err = None
    except Exception as e:
        raw = ""; api_err = f"api_error: {e}"
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)
        with open(os.path.join(dump_dir, f"{rec['id']}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# model={model} tier={rec['tier']} family={rec['constraint_family']}\n")
            f.write(f"# gold: {rec['gold_readable']}\n\n=== PROMPT ===\n{prompt}\n\n=== RAW OUTPUT ===\n{raw}\n")
    return raw, api_err

def score_raw(rec, raw, api_err):
    """z3 verification; MUST run in main thread (z3 is not thread-safe)."""
    out = {"id":rec["id"], "tier":rec["tier"], "fam":rec["constraint_family"], "skip":False}
    if api_err:
        out.update(violated=True, status="api_error", note=api_err); return out
    decls, gold = gold_of(rec)
    M, status = parse_model(raw, rec)
    if status in ("format_error", "empty_after_clean", "symbolic_unresolved"):
        out.update(violated=True, status=status); return out
    try:
        report = audit(decls, M, gold)
    except Exception as e:
        out.update(violated=True, status=f"verify_error:{e}"); return out
    viol = [r for r in report if r["faithful"] is False]
    forced = (status == "ok_dropped_unknown")
    out.update(violated=(len(viol)>0 or forced), status=status,
               n_gold=len(gold), n_viol=len(viol)+(1 if forced else 0),
               counterexamples=[r["counterexample"] for r in viol],
               model_cons=[(c.sense, dict(c.terms), c.rhs) for c in (M or [])],
               reasons=[(r["cid"], r.get("reason")) for r in report if r["faithful"] is False])
    return out

# ----------------------------- run one model (parallel fetch, serial verify) -----------------------------
def run_model(records, model, caller, neutral, workers):
    dump_dir = os.path.join(RUNDIR, model.replace("/","_") + (f"__{DATA_TAG}" if DATA_TAG else "") + ("_neutral" if neutral else ""))
    raws = [None]*len(records); errs=[None]*len(records)
    def task(i):
        idx = records[i]
        if not idx["static_checkable"]:
            return i, None, "SKIP"
        raw, err = fetch_raw(idx, caller, model, neutral, dump_dir)
        return i, raw, err
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:   # threads only for network
        futs = [ex.submit(task, i) for i in range(len(records))]
        for fut in as_completed(futs):
            i, raw, err = fut.result(); raws[i]=raw; errs[i]=err; done+=1
            print(f"\r  {model}: fetched {done}/{len(records)}", end="", flush=True)
    print()
    # serial verification in main thread (z3-safe)
    results=[]
    for i, rec in enumerate(records):
        if not rec["static_checkable"]:
            results.append({"id":rec["id"],"tier":rec["tier"],"fam":rec["constraint_family"],"skip":True}); continue
        results.append(score_raw(rec, raws[i], errs[i]))
    by_tier = collections.defaultdict(lambda:[0,0]); by_fam = collections.defaultdict(lambda:[0,0])
    status_ct = collections.Counter(); skipped = 0
    for r in results:
        if r["skip"]: skipped += 1; continue
        status_ct[r["status"]] += 1
        v = 1 if r["violated"] else 0
        by_tier[r["tier"]][0]+=v; by_tier[r["tier"]][1]+=1
        fam = r["fam"].split("(")[0].strip(); by_fam[fam][0]+=v; by_fam[fam][1]+=1
    return {"model":model, "neutral":neutral, "by_tier":dict(by_tier), "by_fam":dict(by_fam),
            "status":dict(status_ct), "skipped":skipped, "dump_dir":dump_dir, "results":results}

# ----------------------------- print -----------------------------
ORDER = ["Easy","Medium","Hard","Very hard"]
def print_model(rep):
    print(f"\n================  {rep['model']}{'  [neutral prompt]' if rep['neutral'] else ''}  ================")
    for t in ORDER:
        if t in rep["by_tier"]:
            v,n = rep["by_tier"][t]; print(f"   {t:10s} {v}/{n} = {100*v/n:5.1f}%")
    print("  status counts:", rep["status"], f"(skipped symbolic/testable: {rep['skipped']})")
    hard = [rep["by_tier"][t] for t in ("Hard","Very hard") if t in rep["by_tier"]]
    hv=sum(x[0] for x in hard); hn=sum(x[1] for x in hard)
    if hn:
        rate=100*hv/hn
        print(f"   >>> Hard+VeryHard violation = {rate:.1f}%  ->  {'PROCEED' if rate>=15 else 'borderline/PIVOT'}")
    print(f"   raw outputs dumped to: {rep['dump_dir']}/")

def print_summary(reps):
    print("\n================  CROSS-MODEL SUMMARY (violation rate)  ================")
    hdr = "model".ljust(28) + "".join(t[:8].ljust(10) for t in ORDER) + "Hard+VH"
    print(hdr); print("-"*len(hdr))
    for rep in reps:
        row = (rep["model"]+("*" if rep["neutral"] else "")).ljust(28)
        for t in ORDER:
            if t in rep["by_tier"]:
                v,n=rep["by_tier"][t]; row += f"{100*v/n:4.0f}%({v}/{n})".ljust(10)
            else: row += "-".ljust(10)
        hard=[rep["by_tier"][t] for t in ("Hard","Very hard") if t in rep["by_tier"]]
        hv=sum(x[0] for x in hard); hn=sum(x[1] for x in hard)
        row += f"{100*hv/hn:.1f}%" if hn else "-"
        print(row)
    print("(* = neutral prompt)  Easy~0% = sanity OK; gradient across models = headline finding.")


def write_detail(rep, records):
    """Write ONE human-readable detail file per model: per-record gold/model/verdict/reason."""
    import os
    os.makedirs(RUNDIR, exist_ok=True)
    path = os.path.join(RUNDIR, rep["model"].replace("/","_") + (f"__{DATA_TAG}" if DATA_TAG else "") + ("_neutral" if rep["neutral"] else "") + "_detail.txt")
    rmap = {r["id"]: r for r in rep["results"]}
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"MODEL: {rep['model']}{'  [neutral]' if rep['neutral'] else ''}\n")
        for t in ORDER:
            if t in rep["by_tier"]:
                v,n=rep["by_tier"][t]; f.write(f"  {t}: {v}/{n} = {100*v/n:.1f}%\n")
        hard=[rep["by_tier"][t] for t in ("Hard","Very hard") if t in rep["by_tier"]]
        hv=sum(x[0] for x in hard); hn=sum(x[1] for x in hard)
        if hn: f.write(f"  Hard+VeryHard = {100*hv/hn:.1f}%\n")
        f.write(f"  status: {rep['status']}\n")
        f.write("="*80+"\n")
        for rec in records:
            r = rmap.get(rec["id"])
            if r is None or r.get("skip"): continue
            f.write(f"\n[{rec['id']}] tier={rec['tier']} family={rec['constraint_family']}\n")
            f.write(f"  gold_readable: {rec.get('gold_readable','')}\n")
            f.write(f"  gold: {[(g['sense'],g['rhs']) for g in rec['gold_constraints']]}\n")
            f.write(f"  model_encoded ({len(r.get('model_cons',[]))}): {[(s,t,rh) for (s,t,rh) in r.get('model_cons',[])]}\n")
            f.write(f"  VERDICT: {'VIOLATED' if r.get('violated') else 'faithful'}  status={r.get('status')}")
            if r.get("reasons"): f.write(f"  fail_reasons={r.get('reasons')}")
            f.write("\n")
    return path


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--model", nargs="+", default=None, help="one or more model ids")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--neutral-prompt", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--data", default=DATA, help="path to jsonl dataset")
    args=ap.parse_args()
    random.seed(args.seed)
    import os as _os
    globals()["DATA_TAG"] = _os.path.basename(args.data).replace("FaithConstraint-OR_","").replace(".jsonl","")
    records=[r for r in (json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip()) if not r.get("_manifest")]
    print(f"loaded {len(records)} records  (workers={args.workers}, prompt={'neutral' if args.neutral_prompt else 'full'})")
    reps=[]
    if args.mock:
        rep=run_model(records, "MOCK", lambda p,rec: mock_llm(p,rec), args.neutral_prompt, args.workers)
        print_model(rep); reps.append(rep); print("   detail written to:", write_detail(rep, records))
    elif args.model:
        for m in args.model:
            rep=run_model(records, m, lambda p,rec,_m=m: call_real_llm(_m,p), args.neutral_prompt, args.workers)
            print_model(rep); reps.append(rep)
            dp=write_detail(rep, records); print(f"   detail written to: {dp}")
        if len(reps)>1: print_summary(reps)
    else:
        print("nothing to run: pass --mock or --model <id> [<id> ...]")
