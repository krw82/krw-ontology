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
```

Do not assume pack fields are top-level. In normal JSON responses they are usually nested under `research_pack`, `agent_autonomy`, or `kernel`.

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

## Sector/global note

For sector/global/macro questions, query_context should receive an internal English research brief and a bounded covered universe when possible. Do not send broad Korean topic text directly to query_context.
