# FaithConstraint-OR - Data Card

One benchmark, four splits, evaluated separately (do **not** merge). All gold
constraints are mechanically generated and z3-verified. The constraint **structures and
scenarios** are drawn from real regulated pharmacy operations (pricing, reimbursement,
procurement, assortment); numeric values are abstracted from realistic ranges for commercial
confidentiality and to prevent memorization. Each `.jsonl` begins
with a `_manifest` line carrying the split's identity; data readers skip it.

| Split (file) | n | #vars | conflicts | cross-var | what it tests |
|---|---|---|---|---|---|
| `FaithConstraint-OR_single.jsonl` | 44 | [0, 1, 2, 3, 5, 6] | 1 | 8 | single pre-extracted constraint (baseline) |
| `FaithConstraint-OR_multi.jsonl` | 150 | [1] | 82 | 0 | multiple pre-extracted constraints; encode all |
| `FaithConstraint-OR_identification.jsonl` | 150 | [1] | 80 | 0 | identify constraints among numbered noise, then encode |
| `FaithConstraint-OR_multivariate.jsonl` | 174 | [2] | 63 | 174 | two prices p1,p2 with cross-variable constraints + conflicts |

## Per-split manifest

### single  (`FaithConstraint-OR_single.jsonl`)
```json
{
  "_manifest": true,
  "dataset": "FaithConstraint-OR",
  "split": "single-constraint",
  "version": "v0",
  "description": "Single pre-extracted hard constraint per instance (sanity baseline). Mostly one variable; includes 3 intentional out-of-scope marker instances (missing-value / temporal / discrete-ladder) with empty gold.",
  "n_instances": 44,
  "num_variables": [
    0,
    1,
    2,
    3,
    5,
    6
  ],
  "n_conflict_instances": 1,
  "n_with_crossvariable_constraint": 8,
  "ground_truth": "mechanical, z3-verified (never LLM-generated)",
  "generated": "2026-06-03"
}
```

### multi  (`FaithConstraint-OR_multi.jsonl`)
```json
{
  "_manifest": true,
  "dataset": "FaithConstraint-OR",
  "split": "multi-constraint",
  "version": "v0",
  "description": "Multiple pre-extracted hard constraints scattered across documents; encode all. Single variable.",
  "n_instances": 150,
  "ground_truth": "mechanical, z3-verified",
  "generated": "2026-06-03"
}
```

### identification  (`FaithConstraint-OR_identification.jsonl`)
```json
{
  "_manifest": true,
  "dataset": "FaithConstraint-OR",
  "split": "identification",
  "version": "v0",
  "description": "Numbered policy list mixing constraints with noise; identify then encode all. Single variable.",
  "n_instances": 150,
  "ground_truth": "mechanical, z3-verified",
  "generated": "2026-06-03"
}
```

### multivariate  (`FaithConstraint-OR_multivariate.jsonl`)
```json
{
  "_manifest": true,
  "dataset": "FaithConstraint-OR",
  "split": "multivariate",
  "version": "v0",
  "description": "Two prices p1,p2 with cross-variable constraints, derived bounds, noise; incl. over-determined conflicts.",
  "n_instances": 174,
  "ground_truth": "mechanical, z3-verified",
  "generated": "2026-06-03",
  "n_conflict_instances": 63
}
```

## Notes
- Gold validity was spot-checked by **blind human recovery**: on 60 instances from the
  `multivariate` and `identification` splits, two annotators independently reconstructed the gold
  from text alone (without seeing it), matching it on 119/120 instance-reconstructions (the lone
  miss a transcription slip) — the gold is recoverable from the text, not idiosyncratic.
- `single` includes 3 intentional out-of-scope marker instances (missing-value /
  temporal / discrete-ladder) with empty gold; exclude them from main violation stats.
- `multivariate` includes over-determined-conflict instances probing the empty-set loophole.
- Scope: linear constraints (incl. multivariate). Non-linear / logical = future work.
- To scale a split: raise the loop counts in the matching `generate_*.py` and re-run;
  ground truth is re-verified automatically and the manifest count updates.