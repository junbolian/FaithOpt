# -*- coding: utf-8 -*-
"""
run_multigen.py - multiple-generation evaluation for FaithOpt
=====================================================================
Addresses the reviewer request to report mean +/- sd over GENERATIONS,
not just a single generation per instance.

What it does
  For each (model, split) it runs the bare-LLM measurement R times (R independent
  generations per instance), computes the per-instance violation rate for each run,
  and reports across the R runs:  mean, sd, min, max.
  It also writes a tidy CSV (one row per model x split x run) so you can recompute
  anything later, plus a summary table you can paste back to me.

It REUSES the existing harness (formulator.py + faithopt_verifier.py); it does not
re-implement prompting, parsing, or auditing, so the numbers are directly comparable
to Table 2 (tab:bare).

Why repeated runs matter even at temperature 0
  Providers are not bit-exact even at temperature 0 (batching, kernel nondeterminism,
  silent model updates). That provider-side variance is exactly what the single-run
  Wilson intervals do NOT capture. Use --temperature 0 to measure that; use a small
  positive temperature (e.g. 0.5-0.7) if you also want decoding stochasticity.

Setup (same as formulator.py)
  pip install openai z3-solver
  set the API key and (optionally) base url:
      # Windows PowerShell:
      $env:GLOBALAI_KEY="sk-xxxxx"
      $env:LLM_BASE_URL="https://globalai.vip/v1"     # optional, this is the default
      # macOS/Linux:
      export GLOBALAI_KEY=sk-xxxxx

Usage
  # smoke test with the built-in mock LLM (no API key, no network):
  python run_multigen.py --mock --runs 3 --splits multivariate identification

  # real run: 5 generations/instance, 3 models, 2 splits, temperature 0:
  python run_multigen.py --runs 5 --temperature 0 --workers 8 \
      --models claude-opus-4-7 gpt-5.4 claude-haiku-4-5 \
      --splits multi identification multivariate single

  # add decoding stochasticity:
  python run_multigen.py --runs 5 --temperature 0.6 --workers 8 \
      --models claude-opus-4-7 --splits identification

Outputs
  multigen_runs.csv      one row per (model, split, run): violation_rate, n_instances, ...
  multigen_summary.csv   one row per (model, split): mean, sd, min, max, runs
  multigen_summary.txt   human-readable table (paste this back to me)

Notes
  * Hard+Very-hard only, matching the main tables (Easy/Medium are the Single-split
    sanity tiers and sit near zero).
  * z3 verification runs in the main thread (z3 is not thread-safe); only the network
    fetch is parallelized, exactly as in formulator.py.
"""
import os, sys, csv, json, math, argparse, statistics
from functools import partial

import formulator as F           # reuse the existing harness
from faithopt_verifier import audit  # noqa: F401  (used indirectly via F.score_raw)

# ----------------------------------------------------------------------
# Split file names (the released benchmark)
# ----------------------------------------------------------------------
SPLIT_FILES = {
    "single":         "FaithConstraint-OR_single.jsonl",
    "multi":          "FaithConstraint-OR_multi.jsonl",
    "identification": "FaithConstraint-OR_identification.jsonl",
    "multivariate":   "FaithConstraint-OR_multivariate.jsonl",
}
HARD_TIERS = {"Hard", "Very hard"}


def load_split(path):
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("_manifest"):
                continue
            recs.append(r)
    return recs


