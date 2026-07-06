# Guru MCP Tool Policy

Use the dedicated Guru MCP as a read-only ontology lens layer. It is separate from the KRW Ontology filing MCP.

## Default Workflow

```text
krw_guru_query_context
-> optional krw_guru_trace or krw_guru_chain on selected reviewed_ids
-> answer
```

Use `krw_guru_search` only for fallback discovery or debugging. Do not use broad search after `krw_guru_query_context` returns a sufficient or partial pack.

## Tool Roles

```text
krw_guru_query_context
- Default first call. Returns ResearchPack, answerability, allowed next tools, selected lenses, source anchors, and company bridge needs.

krw_guru_trace
- Source support and bounded related objects for one selected ontology object.

krw_guru_chain
- Compact semantic neighbors around one selected ontology object.

krw_guru_search
- Fallback discovery only.

krw_guru_status
- Coverage and curation health only.

krw_guru_data_needs
- Compatibility bridge for filing evidence needs. Prefer the company_bridge section in query_context.
```

## Hard Boundary

The Guru MCP does not answer company facts, current financials, valuation, market prices, or latest filings. If the ResearchPack says company evidence is required, use KRW Ontology filing research later as a separate read-only evidence source.

