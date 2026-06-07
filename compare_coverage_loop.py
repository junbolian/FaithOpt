# -*- coding: utf-8 -*-
"""compare_coverage_loop.py - quantify the END-TO-END value of the coverage condition.

Run faithopt_loop.py twice on a conflict-heavy split (e.g. multivariate, identification),
once normally and once with --no-coverage, then point this script at the two detail files
written under runs/ (FB.RUNDIR), with-coverage first:

    python compare_coverage_loop.py \
        runs/gpt-4o-2024-11-20__multivariate_faithopt_loop.txt \
        runs/gpt-4o-2024-11-20__multivariate_faithopt_loop.txt   # the --no-coverage one

The headline number is |flagged WITH coverage and shipped WITHOUT|: the dropped-rule models
the loop would silently accept if the verifier checked soundness only. (The rigorous,
controlled count is coverage_ablation.py's 395 drops / 158 instances; this is the same
effect under the model's natural behaviour inside the full repair pipeline.)
"""
import re, sys

def load(path):
    """id -> {'bare':bool,'final':bool,'fixed':bool} parsed from a loop detail file."""
    d, cur = {}, None
    for ln in open(path, encoding="utf-8"):
        m = re.match(r"\[(.+?)\]\s+tier=", ln)
        if m:
            cur = m.group(1); d[cur] = {}
            continue
        if cur:
            for key, pat in (("bare", r"bare_violated=(\w+)"),
                             ("final", r"final_violated=(\w+)"),
                             ("fixed", r"fixed=(\w+)")):
                mm = re.search(pat, ln)
                if mm:
                    d[cur][key] = (mm.group(1) == "True")
    return d

def rate(d, key):
    vals = [v[key] for v in d.values() if key in v]
    return (sum(vals), len(vals))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python compare_coverage_loop.py <WITH_coverage_detail> <NO_coverage_detail>")
        sys.exit(1)
    withc, noc = load(sys.argv[1]), load(sys.argv[2])
    common = sorted(set(withc) & set(noc))

    # the key end-to-end quantity: coverage flips the shipped verdict
    flipped = [i for i in common if withc[i].get("final") and not noc[i].get("final")]
    # sanity: cases where no-coverage flags but with-coverage does not (should be ~none)
    reverse = [i for i in common if (not withc[i].get("final")) and noc[i].get("final")]

    wf, wn = rate(withc, "final"); nf, nn = rate(noc, "final")
    print(f"records compared (intersection): {len(common)}")
    print(f"  WITH-coverage  residual violation: {wf}/{wn} = {100*wf/max(wn,1):.1f}%")
    print(f"  NO-coverage    residual violation: {nf}/{nn} = {100*nf/max(nn,1):.1f}%  "
          f"(lower, but blind to dropped rules)")
    print("-"*64)
    print(f"  >> coverage CHANGES the shipped verdict on {len(flipped)}/{len(common)} instances")
    print(f"     (flagged/repaired WITH coverage, silently shipped WITHOUT):")
    for i in flipped:
        print(f"       {i}")
    if reverse:
        print(f"  [check] {len(reverse)} instance(s) flagged only WITHOUT coverage "
              f"(expected ~0 since the two runs are independent API calls): {reverse}")
