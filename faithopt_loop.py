# -*- coding: utf-8 -*-
"""
faithopt_loop.py - the FaithOpt verify->repair->re-verify loop (the METHOD).
=============================================================================
Bare LLM (round 0) often silently violates / drops hard constraints.
FaithOpt closes the loop:

   formulate -> AUDIT (sound + coverage) -> if unfaithful, build FEEDBACK
   (a concrete counterexample + WHICH constraint is violated or not encoded,
    WITHOUT leaking the gold numeric bounds) -> LLM REPAIRS -> re-AUDIT ...
   up to --max-rounds, stopping as soon as the model is faithful.

Outputs: bare-LLM vs FaithOpt violation rate, average repair rounds, and a
per-record detail. The feedback never reveals gold rhs values, so improvement
reflects the LLM re-reading the text under verifier pressure, not answer leakage.

Usage:
  python faithopt_loop.py --mock --data FaithConstraint-OR_tier3_v0.jsonl
  python faithopt_loop.py --model claude-opus-4-7 --data FaithConstraint-OR_tier3_v0.jsonl --max-rounds 3 --workers 8
"""
import json, argparse, collections, os, random, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from faithopt_verifier import Var, LinCon, audit
import formulator as FB   # reuse prompt/parse/gold/callers from the harness

DATA_TAG = ""   # set in __main__ from --data; appended to loop detail filename

_Z3_LOCK = threading.Lock()   # z3 is not thread-safe; serialize only the (fast) audit calls

# ----------------------------- feedback (no gold leakage) -----------------------------
def build_feedback(rec, M, report):
    """Repair message from the audit. Does NOT reveal gold rhs values and does NOT
    use internal constraint ids (the model only sees the numbered policy text).
    We give: (a) a concrete price point the model wrongly allows, and (b) how many
    hard constraints appear missing -- then ask the model to re-read and re-extract."""
    n_violated = sum(1 for r in report if (not r["faithful"]) and r.get("reason") != "not_covered(dropped)")
    n_missing  = sum(1 for r in report if (not r["faithful"]) and r.get("reason") == "not_covered(dropped)")
    # collect one concrete counterexample if any
    ce_pt = None
    for r in report:
        if (not r["faithful"]) and r.get("counterexample"):
            ce_pt = ", ".join(f"{k}={v}" for k, v in r["counterexample"].items()); break
    lines = []
    if ce_pt:
        lines.append(f"- Your model permits the point ({ce_pt}). Re-read the policy: at least one "
                     f"clause forbids this point, so a constraint is missing or too loose.")
    if n_missing:
        lines.append(f"- Your model appears to encode FEWER hard constraints than the policy states. "
                     f"At least {n_missing} required constraint(s) are not represented. Re-read EVERY "
                     f"numbered item and add any pricing bound you skipped (some items are procedures or "
                     f"definitions and should be ignored, but every genuine price bound must be encoded).")
    if not lines:
        lines.append("- Your model is not faithful to the policy. Re-read every numbered item and encode "
                     "all genuine price constraints.")
    current = json.dumps({"constraints": [
        {"id": c.cid, "terms": c.terms, "sense": c.sense, "rhs": c.rhs} for c in (M or [])
    ]}, ensure_ascii=False)
    return (
        "A formal verifier checked your previous model and it is NOT faithful to the policy text:\n"
        + "\n".join(lines) +
        "\n\nYour previous model was:\n" + current +
        "\n\nReturn a CORRECTED model as JSON only (same format). Read EVERY numbered policy item again; "
        "compute any derived bound to a NUMBER (do not leave it symbolic); encode every genuine price "
        "constraint and remove none. Do not invent unstated bounds."
    )

# ----------------------------- one record through the loop -----------------------------
def run_record(rec, caller, neutral, max_rounds):
    decls, gold = FB.gold_of(rec)
    base_prompt = FB.build_prompt(rec, neutral)
    history = []   # (round, status, violated, n_viol, raw_excerpt)
    raw = caller(base_prompt, rec)
    rounds = 0
    bare_violated = None
    final_violated = None
    while True:
        M, status = FB.parse_model(raw, rec)
        if status in ("format_error", "empty_after_clean", "symbolic_unresolved"):
            violated, report = True, []
        else:
            try:
                with _Z3_LOCK:
                    report = audit(decls, M, gold)
                forced = (status == "ok_dropped_unknown")
                violated = any(r["faithful"] is False for r in report) or forced
            except Exception:
                violated, report = True, []
        nviol = sum(1 for r in report if r["faithful"] is False)
        history.append((rounds, status, violated, nviol, (raw or '')[:400]))
        if rounds == 0:
            bare_violated = violated
        final_violated = violated
        if (not violated) or rounds >= max_rounds:
            break
        # build feedback and ask for a repair
        fb = build_feedback(rec, M, report)
        repair_prompt = base_prompt + "\n\n=== VERIFIER FEEDBACK (round %d) ===\n" % (rounds + 1) + fb
        raw = caller(repair_prompt, rec)
        rounds += 1
    return {"id": rec["id"], "tier": rec["tier"],
            "bare_violated": bare_violated, "final_violated": final_violated,
            "rounds_used": rounds, "fixed": bool(bare_violated and not final_violated),
            "history": history}

