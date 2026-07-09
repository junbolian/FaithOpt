# Prompt templates (verbatim)

These are the exact strings the experiment harness reads (`formulator.py`, `faithopt_loop.py`);
the paper's e-companion points here. Placeholders in braces are filled per instance from the
benchmark record: `{scenario}` (the manager request), `{reg_text}` (the policy excerpt),
`{vars}` (the declared decision variables with domains).

## 1. Full formulation prompt (`PROMPT_FULL`)

```text
You are an optimization-modeling assistant for a pharmacy chain.
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
```

## 2. Neutral ablation prompt (`PROMPT_NEUTRAL`)

Omits the variable list and the encode-every-binding-constraint directive (Section 5.4 of the
paper).

```text
A pharmacy manager asks: {scenario}

Relevant text:
{reg_text}

Write the pricing/decision model as a JSON object:
{{"constraints":[{{"id","terms","sense","rhs"}}]}}  (terms maps variable->coefficient; sense in "<=",">=","==").
Output JSON only.
```

## 3. Repair prompt

Each repair round re-sends the full formulation prompt above, followed by:

```text

=== VERIFIER FEEDBACK (round N) ===
<feedback block>
```

where the feedback block is built by `build_feedback` in `faithopt_loop.py`, reproduced verbatim
below. It contains the audit's counterexample and the violated rule's source span; it never
reveals a gold value.

```python
def build_feedback(rec, M, report, no_coverage=False):
    """Repair message from the audit. Does NOT reveal gold rhs values and does NOT
    use internal constraint ids (the model only sees the numbered policy text).
    We give: (a) a concrete price point the model wrongly allows, and (b) how many
    hard constraints appear missing -- then ask the model to re-read and re-extract.
    Ablation: when no_coverage=True the coverage signal is suppressed (the model is
    never told a rule is missing), mirroring a verifier that checks soundness only."""
    n_violated = sum(1 for r in report if (not r["faithful"]) and r.get("reason") != "not_covered(dropped)")
    n_missing  = 0 if no_coverage else sum(1 for r in report if (not r["faithful"]) and r.get("reason") == "not_covered(dropped)")
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
```
