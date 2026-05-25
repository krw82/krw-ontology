# Research Pack Rendering Policy

Research packs are runtime response adapters. They are not DB tables, not ontology schema, and not user-facing terms.

Never mention pack names in normal answers.

## metric_series_pack

Use for numeric answers.

Render as:

```text
table when values are aligned
YoY growth / share / growth difference when provided
short interpretation below the table
```

Check:

```text
period alignment
unit consistency
scale consistency
target dimension vs denominator role
missing parts
```

If the pack does not support a precise table, do not invent one and do not explain internal retrieval limits.

## business_profile_pack

Use for company overview, business model, and revenue driver questions.

Render order:

```text
recent drivers when requested
annual revenue mix / business baseline
segment or product/service explanation
margin/cash-flow implication
material caveats
```

Do not treat missing exact metrics as not answerable by default.

## risk_mechanism_pack

Use for thesis/risk/scenario questions.

Render each major channel as:

```text
risk or premise -> financial path -> affected metric/channel -> implication
```

Representative metrics are context only unless metric lineage is strong.

## comparison_view

Use for comparison questions.

Compare only rows with the same basis, period, or context. MCP can provide conclusion hints, but the AI analyst writes the final comparison judgment.

Do not declare a winner from broad hit counts.

## direct_exposure_pack

Use for direct exposure questions.

Separate:

```text
direct evidence
related context
no direct evidence
```

If strong_claim_allowed is false, do not use direct exposure wording.

## scope_guard_pack

Use for target price, fair value, investment recommendation, or final valuation questions.

Stop valuation conclusion. Provide only filing-supported assumptions or caveats.

## evidence_index and chain_pack

Use as selected trace/chain root suggestions. Do not expand every candidate.
