# KRW Ontology MCP Tools

Use these read-only tools.

## `krw_ontology_catalog`

List indexed companies, documents, periods, and section-quality status.

Useful parameters:
- `ticker`: optional company filter
- `document_types`: `["10-K"]`, `["10-Q"]`, or both
- `limit`, `offset`
- `response_format`: `"json"` or `"markdown"`

## `krw_ontology_query`

Search ontology objects by structured filters.

Useful parameters:
- `topic`: English search phrase such as `"revenue growth"` or `"margin pressure"`
- `tickers`: e.g. `["VG"]`
- `document_types`: e.g. `["10-K"]`
- `periods`: e.g. `["FY2025"]`
- `object_types`: prefer `["ResearchClaim", "EvidenceQuote"]` for factual evidence; include `GrowthDriver`, `RiskFactor`, `Headwind`, or `AssumptionCandidate` for themes. Common aliases such as `Risk`, `Claim`, and `Metric` are accepted and normalized.
- `include_rejected`: default `false`
- `limit`, `offset`
- `response_detail`: default `"compact"` for agent-safe results. Use `"full"` only when you need full raw objects/document metadata.

Returns compact evidence bundles by default. Each result should include the object summary, supporting claims, supporting quotes, source spans when available, related objects sharing support, document quality counts, and a small quality-event summary. Do not assume one query is enough for scenario questions; use returned channels and metrics to plan follow-up searches.

Each response also includes `search_diagnostics` for topic searches. If results are empty, inspect `normalized_terms`, `fts_query`, and `warnings` before broadening the query. Common warnings:
- `empty_topic_after_tokenization`: the topic produced no searchable English/canonical tokens, often from Korean-only or overly natural-language input.
- `strict_and_query_may_be_too_narrow`: the strict FTS query likely required too many terms at once. Split the topic into smaller searches.
- `long_strict_topic`: prefer several small topic queries over one long topic.

## `krw_ontology_topic_map`

Return company-specific search vocabulary for broad, scenario, business-model, and external-report impact questions.

Useful parameters:
- `ticker`: required company ticker
- `document_types`: optional scope for fallback activity/exposure objects
- `periods`: optional scope for fallback activity/exposure objects
- `limit`
- `response_format`: `"json"` or `"markdown"`

This is a search-planning helper, not final evidence. It repackages `CompanyBusinessProfile` and falls back to `BusinessActivity`, `ExternalFactorExposure`, and metric objects. Use its terms to plan focused `krw_ontology_query` calls, then trace important objects before answering.

## `krw_ontology_trace`

Trace a returned object ID to supporting quotes, source spans, document metadata, and quality signals. Exact IDs are best, but a unique returned ID prefix is accepted; ambiguous prefixes return candidates.

## `krw_ontology_quality`

Inspect rejected objects, batch failures, section-quality warnings, and company-context coverage warnings. Use this before declaring a dataset reliable or complete.

## `krw_ontology_compare`

Compare companies by `topic` or canonical `metric`. Pass two or more tickers and a small `limit_per_ticker`. Default output is compact.

## `krw_ontology_retrieve`

Use the deterministic local planner for natural-language questions. This is convenient but less controllable than `krw_ontology_query`. It is useful for quick broad retrieval; trace important IDs before final synthesis.

For scenario questions, prefer explicit `krw_ontology_query` calls over relying on `retrieve` alone. Search factor/benchmark terms first, then follow up on revenue, cost, margin, cash flow, liquidity, and explicit threshold terms when relevant.

## `krw_ontology_plan_query`

Show the deterministic plan without executing retrieval.
