#!/usr/bin/env python3
"""
coverage_ablation.py -- with/without coverage, on the benchmark's conflict instances.

The verify-repair loop's *action* on each audited model is determined by the verdict:
  faithful  -> accept / deploy (loop exits)
  unfaithful-> repair or escalate (loop acts)

This script isolates what the coverage condition changes, with NO LLM/API. On every
conflict instance (gold set infeasible), we drop each gold rule in turn to obtain a
model M that has silently lost that rule, and audit M against the gold two ways:

  (S)  soundness only            -- the natural reference-free check (entailment)
  (S+C) soundness + feasibility-gated coverage  -- FaithOpt

On an infeasible model, soundness is vacuous (an infeasible system entails everything),
so (S) certifies the dropped-rule model as faithful and the loop would SHIP it; (S+C)
flags the missing rule and the loop would REPAIR/ESCALATE. We count where the two
pipelines take different actions.
"""
import json
import faithopt_verifier as V

SPLITS = ["multi", "identification", "multivariate"]


def load(split):
    rows = [json.loads(l) for l in open(f"FaithConstraint-OR_{split}.jsonl", encoding="utf-8") if l.strip()]
    return [r for r in rows if "_manifest" not in r]


def build(inst):
    decls = [V.Var(v["name"], v["kind"], lb=v.get("lb"), ub=v.get("ub")) for v in inst["var_decls"]]
    gold = [V.LinCon(c["id"], c["terms"], c["sense"], c["rhs"]) for c in inst["gold_constraints"]]
    return decls, gold


def flagged_soundness(rep):
    """A rule flagged by soundness alone (an admitted violation), not by coverage."""
    return any((not r["faithful"]) and ("not_covered" not in r["reason"].lower())
               and ("cover" not in r["reason"].lower()) for r in rep)


def flagged_any(rep):
    return any(not r["faithful"] for r in rep)


def main():
    grand = dict(conflict_instances=0, drops=0, ship_without_cov=0, caught_with_cov=0,
                 cov_only=0, instances_rescued=0)
    print(f"{'split':16}{'conflict':>9}{'drops':>7}{'S ships':>9}{'S+C flags':>11}{'coverage-only':>15}")
    print("-" * 67)
    for split in SPLITS:
        n_conf = n_drops = n_ship_S = n_flag_SC = n_cov_only = n_rescued = 0
        for inst in load(split):
            decls, gold = build(inst)
            if V.model_feasible(decls, gold):
                continue                      # not a conflict instance
            n_conf += 1
            rescued_here = False
            for c in gold:
                M = [g for g in gold if g.cid != c.cid]   # silently drop rule c
                rep = V.audit(decls, M, gold)
                n_drops += 1
                s_catches = flagged_soundness(rep)        # soundness-only verdict
                sc_catches = flagged_any(rep)             # FaithOpt verdict
                if not s_catches:
                    n_ship_S += 1                         # (S) certifies -> loop ships the drop
                if sc_catches:
                    n_flag_SC += 1
                if sc_catches and not s_catches:          # coverage flips accept -> flag
                    n_cov_only += 1
                    rescued_here = True
            if rescued_here:
                n_rescued += 1
        print(f"{split:16}{n_conf:>9}{n_drops:>7}{n_ship_S:>9}{n_flag_SC:>11}{n_cov_only:>15}")
        grand["conflict_instances"] += n_conf
        grand["drops"] += n_drops
        grand["ship_without_cov"] += n_ship_S
        grand["caught_with_cov"] += n_flag_SC
        grand["cov_only"] += n_cov_only
        grand["instances_rescued"] += n_rescued
    print("-" * 67)
    print(f"{'TOTAL':16}{grand['conflict_instances']:>9}{grand['drops']:>7}"
          f"{grand['ship_without_cov']:>9}{grand['caught_with_cov']:>11}{grand['cov_only']:>15}")
    print()
    print(f"Conflict (infeasible-gold) instances: {grand['conflict_instances']}")
    print(f"Silent dropped-rule models constructed: {grand['drops']}")
    print(f"  certified faithful by soundness-only (would SHIP the drop): {grand['ship_without_cov']}")
    print(f"  of those, flagged once coverage is added (-> REPAIR/ESCALATE): {grand['cov_only']}")
    print(f"  conflict instances with >=1 such rescue: {grand['instances_rescued']}")
    print()
    print("=> Coverage changes the loop's action from silent-accept to flag-and-act on")
    print(f"   {grand['cov_only']} dropped-rule models that soundness alone would have shipped.")


if __name__ == "__main__":
    main()
