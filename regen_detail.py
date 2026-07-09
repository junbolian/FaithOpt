# -*- coding: utf-8 -*-
"""
regen_detail.py - Rebuild <model>__<split>_detail.txt PURELY from the saved raw
outputs in runs/<model>__<split>/<id>.txt.  No API calls: re-parse the
'=== RAW OUTPUT ===' section and re-audit with z3, reusing formulator's exact
parse_model / score_raw / write_detail so the numbers match a live run.

Usage:
  python regen_detail.py --data FaithConstraint-OR_multivariate.jsonl
  python regen_detail.py --data FaithConstraint-OR_multivariate.jsonl --model gpt-5.4 qwen3-max
"""
import os, re, json, argparse, collections
import formulator as F

RAW_MARK = "=== RAW OUTPUT ===\n"

def extract_raw(path):
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    i = txt.find(RAW_MARK)
    if i < 0:
        return None
    raw = txt[i + len(RAW_MARK):]
    # dump writes '...{raw}\n'; strip the single trailing newline the dumper added
    if raw.endswith("\n"):
        raw = raw[:-1]
    return raw

def rebuild_rep(model, records, raw_dir):
    """Mirror formulator.run_model's aggregation, but source raw text from disk."""
    results = []
    for rec in records:
        if not rec.get("static_checkable"):
            results.append({"id": rec["id"], "tier": rec["tier"],
                            "fam": rec["constraint_family"], "skip": True})
            continue
        p = os.path.join(raw_dir, f"{rec['id']}.txt")
        if not os.path.exists(p):
            print(f"  [warn] raw dump missing, scoring as api error: {p}")
            results.append(F.score_raw(rec, None, "raw_dump_missing"))
            continue
        raw = extract_raw(p)
        results.append(F.score_raw(rec, raw, None))

    by_tier = collections.defaultdict(lambda: [0, 0])
    by_fam = collections.defaultdict(lambda: [0, 0])
    status_ct = collections.Counter()
    skipped = 0
    for r in results:
        if r.get("skip"):
            skipped += 1; continue
        status_ct[r["status"]] += 1
        v = 1 if r["violated"] else 0
        by_tier[r["tier"]][0] += v; by_tier[r["tier"]][1] += 1
        fam = r["fam"].split("(")[0].strip()
        by_fam[fam][0] += v; by_fam[fam][1] += 1
    return {"model": model, "neutral": False, "by_tier": dict(by_tier), "by_fam": dict(by_fam),
            "status": dict(status_ct), "skipped": skipped, "dump_dir": raw_dir, "results": results}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", nargs="+", default=None, help="one or more model ids")
    ap.add_argument("--data", default=F.DATA, help="path to jsonl dataset")
    args = ap.parse_args()

    tag = os.path.basename(args.data).replace("FaithConstraint-OR_", "").replace(".jsonl", "")
    F.DATA_TAG = tag  # so write_detail names the file <model>__<tag>_detail.txt
    records = [r for r in (json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip())
               if not r.get("_manifest")]
    print(f"loaded {len(records)} records  split={tag}")

    suffix = f"__{tag}"
    if args.model:
        models = args.model
    else:
        models = sorted(d[:-len(suffix)] for d in os.listdir(F.RUNDIR)
                        if d.endswith(suffix) and os.path.isdir(os.path.join(F.RUNDIR, d)))
    print("models:", models)

    for m in models:
        raw_dir = os.path.join(F.RUNDIR, m.replace("/", "_") + suffix)
        if not os.path.isdir(raw_dir):
            print(f"  [skip] no raw dir for {m}: {raw_dir}")
            continue
        rep = rebuild_rep(m, records, raw_dir)
        F.print_model(rep)
        dp = F.write_detail(rep, records)
        print(f"   detail rewritten: {dp}")
