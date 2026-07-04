# Web Chat Runtime Contract

Default KRW Ontology English web chat output is English Markdown only.

Do not produce structured JSON, `ResearchSynthesis`, `DisplayPlan`, `answer_blocks`, `canonical_answer.units`, or renderer-specific payloads unless explicitly requested by the runtime or user.

## Visible answer shape

Recommended structure:

```text
Conclusion
Key Points
Interpretation
What To Watch
Next Questions To Dig Into
```

The exact headings may vary, but the answer must be directly useful and filing-aware.

## Normal answer must not include

```text
runtime setting names
tool names
pack names
object IDs
object type names
diagnostics
schema terms
query/routing narration
progress narration
generic limitation sections
```

## Follow-up questions

Normal answers should end with:

```text
Next Questions To Dig Into
```

Include exactly 3 concise English questions as a numbered Markdown list using `1.`, `2.`, `3.`. Do not run tools to create them. Do not call them chains. Do not mention internal concepts.

At least one normal follow-up should naturally route to scenario/sensitivity work, usually by asking for upside/downside/sideways checkpoints or judgment-change conditions.

## Evidence handling

Show grounding through careful wording and mechanism, not citation dumping.

Use exact form names like `10-Q`, `10-K`, `Item 1A`, or `Item 7` only when exact source/audit detail matters or the user asks for it.

In normal investor-facing answers, translate raw SEC item labels into user-facing source labels such as `Business section`, `MD&A`, `Risk Factors`, `Notes`, or `cash flow statement`. Do not write parentheticals like `verified by trace`.

For normal recent/latest questions, plain labels such as `latest quarter`, `CY2026Q1`, or `CY2025 annual baseline` are preferred.
