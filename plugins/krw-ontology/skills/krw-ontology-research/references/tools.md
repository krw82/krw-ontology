# KRW Ontology MCP Tools

Use these read-only tools. They query the canonical agent index built from v1.0.0-alpha ontology artifacts.

## Core Tool Principle

```text
Search may be broad.
Trace and answerability must be strict.
```

Do not treat a retrieved object as direct evidence until its result tier, trace status, and evidence path support that interpretation.

## Canonical Type Rules

Prefer canonical object types in tool calls:

```text
EvidenceQuote
ResearchClaim
MetricObservation
Calculation
XBRLFact
BusinessActivity
BusinessFactor
ExternalFactorExposure
AssumptionCandidate
AgreementTerm
BusinessEvent
CompanyBusinessProfile
TemporalLink
TrendObservation
ChangeEvent
SourceDocument
SourceSpan
SourceLocation
SourceTable
SourceTableCell
CanonicalEntity
EntityMention
```

Compatibility aliases are accepted, but they should normalize to canonical types:

```text
risk, risk_factor, RiskFactor -> BusinessFactor filtered to risk role
driver, growth_driver, GrowthDriver -> BusinessFactor filtered to driver/growth_driver role
headwind, Headwind -> BusinessFactor filtered to headwind role
metric, financial_metric_value, FinancialMetricValue -> MetricObservation
derived_metric_value, DerivedMetricValue -> MetricObservation with derived source / Calculation support
numeric_evidence, NumericEvidence -> MetricObservation, Calculation, EvidenceQuote, or SourceTableCell support depending on source
project_milestone -> BusinessEvent filtered to project_milestone
regulatory_proceeding -> BusinessEvent filtered to regulatory_proceeding
guidance -> BusinessEvent filtered to guidance_update
contract_term, capital_structure_item, lease, covenant -> AgreementTerm filtered by subtype
segment_performance -> MetricObservation with segment dimensions
```

Event aliases must be unambiguous:

```text
business_event, event -> BusinessEvent
change_event, disclosure_change, change -> ChangeEvent
```

If the implementation supports multi-type aliases, `event` may return both `BusinessEvent` and `ChangeEvent`, but the response must label them clearly. Do not let a Python dict overwrite make `event` point unpredictably to one type.

## Answerability And Result Tier Fields

When supported by the implementation, `query`, `retrieve`, `compare`, and discovery responses should expose:

```json
{
  "semantic_relevance": "direct",
  "trace_status": "traceable",
  "tier": "traceable_direct",
  "evidence_chain_count": 3,
  "support_depth": 2,
  "support_quote_count": 1,
  "support_claim_count": 1,
  "matched_required_facets": [],
  "matched_related_facets": [],
  "missing_required_facets": [],
  "why_tier": "Matched the requested premise with explicit quote and claim support."
}
```

Interpretation:

```text
traceable_direct
  Strong final evidence.

traceable_metric_lineage
  Strong numeric evidence.

traceable_related
  Context only; not direct proof of a narrow premise.

untraced_direct_candidate
  Candidate only; do not use as strong evidence.

broad_related_candidate
  Exploration/context only.

no_direct_evidence
  Useful for negative answer when search coverage is adequate.

not_answerable
  Insufficient support.
```

## `krw_ontology_catalog`

List indexed companies, documents, periods, object counts, schema/index versions, and section-quality status.

Useful parameters:

- `ticker`: optional company filter
- `document_types`: `['10-K']`, `['10-Q']`, or both
- `limit`, `offset`
- `response_format`: `json` or `markdown`

Use catalog before broad research when coverage, document scope, periods, or index freshness are unclear.

## `krw_ontology_index_context`

Return the compact index capability card. Use this instead of trying to infer current schema, serving tables, available tickers, object counts, or answerability policy from raw SQLite rows.

Useful parameters:

- `include_counts`: include object and serving-table counts
- `include_capabilities`: include available context/retrieval capabilities
- `include_quality_summary`: include compact quality counters
- `response_format`: `json` or `markdown`

Use this early when the current index coverage, schema version, topic-index availability, or answerability behavior is unclear.

## `krw_ontology_company_context`

Return a compressed evidence-derived topic profile for one ticker from `company_topic_index`.

Useful parameters:

- `ticker`: required company ticker
- `document_types`: optional scope
- `periods`: optional scope
- `limit_topics`: number of company topics to return
- `include_internal_ids`: default `true`; use IDs only for follow-up trace calls
- `response_format`: `json` or `markdown`