# ----------------------------- run a model over the dataset -----------------------------
def run_model(records, model, caller, neutral, max_rounds, workers=1):
    todo = [r for r in records if r["static_checkable"]]
    results = [None]*len(todo)
    done = 0
    def task(i):
        rec = todo[i]
        try:
            return i, run_record(rec, caller, neutral, max_rounds)
        except Exception as e:
            return i, {"id": rec["id"], "tier": rec["tier"], "bare_violated": True,
                       "final_violated": True, "rounds_used": 0, "fixed": False, "error": str(e)}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(task, i) for i in range(len(todo))]
        for fut in as_completed(futs):
            i, res = fut.result(); results[i] = res; done += 1
            print(f"\r  {model}: {done}/{len(todo)}", end="", flush=True)
    print()
    return results

def summarize(results, model, max_rounds):
    n = len(results)
    bare = sum(1 for r in results if r["bare_violated"])
    final = sum(1 for r in results if r["final_violated"])
    fixed = sum(1 for r in results if r["fixed"])
    rounds_on_fixed = [r["rounds_used"] for r in results if r["fixed"]]
    avg_rounds = sum(rounds_on_fixed) / len(rounds_on_fixed) if rounds_on_fixed else 0
    print(f"\n================  FaithOpt loop: {model}  (max_rounds={max_rounds})  ================")
    print(f"  records evaluated:        {n}")
    print(f"  bare-LLM violation rate:  {bare}/{n} = {100*bare/n:.1f}%")
    print(f"  FaithOpt violation rate:  {final}/{n} = {100*final/n:.1f}%   (lower is better)")
    print(f"  repaired by the loop:     {fixed}/{bare if bare else 1}  of initially-bad cases")
    print(f"  residual (still bad):     {final}/{n}")
    print(f"  avg repair rounds (fixed):{avg_rounds:.2f}")
    # by tier
    for t in ("Hard", "Very hard"):
        sub = [r for r in results if r["tier"] == t]
        if sub:
            b = sum(1 for r in sub if r["bare_violated"]); f = sum(1 for r in sub if r["final_violated"])
            print(f"    {t:10s} bare {100*b/len(sub):5.1f}%  ->  FaithOpt {100*f/len(sub):5.1f}%")
    return {"model": model, "n": n, "bare": bare, "final": final, "fixed": fixed, "avg_rounds": avg_rounds}

def write_detail(results, model):
    os.makedirs(FB.RUNDIR, exist_ok=True)
    path = os.path.join(FB.RUNDIR, f"{model.replace('/','_')}" + (f"__{DATA_TAG}" if DATA_TAG else "") + "_faithopt_loop.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"FaithOpt loop detail: {model}\n" + "="*70 + "\n")
        for r in results:
            f.write(f"\n[{r['id']}] tier={r['tier']}\n")
            f.write(f"  bare_violated={r['bare_violated']}  final_violated={r['final_violated']}  "
                    f"rounds={r['rounds_used']}  fixed={r['fixed']}\n")
            for h in r.get("history", []):
                rd, st, vio, nv = h[0], h[1], h[2], h[3]
                raw = h[4] if len(h) > 4 else ""
                f.write(f"    round {rd}: status={st} violated={vio} n_viol={nv}\n")
                f.write(f"      RAW[:400]: {raw!r}\n")
    return path

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--model", nargs="+", default=None)
    ap.add_argument("--data", default="FaithConstraint-OR_identification.jsonl")
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument("--neutral-prompt", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    random.seed(args.seed)
    import os as _os
    globals()["DATA_TAG"] = _os.path.basename(args.data).replace("FaithConstraint-OR_","").replace(".jsonl","")
    records = [r for r in (json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip()) if not r.get("_manifest")]
    print(f"loaded {len(records)} records from {args.data}  (max_rounds={args.max_rounds})")

    def mock_caller_factory():
        # a mock that starts unfaithful, then improves when it sees verifier feedback,
        # so we can validate the loop mechanics without an API key.
        def caller(prompt, rec):
            decls, gold = FB.gold_of(rec)
            has_fb = "VERIFIER FEEDBACK" in prompt
            rounds_seen = prompt.count("VERIFIER FEEDBACK")
            kept = []
            for idx, g in enumerate(gold):
                # round 0: drop ~40% of constraints; each feedback round recovers more
                p_drop = max(0.0, 0.4 - 0.25*rounds_seen)
                if (not has_fb or rounds_seen < 3) and random.random() < p_drop:
                    continue
                kept.append({"id": g.cid, "terms": g.terms, "sense": g.sense, "rhs": g.rhs})
            return json.dumps({"constraints": kept}, ensure_ascii=False)
        return caller

    summaries = []
    if args.mock:
        res = run_model(records, "MOCK", mock_caller_factory(), args.neutral_prompt, args.max_rounds, args.workers)
        summarize(res, "MOCK", args.max_rounds); print("  detail:", write_detail(res, "MOCK"))
    elif args.model:
        for m in args.model:
            caller = (lambda _m: (lambda prompt, rec: FB.call_real_llm(_m, prompt)))(m)
            res = run_model(records, m, caller, args.neutral_prompt, args.max_rounds, args.workers)
            summaries.append(summarize(res, m, args.max_rounds))
            print("  detail:", write_detail(res, m))
        if len(summaries) > 1:
            print("\n================  BARE vs FaithOpt SUMMARY  ================")
            print("model".ljust(28) + "bare".rjust(8) + "FaithOpt".rjust(10) + "avg_rounds".rjust(12))
            for s in summaries:
                print(s["model"].ljust(28) + f"{100*s['bare']/s['n']:.1f}%".rjust(8) +
                      f"{100*s['final']/s['n']:.1f}%".rjust(10) + f"{s['avg_rounds']:.2f}".rjust(12))
    else:
        print("pass --mock or --model <id> [<id> ...]")
