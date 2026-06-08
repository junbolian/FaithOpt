# -*- coding: utf-8 -*-
"""llm_judge_baseline.py  --  empirical LLM-as-judge baseline (reviewer #3).

For each instance: a generator LLM produces a formulation M; the FORMAL verifier gives
the ground-truth verdict (audit against z3-checked gold); a separate JUDGE LLM is shown the
SAME policy text + M and asked "faithful or not?". We then measure how often the judge MISSES
a real violation (says faithful when the verifier proves it unfaithful) -- the silent failures a
model-opinion check would pass -- and its false-alarm rate. This quantifies why the verifier's
verdict (a soundness-backed procedure) is not interchangeable with another model's opinion.

Needs an OpenAI-compatible endpoint (same as formulator). Example:

  export GLOBALAI_KEY=sk-...
  python llm_judge_baseline.py --gen-model gpt-4o-2024-11-20 --judge-model gpt-4o-2024-11-20 \
      --data FaithConstraint-OR_identification.jsonl --workers 8
  # try a strong judge too, e.g. --judge-model gpt-5.4-2026-03-05
"""
import json, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import formulator as FB
from faithopt_verifier import audit

_LOCK = threading.Lock()   # z3 is not thread-safe

def judge_prompt(reg_text, M):
    if M:
        cons = "\n".join(
            f"  {i+1}. {('+'.join(f'{c}*{v}' for v, c in cc.terms.items()))} {cc.sense} {cc.rhs}"
            for i, cc in enumerate(M))
    else:
        cons = "  (the model encodes no constraints)"
    return (
        "You are auditing whether an optimization model faithfully encodes ALL hard numeric "
        "constraints stated in a policy. You are given the policy text and the model's constraints.\n\n"
        f"POLICY:\n{reg_text}\n\n"
        f"MODEL CONSTRAINTS:\n{cons}\n\n"
        "Does the model encode EVERY genuine hard constraint in the policy with none dropped, "
        "loosened, or mis-encoded? Some policy items may be procedures or definitions (ignore those); "
        "every genuine numeric bound must be present and correct.\n"
        "Answer with exactly one word on the first line: FAITHFUL or UNFAITHFUL.")

def parse_verdict(raw):
    t = (raw or "").strip().upper()
    head = t[:60]
    if "UNFAITHFUL" in head: return "unfaithful"
    if "FAITHFUL" in head:   return "faithful"
    return "unparsed"

def one(rec, gen_model, judge_model):
    decls, gold = FB.gold_of(rec)
    raw = FB.call_real_llm(gen_model, FB.build_prompt(rec, False))
    M, status = FB.parse_model(raw, rec)
    if status not in ("ok", "ok_dropped_unknown"):
        return None
    with _LOCK:
        rep = audit(decls, M, gold)
    formal_unfaithful = any(r["faithful"] is False for r in rep)
    jraw = FB.call_real_llm(judge_model, judge_prompt(rec["reg_text"], M))
    jv = parse_verdict(jraw)
    if jv == "unparsed":
        return ("unparsed", formal_unfaithful)
    return (jv, formal_unfaithful)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen-model", required=True, help="model that PRODUCES the formulation")
    ap.add_argument("--judge-model", required=True, help="LLM-as-judge")
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    records = [r for r in (json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip())
               if not r.get("_manifest") and r.get("static_checkable")]
    if args.limit:
        records = records[:args.limit]
    print(f"loaded {len(records)} records; gen={args.gen_model} judge={args.judge_model}")

    miss = fa = agree = formal_unf = n = unparsed = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(one, r, args.gen_model, args.judge_model) for r in records]
        for f in as_completed(futs):
            r = f.result(); done += 1
            print(f"\r  {done}/{len(records)}", end="", flush=True)
            if r is None: continue
            jv, formal_unfaithful = r
            if jv == "unparsed": unparsed += 1; continue
            n += 1; formal_unf += int(formal_unfaithful)
            judge_unfaithful = (jv == "unfaithful")
            if formal_unfaithful and not judge_unfaithful:   miss += 1
            elif (not formal_unfaithful) and judge_unfaithful: fa += 1
            else: agree += 1
    print()

    miss_rate = 100.0 * miss / formal_unf if formal_unf else 0.0
    print(f"\n=== LLM-as-judge ({args.judge_model}) vs formal verifier, on {args.gen_model} outputs ===")
    print(f"  models judged:                          {n}")
    print(f"  formally UNFAITHFUL (verifier truth):   {formal_unf}/{n}")
    print(f"  judge MISSES a real violation:          {miss}/{formal_unf if formal_unf else 1} "
          f"= {miss_rate:.1f}%   <-- silent failures the judge would pass")
    print(f"  judge FALSE ALARMS (truly faithful):    {fa}/{n - formal_unf if n > formal_unf else 1}")
    print(f"  judge agrees with formal verifier:      {agree}/{n} = {100.0*agree/n if n else 0:.1f}%")
    if unparsed: print(f"  (judge output unparseable on {unparsed} instances)")