This is a search map, not final evidence. Use `topic_label`, `topic_summary`, terms, impact channels, trace status, and source object IDs to plan focused queries and traces. Do not expose `topic_id` or `source_object_ids` in final answers.

## `krw_ontology_query_context`

Return a question-specific context pack with deterministic answerability guidance.

Useful parameters:

- `question`: user question
- `ticker` or `tickers`: optional company scope
- `universe`: use `all` for broad discovery
- `document_types`, `periods`: optional scope
- `limit_results`, `limit_tickers`
- `include_internal_ids`: default `true`; IDs are internal trace inputs only
- `response_format`: `json` or `markdown`

Use this for broad, multi-ticker, direct-exposure, scenario, and ambiguous natural-language questions before writing the answer. Inspect:

```text
query_frame
answerability
ticker_candidates
recommended_tools
final_answer_guidance
```

If `answerability.direct_answerable=false` and `related_context_available=true`, do not promote related context into a direct answer. Trace only the recommended final or related-context objects.

## `krw_ontology_query`

Search ontology objects by structured filters.

Useful parameters:

- `topic`: English or canonical search phrase such as `revenue growth`, `margin pressure`, `export controls`, or `natural gas price`
- `tickers`: e.g. `['VG']`
- `document_types`: e.g. `['10-K']`
- `periods`: e.g. `['FY2025']`
- `object_types`: prefer canonical types. Examples:
  - factual evidence: `['ResearchClaim', 'EvidenceQuote']`
  - exact metrics: `['MetricObservation', 'Calculation', 'XBRLFact']`
  - scenario/risk: `['ExternalFactorExposure', 'BusinessFactor', 'ResearchClaim']`
  - contracts/events: `['AgreementTerm', 'BusinessEvent', 'ResearchClaim', 'EvidenceQuote']`
- `include_rejected`: default `false`
- `limit`, `offset`
- `response_detail`: default `compact`; use `full` only for final selected objects or debugging.

Returns compact evidence bundles by default. Each result should include object summary, support links or supporting evidence counts, source labels when available, result tier metadata when available, related objects sharing support, document quality counts, and a small quality-event summary.

For scenario questions, do not assume one query is enough. Use returned channels, scenario effects, metrics, entities, and support summaries to plan follow-up searches.

Each response may include `search_diagnostics` for topic searches. If results are empty, inspect `normalized_terms`, `fts_query`, and `warnings` before broadening the query.

Common warnings:

- `empty_topic_after_tokenization`: the topic produced no searchable English/canonical tokens, often from Korean-only or overly natural-language input.
- `topic_rewritten_for_search`: the original topic produced no English/canonical tokens, but deterministic query expansion found searchable fallback terms.
- `strict_and_query_may_be_too_narrow`: the strict FTS query likely required too many terms at once. Split the topic into smaller searches.
- `long_strict_topic`: prefer several small topic queries over one long topic.

`search_diagnostics.search_strategy` records the attempted retrieval path. The tool first tries strict AND search, then deterministic canonical expansion, split-term searches, and relaxed OR search. This does not modify the ontology index or create new objects.

## `krw_ontology_retrieve`

Use the deterministic local planner for natural-language questions. This is convenient but less controllable than `krw_ontology_query`.

Important rule:

```text
The retrieve contract is answerability-aware, not a flat result list.
Use answerability, direct_evidence, related_context, rejected_context, tier, trace_status, and why_tier.
```

Expected high-quality response shape:

```json
{
  "answerability": {
    "direct_answerable": false,
    "related_context_available": true,
    "negative_answer_supported": true,
    "needs_user_clarification": false,
      "recommended_answer_mode": "no_direct_evidence_with_related_context"
  },
  "query_frame": {
    "query_type": "direct_exposure_check",
    "required_facets": ["semiconductor", "GPU", "HBM"],
    "impact_channels": ["price_volatility", "margin", "cost", "revenue"],
    "question_requires_direct_match": true
  },
  "direct_evidence": [],
  "related_context": [
    {
      "id": "external_factor_exposure:VG:...",
      "tier": "traceable_related",
      "trace_status": "traceable",
      "why_tier": "Evidence is traceable and related, but broader than the requested premise."
    }
  ],
  "rejected_context": []
}
```

