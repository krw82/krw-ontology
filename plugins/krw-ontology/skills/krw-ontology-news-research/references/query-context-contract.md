# Query Context Contract

Use `krw_ontology_query_context` as the default research workbench for normal questions.

## Field paths

Current MCP builds may return fields like:

```text
research_context_version
research_status
research_pack.metric_series_pack
research_pack.projection_pack
research_pack.chain_pack
research_pack.company_topic_pack
research_pack.directness_guard
research_pack.stop_guard
research_pack.valuation_guard
agent_autonomy.mode
agent_autonomy.may_continue_research
agent_autonomy.allowed_next_tools
agent_autonomy.disallowed_next_tools
agent_autonomy.max_additional_tool_calls
missing_parts
do_not_call
recommended_tools
final_answer_guidance
answerability
query_frame
ticker_candidates
results_by_ticker
search_diagnostics
kernel
current_document_anchors
filing_document_roles
```

Do not assume pack fields are top-level. In normal JSON responses they are usually nested under `research_pack`, `agent_autonomy`, or `kernel`.

## Filing document roles

Current MCP builds may return `filing_document_roles` at the top level, under `research_pack`, or under routing diagnostics.

Use it as the authoritative document-role map when present:

```text
filing_document_roles.<TICKER>.current_driver
  latest available 10-Q when it exists, otherwise latest available 10-K
  use as the latest reported financial baseline for current/news-event interpretation

filing_document_roles.<TICKER>.annual_baseline
  latest available 10-K
  use for business mix, segment structure, annual baseline, historical risk baseline

filing_document_roles.<TICKER>.latest_available
  latest available 10-Q/10-K filing
```

`current_document_anchors` is compatibility shorthand for `filing_document_roles.<TICKER>.current_driver`.

When both fields exist, follow `filing_document_roles` and use `current_document_anchors` only as a quick label.

The role contract is intentionally limited to 10-Q and 10-K. Do not infer or invent other filing-form roles.

## Compatibility fallback

Older or partially deployed MCP builds may omit some fields.

Use conservative fallback:

```text
answerability.direct_answerable = true and recommended trace roots exist:
treat as sufficient_but_trace_recommended

answerability.negative_answer_supported = true:
treat as sufficient for a negative direct-exposure answer

ticker_candidates contain traceable direct or related candidates:
treat as sufficient for a default answer unless exhaustive audit is requested

no candidates returned:
allow at most one targeted structured follow-up query

target price, fair value, investment recommendation, 12-month price target:
treat as out of scope for filing-only ontology unless external valuation inputs are supplied
```

Do not compensate for missing research-pack fields by launching open-ended query, retrieve, compare, trace, or chain loops.

## Research status interpretation

```text
sufficient_for_default_answer:
answer without broad follow-up

sufficient_but_trace_recommended:
trace/chain selected roots only before strong claims

partial_answer_possible:
answer narrowly; fill explicit missing parts only if cheap and targeted

needs_targeted_followup:
use allowed_next_tools only

no_direct_evidence_with_related_context:
separate direct absence from related context

out_of_scope_for_filing_ontology:
stop searching and explain using filing-supported assumptions only

not_answerable:
do not force a conclusion
```

## Internal English investment brief

For normal KRW ontology research, query_context should receive a concise internal English investment brief. Do not send broad Korean topic text directly to query_context.

The brief is not a literal translation. Build it with awareness of the KRW ontology schema, evidence types, research packs, and MCP retrieval surface so it is optimized for ontology evidence retrieval.

In news mode, build the English brief after stock-news event discovery. The brief should combine the user's original intent with the discovered event, source timing, financial channel, and ontology bridge hints. Do not pass a broad Korean news question directly into query_context.

The English investment brief should preserve:

```text
user intent
tickers and company names
periods
event date and source timing
event type
exact metrics
comparison axes
direct-exposure factors
capital-allocation or cash-flow concepts
```

Map user intent to ontology-friendly retrieval concepts when useful:

```text
explanatory filing text / notes
MD&A / management discussion
segment commentary
revenue recognition / RPO / backlog
cost of revenue / gross margin / operating margin
cash flow / FCF / capex
business combinations / acquisitions
share repurchases / dilution management
SBC / R&D / talent investment
direct exposure / related pressure channel
business model / product platform / customer demand
```

For broad sector/global/macro news questions, pair the internal English investment brief with a bounded covered universe when possible.

For concrete tickerless news events with a specific factor, channel, product, business model, metric, event type, or company type, query_context may receive `tickers=[]` with `limit_tickers <= 5` and `limit_results <= 3` after stock-news event discovery. Treat the v3 global spine result as candidate ticker ranking and evidence routing, not as the final answer.