def make_caller(use_mock, temperature):
    """Return a caller(prompt, rec) -> raw_text, matching formulator's call signature."""
    if use_mock:
        # formulator.mock_llm(prompt, rec) ignores temperature; good enough for a smoke test
        return lambda prompt, rec: F.mock_llm(prompt, rec)

    # real caller: OpenAI-compatible, with a temperature knob (formulator hardcodes 0)
    from openai import OpenAI
    client = OpenAI(
        api_key=os.environ["GLOBALAI_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://globalai.vip/v1"),
    )

    def caller(prompt, rec, _model):
        r = client.chat.completions.create(
            model=_model, max_tokens=1024, temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.choices[0].message.content

    return caller


def eval_once(records, model, caller, workers, use_mock):
    """One full pass over the split for one model. Returns (n_instances, n_violated).
    Reuses formulator.fetch_raw (threaded network) + formulator.score_raw (serial z3)."""
    # bind model into the caller for the real path
    if use_mock:
        bound = caller
    else:
        bound = lambda prompt, rec: caller(prompt, rec, model)

    # ---- parallel fetch (network only) ----
    from concurrent.futures import ThreadPoolExecutor, as_completed
    hard = [r for r in records if r.get("tier") in HARD_TIERS and r.get("static_checkable", True)]
    raws = [None] * len(hard)
    errs = [None] * len(hard)

    def task(i):
        raw, err = F.fetch_raw(hard[i], bound, model, neutral=False, dump_dir=None)
        return i, raw, err

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(task, i) for i in range(len(hard))]
        for fut in as_completed(futs):
            i, raw, err = fut.result()
            raws[i] = raw
            errs[i] = err

    # ---- serial verify (z3 main thread) ----
    n_inst = 0
    n_viol = 0
    for i, rec in enumerate(hard):
        out = F.score_raw(rec, raws[i], errs[i])
        if out.get("skip"):
            continue
        n_inst += 1
        if out.get("violated"):
            n_viol += 1
    return n_inst, n_viol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["claude-opus-4-7", "gpt-5.4", "deepseek-v3.2",
                             "claude-haiku-4-5", "qwen3-max", "gpt-4o-2024-11-20"])
    ap.add_argument("--splits", nargs="+",
                    default=["multi", "identification", "multivariate", "single"])
    ap.add_argument("--runs", type=int, default=5, help="generations per instance")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--mock", action="store_true", help="use built-in mock LLM (no API key)")
    ap.add_argument("--out-prefix", default="multigen")
    args = ap.parse_args()

    for s in args.splits:
        if s not in SPLIT_FILES:
            sys.exit(f"unknown split '{s}'; choose from {list(SPLIT_FILES)}")
        if not os.path.exists(SPLIT_FILES[s]):
            sys.exit(f"missing data file {SPLIT_FILES[s]} (run from the folder with the .jsonl files)")

    caller = make_caller(args.mock, args.temperature)

    rows = []          # per (model, split, run)
    summary = []       # per (model, split)
    for split in args.splits:
        recs = load_split(SPLIT_FILES[split])
        for model in args.models:
            rates = []
            for run in range(1, args.runs + 1):
                n_inst, n_viol = eval_once(recs, model, caller, args.workers, args.mock)
                rate = (100.0 * n_viol / n_inst) if n_inst else float("nan")
                rates.append(rate)
                rows.append({"model": model, "split": split, "run": run,
                             "n_instances": n_inst, "n_violated": n_viol,
                             "violation_rate_pct": round(rate, 2),
                             "temperature": args.temperature})
                print(f"[{split:14s}] {model:22s} run {run}/{args.runs}: "
                      f"{rate:5.1f}%  ({n_viol}/{n_inst})", flush=True)
            mean = statistics.fmean(rates)
            sd = statistics.stdev(rates) if len(rates) > 1 else 0.0
            summary.append({"model": model, "split": split, "runs": args.runs,
                            "mean_pct": round(mean, 2), "sd_pct": round(sd, 2),
                            "min_pct": round(min(rates), 2), "max_pct": round(max(rates), 2),
                            "temperature": args.temperature})
            print(f"  -> {model} on {split}: mean {mean:.1f} +/- {sd:.1f} "
                  f"(min {min(rates):.1f}, max {max(rates):.1f})\n", flush=True)

    # ---- write CSVs ----
    with open(f"{args.out_prefix}_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(f"{args.out_prefix}_summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        w.writeheader(); w.writerows(summary)

    # ---- human-readable summary ----
    lines = []
    lines.append(f"FaithOpt multi-generation summary  (runs={args.runs}, temperature={args.temperature})")
    lines.append("mean +/- sd of per-instance violation rate over independent generations, Hard+Very-hard")
    lines.append("")
    header = f"{'model':24s}" + "".join(f"{s:>20s}" for s in args.splits)
    lines.append(header)
    lines.append("-" * len(header))
    by = {(r["model"], r["split"]): r for r in summary}
    for model in args.models:
        cells = []
        for split in args.splits:
            r = by.get((model, split))
            cells.append(f"{r['mean_pct']:.1f} +/- {r['sd_pct']:.1f}" if r else "-")
        lines.append(f"{model:24s}" + "".join(f"{c:>20s}" for c in cells))
    txt = "\n".join(lines)
    with open(f"{args.out_prefix}_summary.txt", "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    print("\n" + txt)
    print(f"\nWrote {args.out_prefix}_runs.csv, {args.out_prefix}_summary.csv, {args.out_prefix}_summary.txt")


if __name__ == "__main__":
    main()
