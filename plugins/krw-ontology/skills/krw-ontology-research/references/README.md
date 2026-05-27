# KRW Ontology Research References

These references are internal guidance for the `krw-ontology-research` skill. Normal web-chat answers should not expose file names, schema terms, tool names, object IDs, diagnostics, runtime settings, or routing logic.

## Active references

```text
web-chat-runtime.md
- visible answer shape, Korean Markdown boundary, follow-up question rule

tool-policy.md
- MCP tool roles and normal tool workflow

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

Keep this folder mode-independent. If future fast/standard/deep/super-deep behavior needs detailed policy, create separate mode-specific skills or runner documentation instead of adding mode policy back here.
