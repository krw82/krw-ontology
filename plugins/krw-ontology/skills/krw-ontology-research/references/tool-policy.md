# MCP Tool Policy

Use KRW ontology MCP tools as read-only evidence tools. Do not use raw JSONL or memory when indexed ontology evidence can answer or constrain the answer.

## Tool roles

```text
krw_ontology_query_context
Default first call for natural-language research. Returns research state, packs, answerability, and allowed next tools.

krw_ontology_query
Targeted structured follow-up for a specific metric, fact, object type, ticker, period, or explicit missing part.

krw_ontology_compare
Explicit comparison only when query_context does not provide enough comparison state or the user asks for a comparison table.

krw_ontology_trace
Evidence lineage for one selected object.

krw_ontology_chain
Business/semantic/temporal mechanism around one selected object.

krw_ontology_retrieve
Legacy fallback only. Do not use after sufficient query_context in Fast or Standard mode.

krw_ontology_company_context
Company orientation only when query_context lacks company-specific vocabulary or the user asks broad company profile context.

krw_ontology_index_context
Debug/capability/coverage only. Not for normal research.

krw_ontology_catalog
Inventory/coverage only.

krw_ontology_quality
Audit/validation/coverage only.
```

## Normal workflow

```text
query_context -> optional targeted query/compare -> selected trace/chain -> answer
```

Do not issue repeated equivalent queries. Do not use `full` response detail for broad first-pass exploration.

For V1 web chat, never request `response_detail="full"` in any research mode. Use compact or ticker-summary query output, then selected trace/chain when stronger verification is needed.

## Strong claim rule

Strong final claims require traceable direct evidence or metric lineage. Projection or topic candidates are routes, not final proof.
