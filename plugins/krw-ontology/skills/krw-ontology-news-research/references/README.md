# KRW Ontology News Research References

These references are internal guidance for the `krw-ontology-news-research` skill. Normal web-chat answers should not expose file names, schema terms, tool names, object IDs, diagnostics, runtime settings, stock-news mechanics, source tiers, event IDs, ontology bridge briefs, or routing logic.

## Active references

```text
web-chat-runtime.md
- visible answer shape, Korean Markdown boundary, follow-up question rule

news-event-policy.md
- stock-news event discovery, source priority, ontology augmentation, and conflict handling

economic-impact-framework.md
- causal event-to-company analysis for policy, financing, industry, legal, and macro shocks

tool-policy.md
- stock-news event tool roles, KRW ontology tool roles, inputs, and normal workflow

research-pack-rendering.md
- how runtime research packs should be rendered without exposing pack names

evidence-to-analyst-synthesis.md
- how to translate ontology evidence into analyst language

ontology-schema-reference.md
- detailed current object schema based on src/krw_ontology/schema/objects.py

ontology-layer-map.md
- short mental model of ontology layers and serving index relationship

query-context-contract.md
- query_context response fields, fallback interpretation, status handling

bounded-autonomy-and-stop-rules.md
- when to stop searching and how to preserve analyst autonomy without loops

period-and-latest-policy.md
- CY labels and latest-filing precedence

financial-statement-interpretation.md
- cash-flow, FCF, R&D, M&A, buyback, SBC, and capital-allocation interpretation guardrails

trace-chain-policy.md
- trace vs chain use cases and boundaries

forbidden-user-facing-language.md
- phrases and internal terms that must not appear in normal answers

structured-handoff-contract.md
- optional structured handoff boundary

artifact-contract.md
- canonical artifact, serving index, audit/export boundary

evaluation-gates.md
- quality gates for E2E and manual checks
```

## Removed/merged references

```text
research-mode-policy.md
- removed because runtime modes will be handled by runner/UI or separate mode-specific skills.

ontology-structure.md
- merged into ontology-schema-reference.md and ontology-layer-map.md.

tools.md
- merged into tool-policy.md.
```

## Maintenance rule

Keep this folder specific to news-led research. If future fast/standard/deep behavior needs detailed policy, create runner documentation instead of mixing speed/budget policy into this news skill.
