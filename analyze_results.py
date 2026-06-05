# -*- coding: utf-8 -*-
"""
analyze_results.py - compute the paper's reported numbers from run outputs.

Produces, from files already written by formulator.py / faithopt_loop.py:
  (1) Bare-LLM violation table   : model x split, Hard+VeryHard %, with Wilson 95% CI
  (2) Neutral vs full prompt     : paired violation % per model/split
  (3) FaithOpt loop table        : bare % -> FaithOpt %, repaired/total, round distribution
  (4) Failure-status decomposition: ok / format_error / empty counts (localizability backing)

These map directly onto the \todo placeholders in sections 4-6.

Usage:
  python analyze_results.py                 # scans ./runs
  python analyze_results.py --runs PATH     # custom runs dir

Note: this only reads logs; it does not call any LLM. Re-run the experiments first
(formulator.py / faithopt_loop.py) to refresh the logs at the final benchmark size.
"""
import os, re, glob, json, math, argparse
from collections import defaultdict, Counter


# Models excluded from analysis (e.g. unstable output format -> format_error dominates).
EXCLUDE_MODELS = {"gemini-3.5-flash", "gemini-3_5-flash"}


def wilson_ci(k, n, z=1.96):
    """Wilson 95% CI for a proportion; returns (lo, hi) in percent."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (100 * (center - half), 100 * (center + half))


# ---------- (1) + (4): bare-LLM detail files ----------
def parse_bare_detail(path):
    """Return dict with model, split-agnostic per-record verdicts and status counts."""
    txt = open(path, encoding="utf-8").read()
    model = None
    m = re.search(r"MODEL:\s*(\S+)", txt)
    if m:
        model = m.group(1)
    # Hard / Very hard lines
    hard = re.search(r"Hard:\s*(\d+)/(\d+)", txt)
    vhard = re.search(r"Very hard:\s*(\d+)/(\d+)", txt)
    status = {}
    ms = re.search(r"status:\s*(\{[^}]*\})", txt)
    if ms:
        try:
            status = eval(ms.group(1))  # the harness prints a python dict literal
        except Exception:
            status = {}
    hv_k = (int(hard.group(1)) if hard else 0) + (int(vhard.group(1)) if vhard else 0)
    hv_n = (int(hard.group(2)) if hard else 0) + (int(vhard.group(2)) if vhard else 0)
    return {"model": model, "hv_k": hv_k, "hv_n": hv_n, "status": status}


def bare_table(runs):
    # group detail files by (model, split-from-filename or content)
    rows = []
    for f in sorted(glob.glob(os.path.join(runs, "*_detail.txt"))):
        base = os.path.basename(f)
        if base.startswith("MOCK"):
            continue
        if any(m in base for m in EXCLUDE_MODELS):
            continue
        info = parse_bare_detail(f)
        if info["hv_n"] == 0:
            continue
        # split label from filename: <model>__<split>_detail.txt
        split = ""
        stem = base.replace("_detail.txt", "")
        if "__" in stem:
            split = stem.split("__", 1)[1].replace("_neutral", "")
        lo, hi = wilson_ci(info["hv_k"], info["hv_n"])
        label = f"{info['model'] or base}  [{split}]" if split else (info["model"] or base)
        rows.append((label, info["hv_k"], info["hv_n"],
                     100 * info["hv_k"] / info["hv_n"], lo, hi, info["status"]))
    print("=" * 78)
    print("(1) BARE-LLM violation (Hard+VeryHard).  NOTE: detail files are per-run;")
    print("    if you ran one split at a time, label each row by the split you ran.")
    print("=" * 78)
    print(f"{'model/file':<34}{'viol':>10}{'rate%':>9}{'95% CI':>18}")
    for model, k, n, rate, lo, hi, _ in rows:
        print(f"{model:<34}{f'{k}/{n}':>10}{rate:>8.1f}{f'[{lo:.1f},{hi:.1f}]':>18}")
    print()
    # (4) status decomposition
    print("=" * 78)
    print("(4) FAILURE-STATUS DECOMPOSITION (localizability backing)")
    print("    'ok' with violation = localizable (mis-encode); format_error/empty = non-localizable")
    print("=" * 78)
    agg = Counter()
    for *_, status in rows:
        agg.update(status)
    print("  aggregate status across these detail files:", dict(agg))
    print()


# ---------- (3): loop files + round distribution ----------
def loop_table(runs):
    print("=" * 78)
    print("(3) FaithOpt LOOP: bare -> FaithOpt, repaired, round distribution")
    print("=" * 78)
    for f in sorted(glob.glob(os.path.join(runs, "*_faithopt_loop.txt"))):
        base = os.path.basename(f)
        if base.startswith("MOCK"):
            continue
        if any(m in base for m in EXCLUDE_MODELS):
            continue
        txt = open(f, encoding="utf-8").read()
        bare = re.search(r"bare-LLM violation rate:\s*(\d+)/(\d+)", txt)
        faith = re.search(r"FaithOpt violation rate:\s*(\d+)/(\d+)", txt)
        rep = re.search(r"repaired by the loop:\s*(\d+)/(\d+)", txt)
        # round distribution among fixed cases (loop logs: "rounds=N  fixed=True")
        rounds = re.findall(r"rounds=(\d+)\s+fixed=True", txt)
        rdist = Counter(int(r) for r in rounds)
        print(f"--- {base} ---")
        if bare:
            bk, bn = int(bare.group(1)), int(bare.group(2))
            blo, bhi = wilson_ci(bk, bn)
            print(f"  bare:     {bk}/{bn} = {100*bk/bn:.1f}%  CI[{blo:.1f},{bhi:.1f}]")
        if faith:
            fk, fn = int(faith.group(1)), int(faith.group(2))
            flo, fhi = wilson_ci(fk, fn)
            print(f"  FaithOpt: {fk}/{fn} = {100*fk/fn:.1f}%  CI[{flo:.1f},{fhi:.1f}]")
        if rep:
            print(f"  repaired: {rep.group(1)}/{rep.group(2)}")
        if rdist:
            print(f"  round distribution (fixed cases): {dict(sorted(rdist.items()))}")
        print()


# ---------- (2): neutral vs full ----------
def neutral_compare(runs):
    print("=" * 78)
    print("(2) NEUTRAL vs FULL prompt (pair files <model>_detail.txt vs <model>_neutral_detail.txt)")
    print("=" * 78)
    fulls = {os.path.basename(f).replace("_detail.txt", ""): f
             for f in glob.glob(os.path.join(runs, "*_detail.txt"))
             if "_neutral" not in os.path.basename(f)}
    found = False
    for f in glob.glob(os.path.join(runs, "*_neutral_detail.txt")):
        key = os.path.basename(f).replace("_neutral_detail.txt", "")
        if key in fulls:
            found = True
            fu = parse_bare_detail(fulls[key])
            ne = parse_bare_detail(f)
            fr = 100 * fu["hv_k"] / fu["hv_n"] if fu["hv_n"] else float("nan")
            nr = 100 * ne["hv_k"] / ne["hv_n"] if ne["hv_n"] else float("nan")
            print(f"  {key:<28} full={fr:5.1f}%   neutral={nr:5.1f}%   delta={nr-fr:+.1f}")
    if not found:
        print("  (no *_neutral_detail.txt files found; run formulator.py --neutral-prompt first)")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    a = ap.parse_args()
    if not os.path.isdir(a.runs):
        raise SystemExit(f"runs dir not found: {a.runs}")
    bare_table(a.runs)
    neutral_compare(a.runs)
    loop_table(a.runs)