Use `retrieve` for quick broad retrieval and planning. For final answers, use `direct_evidence` only for strong claims, keep `related_context` as context, ignore `rejected_context`, and trace important IDs or use structured `query` plus `chain`.

Do not assume one query is enough for scenario questions. Use returned channels, scenario effects, metrics, entities, and support summaries to plan follow-up searches.

### Direct Exposure / Negative Questions

For questions such as:

```text
Is this company directly exposed to X?
Does the filing show direct exposure to X?
```

`retrieve` must not answer yes from broad related evidence. It should classify:

```text
direct_answerable=false
related_context_available=true
negative_answer_supported=true
```

when the requested premise is missing but broader related evidence exists. Put the broader evidence in `related_context`, not `direct_evidence`.

Example:

```text
Question: Is VG directly exposed to semiconductor memory price cycle or GPU HBM price volatility?
Correct: no direct evidence; related broad commodity/feed gas/LNG price risk may exist.
Incorrect: commodity price exposure means direct HBM exposure.
```

## `krw_ontology_plan_query`

Show the deterministic plan without executing retrieval.

A useful plan should show:

```json
{
  "original_question": "...",
  "query_frame": {
    "query_type": "scenario",
    "required_facets": [],
    "impact_channels": ["gross_margin"],
    "question_requires_direct_match": false
  },
  "normalized_terms": ["natural_gas_price", "gross_margin"],
  "tickers": ["VG"],
  "period_filter": "latest relevant filings",
  "object_types": ["ExternalFactorExposure", "BusinessFactor", "ResearchClaim", "EvidenceQuote"],
  "aliases_applied": {
    "risk": "BusinessFactor"
  },
  "topic_map_terms_used": ["feed_gas_cost", "Henry Hub"],
  "filters": {},
  "ranking_strategy": "evidence_first_materiality_weighted"
}
```

Use this tool to debug why a natural-language query may be too broad, too narrow, or mapped to the wrong object types.

## `krw_ontology_topic_map`

Return company-specific search vocabulary for broad, scenario, business-model, and external-report impact questions.

Useful parameters:

- `ticker`: required company ticker
- `document_types`: optional scope for fallback activity/exposure objects
- `periods`: optional scope for fallback activity/exposure objects
- `limit`
- `response_format`: `json` or `markdown`

This is a search-planning helper, not final evidence. It repackages `CompanyBusinessProfile` and falls back to `BusinessActivity`, `BusinessFactor`, `ExternalFactorExposure`, `MetricObservation`, and relevant entity terms.

Do not use generic topic labels as final evidence. Penalize or ignore topics such as:

```text
business_activity
project
revenue_source
risk
external_factor
```

Prefer specific evidence-derived topics such as:

```text
LNG and feed gas price exposure
SPA termination and debt acceleration risk
FERC/DOE permitting delay risk
Henry Hub feed gas margin pressure
```

Use terms to plan focused `krw_ontology_query` calls, then trace important objects before answering.

## Company Discovery / Large Universe Protocol

For many tickers, do not call topic_map or full query for every company.

Use:

```text
1. Global compact discovery or company_topic_index search.
2. Candidate ticker narrowing.
3. topic_map only for shortlisted tickers.
4. Focused compact query for shortlisted tickers.
5. Trace selected objects only.
6. Synthesis with premise / filing evidence / analyst inference separated.
```

If available, prefer:

```text
retrieve(mode='company_discovery')
response_detail='ticker_summary'
group_by='ticker'
answer_candidate_only=true
```

A good ticker summary should include:

```json
{
  "ticker": "VG",
  "tier": "traceable_direct",
  "score": 0.91,
  "matched_topics": [],
  "matched_required_facets": [],
  "missing_required_facets": [],
  "trace_status": "traceable",
  "top_traceable_object_ids": [],
  "why_tier": "..."
}
```

## `krw_ontology_trace`

Trace a returned object ID to support links, claims, quotes, source locations/spans, source tables/cells, XBRL facts, calculations, document metadata, and quality signals. Exact IDs are best, but a unique returned ID prefix is accepted; ambiguous prefixes return candidates.

Use `trace` when the task asks for or depends on:

- exact values
- dates
- project milestones
- contract terms
- debt maturities
- covenant terms
- guidance
- source evidence for one important conclusion
- metric lineage
- whether a retrieved object is actually traceable

`trace` should prioritize `SupportLink` evidence lineage. It is not graph exploration.

Metric trace is valid without quote support when it resolves through:

