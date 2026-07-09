# Guru MCP Tool Policy

Use the dedicated Guru MCP as a read-only ontology lens layer. It is separate from the KRW Ontology filing MCP.

## Default Workflow

```text
English-first private internal guru consultation brief
-> if company-specific: use app-provided company context when it already exists; do not call KRW Ontology filing MCP directly from the main Guru run
-> normalize company_context only from app-provided runtime context, without ticker hard-coding
-> krw_guru_query_context with English-first query text and company_context when available
-> if company-specific: krw_guru_company_brief with the same company_context
-> app-provided company_evidence_researcher receives the exact dynamic_question_plan, searches filings, selects exact object IDs, and calls krw_ontology_verify_evidence once
-> the subagent returns the exact krw-verified-company-evidence/v1 payload unchanged
-> krw_guru_review_company_evidence receives that exact payload before final writing
-> optional krw_guru_trace or krw_guru_chain on selected reviewed_ids only for extra guru-source support; these tools never replace company filing evidence
-> answer
```

Use `krw_guru_search` only for fallback discovery or debugging. Do not use broad search after `krw_guru_query_context` returns a sufficient or partial pack.

The brief is private. Do not expose it to the user. Guru source materials are English-first, so the actual query text passed to Guru MCP should lead with the English internal investment brief and English retrieval terms. Korean original wording may be included only as secondary context.

All MCP usage is silent. Do not tell the user that you are calling tools, reading files, checking `research_status`, retrying JSON parameters, or waiting for a bridge. Tool outputs are internal materials for the final answer only.

## Tool Roles

```text
krw_guru_query_context
- Default first Guru MCP call after the English-first internal guru consultation brief. For company-specific questions, pass company_context only when the app runtime already supplied bounded company context. Returns ResearchPack, answerability, allowed next tools, selected lenses, source anchors, runtime mode, and company bridge needs.

krw_guru_company_brief
- Company-specific bridge. Converts selected guru lenses, data_needs, company_context, and company_bridge requirements into a KRW Ontology company filing research question and dynamic_question_plan. It does not retrieve company facts. The dynamic_question_plan is the preferred evidence contract for the company_evidence_researcher subagent.

krw_guru_company_pack
- Legacy/debug post-company-evidence bridge. It is not part of the default product runtime. The default runtime writes the final answer from the Guru ResearchPack, CompanyEvidencePack, and krw_guru_review_company_evidence guidance.

krw_guru_review_company_evidence
- Required post-company-evidence review for named-company judgments. It accepts only the exact verified payload and reviews how that evidence strengthens, weakens, or fails to support the selected Guru ResearchPack lenses. It returns interpretation guidance only; it does not write final prose or impose a fixed report template.

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
call krw_guru_company_brief with the same author_keys and company_context when available, pass the exact dynamic_question_plan with question_id values to company_evidence_researcher, require one krw_ontology_verify_evidence call, then pass the exact verifier payload to krw_guru_review_company_evidence; retry the subagent if the review rejects a missing or modified pack

ontology_gap:
state that the current guru ontology did not return enough support
```

Trace or chain only selected reviewed_ids from the ResearchPack. Do not trace every candidate.

For named-company or ticker-specific judgment, do not finish from Guru MCP calls alone. The required path is `krw_guru_company_brief -> company_evidence_researcher -> krw_ontology_verify_evidence -> krw_guru_review_company_evidence`. `krw_guru_trace`, `krw_guru_chain`, and `krw_guru_evidence` may improve guru-source support, but they are not company filing research.

Company-specific final-answer gate:

```text
krw_guru_query_context
-> krw_guru_company_brief
-> Agent(subagent_type="company_evidence_researcher", prompt includes dynamic_question_plan)
-> exact krw-verified-company-evidence/v1 payload
-> krw_guru_review_company_evidence
-> final consultation answer
```

Do not answer from `krw_guru_query_context` or `krw_guru_company_brief` alone for durable company claims. If the company evidence Agent or review fails, retry once with the exact question plan and verifier payload. If it still fails, return a brief limitation in advisor voice instead of inventing company facts or claiming filing evidence was checked.

## Hard Boundary

The Guru MCP does not answer company facts, current financials, valuation, market prices, or latest filings. `company_context` is orientation only, not evidence. `krw_guru_company_brief` prepares the company filing research brief and dynamic question plan. `krw_guru_review_company_evidence` only reviews already-supplied company evidence against selected guru lenses and the evidence questions. The app-provided company_evidence_researcher subagent owns the actual KRW Ontology company filing MCP call.
