# Guru MCP Tool Policy

Use the dedicated Guru MCP as a read-only ontology lens layer. It is separate from the KRW Ontology filing MCP.

## Default Workflow

```text
private internal guru consultation brief
-> krw_guru_query_context
-> optional krw_guru_trace or krw_guru_chain on selected reviewed_ids
-> answer
```

Use `krw_guru_search` only for fallback discovery or debugging. Do not use broad search after `krw_guru_query_context` returns a sufficient or partial pack.

The brief is private. Do not expose it to the user.

## Tool Roles

```text
krw_guru_query_context
- Default first MCP call after the internal guru consultation brief. Returns ResearchPack, answerability, allowed next tools, selected lenses, source anchors, runtime mode, and company bridge needs.

krw_guru_trace
- Source support and bounded related objects for one selected ontology object.

krw_guru_chain
- Compact semantic neighbors around one selected ontology object.

krw_guru_search
- Fallback discovery only.

krw_guru_status
- Coverage and curation health only.

krw_guru_index_context
- Debug/capability/index health only. Not for normal answers. `krw_guru_query_context` is already shard-aware.

krw_guru_data_needs
- Compatibility bridge for filing evidence needs. Prefer the company_bridge section in query_context.
```

## Index And Shard Policy

The Guru MCP is optimized internally:

```text
reviewed JSONL = source of truth
author SQLite shards = read-optimized serving layer
guru_shard_manifest.json = lightweight global map
```

Skills must not read shard files directly. Always call `krw_guru_query_context` with the fixed `author_keys` for the selected skill. The MCP will use the author shard when available and fall back to reviewed JSONL if needed.

## Stop Rules

```text
sufficient_lens:
answer without broad follow-up

partial_lens:
answer narrowly; do not fill missing lenses from memory

needs_clarification:
ask the returned clarifying question before strong advice

needs_company_evidence:
keep the guru lens, but use KRW Ontology filing evidence before a company-specific judgment

ontology_gap:
state that the current guru ontology did not return enough support
```

Trace or chain only selected reviewed_ids from the ResearchPack. Do not trace every candidate.

## Hard Boundary

The Guru MCP does not answer company facts, current financials, valuation, market prices, or latest filings. If the ResearchPack says company evidence is required, use KRW Ontology filing research later as a separate read-only evidence source.