```text
MetricObservation -> XBRLFact -> SourceDocument
MetricObservation -> SourceTableCell -> SourceDocument
MetricObservation -> Calculation -> input MetricObservation(s) -> XBRLFact / SourceTableCell / SourceDocument
```

## `krw_ontology_chain`

Return a compact relationship chain around one object. Use it when the answer depends on how evidence, claims, metrics, semantic objects, agreement terms, business events, and temporal context connect.

Useful parameters:

- `object_id`: exact object ID or unique returned prefix
- `max_depth`: default `2`; limits graph edge expansion
- `direction`: `both`, `incoming`, or `outgoing`; aliases such as `upstream` and `downstream` are accepted
- `include_quote_text`: default `false`; keep it false for normal customer-facing research
- `response_format`: `json` or `markdown`

Returns:

- `evidence_chain`: support links, supporting claims, quotes, metrics, calculations, source spans/locations, tables/cells, and source documents when available
- `semantic_neighbors`: related business factors, activities, exposures, agreement terms, business events, assumptions, entities, and metrics
- `temporal_context`: related change events, trend observations, temporal links, and comparable-period objects
- `quality`: evidence grade and warnings such as missing support, rejected object status, stale artifact status, or weak evidence

Use `trace` for one object's source evidence. Use `chain` when connected business meaning matters. Keep `max_depth` bounded to avoid flooding the model with low-value graph neighbors.

## `krw_ontology_quality`

Inspect validation reports, rejected objects, batch failures, section-quality warnings, stale artifacts, old compatibility artifacts, registry/index versions, company-context coverage warnings, traceability coverage, and directness negative-test failures.

A useful quality response should expose internally:

```text
schema_version
registry_version
agent_index_schema_version
retrieval_text_builder_version
support_link_builder_version
artifact_root
index_path
object_counts
critical_validation_errors
quote_exact_match_rate
invalid_reference_count
support_link_resolve_rate
edge_resolve_rate
metric_validation_status
BusinessFactor trace coverage
ExternalFactorExposure trace coverage
orphan_answer_object_count
weakly_linked_object_count
old_artifact_detected
stale_artifact_detected
rejected_count
negative_directness_test_failures
```

Do not expose these details in a normal customer-facing answer unless the user explicitly asks for quality, audit, coverage, or debug output.

## `krw_ontology_compare`

Compare companies, periods, factors, or metrics by `topic` or canonical `metric`. Pass two or more tickers or comparable periods and a small `limit_per_ticker`. Default output is compact.

The response should preserve raw per-ticker or per-period `results` and also include `comparison_rows`: normalized rows with `ticker`, `period`, `document_type`, `source_label`, selected object summary, confidence/evidence grade, evidence tier, evidence counts, caveats, and missing-result status.

Use `comparison_rows` for comparison tables. Trace or query the selected object when a row drives an important conclusion.

Strong comparisons should use temporal and metric objects when relevant:

```text
TemporalLink
TrendObservation
ChangeEvent
MetricObservation
Calculation
BusinessFactor
ExternalFactorExposure
```

Do not treat comparison as two unrelated searches when temporal or metric context is available.

## Response Detail Policy

```text
ids_only       = follow-up trace or debug selection
compact        = normal search and focused query
ticker_summary = company discovery over many tickers
trace_summary  = final pre-answer evidence selection
full           = final selected trace/debug only
```

Never use `full` for broad discovery over many tickers.

## Default Retrieval Object Policy

Default natural retrieval should prioritize answer candidates:

```text
ResearchClaim
EvidenceQuote
BusinessFactor
ExternalFactorExposure
BusinessActivity
BusinessEvent
AgreementTerm
MetricObservation
Calculation
CompanyBusinessProfile
ChangeEvent
TrendObservation
TemporalLink
AssumptionCandidate
```

Default natural retrieval should not surface these as top answer candidates unless explicitly requested:

```text
SupportLink
Edge
CanonicalEntity
EntityMention
SourceDocument
SourceLocation
SourceSpan
XBRLFact
SourceTable
SourceTableCell
```

These objects remain available for explicit query, trace, chain, audit, and metric lineage.

## Safe Final Answer Protocol

Before making a visible claim:

```text
1. Check result tier.
2. Check trace_status.
3. Trace important object IDs.
4. Use traceable_direct / traceable_metric_lineage for strong claims.
5. Use traceable_related only as context.
6. If direct_answerable=false, say direct evidence was not found and separate related context.
```
