---
name: krw-ontology-research
description: Use when answering equity research questions from KRW ontology data, especially questions about 10-K or 10-Q evidence, claims, quotes, risks, drivers, headwinds, assumptions, metrics, rejected objects, section quality, or cross-company comparisons. Use the krw-ontology MCP tools instead of raw JSONL or memory.
---

# KRW Ontology Research

Use the `krw-ontology` MCP server as the source of truth. Do not answer from memory when ontology evidence is available.

## Mental Model

The MCP server reads the generated SQLite agent index, not raw JSONL files.

- `agent_index.sqlite` is the read-optimized cache built from accepted ontology JSONL artifacts and edges.
- MCP tools use `OntologyStore` over that index for catalog, search, trace, quality, and comparison.
- `krw_ontology_query`, `krw_ontology_trace`, `krw_ontology_quality`, and `krw_ontology_compare` do not call an LLM planner.
- `krw_ontology_retrieve` and `krw_ontology_plan_query` use the deterministic local `DefaultQueryPlanner`, not `ClaudeAgentQueryPlanner`.
- `ClaudeAgentQueryPlanner` is only for standalone Python SDK tests or local retriever scripts where there is no outer AI agent to choose MCP tool arguments.

When you are the outer AI agent, prefer planning the MCP call yourself. Treat the MCP tools as deterministic evidence-retrieval tools over the agent index.

## Ontology Layer Map

Use the ontology as layered evidence, not as one flat search result list.

1. Evidence layer: `SourceSpan` and `EvidenceQuote`. These are closest to the filing text and are the strongest support for factual answers.
2. Claim layer: `ResearchClaim`. Claims summarize evidence into atomic facts, risks, forward-looking statements, strategic statements, assumptions, or factual statements.
3. Quant layer: `XBRLFact`, `FinancialMetricValue`, `DerivedMetricValue`, and `NumericEvidence`. Use these first for exact financial values, reported metrics, and numeric tables.
4. Semantic business layer: `RiskFactor`, `GrowthDriver`, `Headwind`, `BusinessActivity`, `ExternalFactorExposure`, and `AssumptionCandidate`. These are normalized research objects built from claims and evidence.
5. Company context layer: `CompanyBusinessProfile`, `TemporalLink`, `TrendObservation`, and `ChangeEvent`. These are company-level and multi-period synthesis objects.

Object priority matters. For exact facts, dates, amounts, thresholds, project milestones, contract terms, capacity, ownership, debt maturity, covenant terms, or guidance, search direct evidence, claims, and quant objects before high-level risk/headwind objects. For scenario, risk, business model, and trend questions, use semantic and context objects, then trace back to claims and quotes before making the answer final.

For detailed object usage guidance, see `references/ontology-structure.md`.

## Workflow

1. Call `krw_ontology_catalog` first when the available companies, document types, or periods are unclear.
2. For evidence questions, call `krw_ontology_query` with explicit `tickers`, `document_types`, `periods` or `period_policy` reasoning, `topics`, and relevant `object_types`.
3. For natural-language convenience, `krw_ontology_retrieve` is available, but prefer structured `krw_ontology_query` when you can infer filters yourself. `retrieve` is a fallback for ambiguous questions, not the primary path.
4. Call `krw_ontology_trace` on important returned object IDs before making a final claim.
5. Call `krw_ontology_quality` when section quality, rejected objects, batch failures, or trustworthiness matter.
6. Call `krw_ontology_compare` for multi-company topic or metric comparisons.

## Broad Research Questions

For open-ended prompts such as "What are the key risks for VG?" or "compare demand and margin pressure across these companies":

1. Build a short private research plan: identify tickers, document scope, periods, likely topics, object types, and the evidence needed for a trustworthy answer.
2. Use `krw_ontology_catalog` to verify the available company/document/period coverage unless the user supplied it and it is already known.
3. Start broad with compact search results: use `response_detail="compact"`, small limits, and object types such as `ResearchClaim`, `RiskFactor`, `Headwind`, `GrowthDriver`, and `EvidenceQuote`.
4. Refine with follow-up `krw_ontology_query` calls by topic/category/period. For key risks, prefer latest 10-K first, then use 10-Q only if the question asks for current or recent changes.
5. Trace the most important returned IDs with `krw_ontology_trace`. Trace accepts exact IDs and unique ID prefixes returned by query.
6. Check `krw_ontology_quality` before making a reliability statement.
7. Only use `response_detail="full"` when compact output lacks necessary fields; full output can be very large.

## Factual Lookup Questions

Use this protocol for questions asking for exact facts, dates, target dates, amounts, thresholds, ownership, contract terms, project milestones, capacity, production, debt maturity, covenant terms, guidance, or named asset details. Examples include "CP2 COD 언제야?", "Permian production target?", "debt maturities?", "SPA pricing terms?", "capex guidance?", or "what capacity did the company disclose?".

1. Search ontology before web search or memory. Direct 10-K/10-Q filing evidence outranks press releases, news, analyst estimates, and inference unless the external source is newer and explicitly updates the filing.
2. Query the latest relevant filing periods first. For current facts, prefer latest 10-Q plus latest 10-K. For long-horizon annual context, prefer latest 10-K first.
3. Search both the named subject and the requested attribute. Example subject terms: CP2, Permian, debt, SPA, facility, segment, asset name. Example attribute terms: COD, commercial operation date, FID, final investment decision, capacity, price, maturity, covenant, target, guidance, volume, ownership, deadline, termination.
4. Prefer `ResearchClaim`, `EvidenceQuote`, `FinancialMetricValue`, `DerivedMetricValue`, and `NumericEvidence` before `RiskFactor`, `Headwind`, or `GrowthDriver`.
5. Trace exact or near-exact matches before answering. If the answer depends on one key value or date, trace that object even if the query result already shows a quote snippet.
6. If a filing gives a direct value/date/condition, use it as the primary answer and label it accurately as reported, targeted, expected, estimated, guided, or forward-looking.
7. If the exact value is not found, say so. Do not substitute a market estimate or inferred value as if it were a filing disclosure.
8. Before finalizing, run one contradiction check using the named subject plus the proposed answer term/date, especially for project timelines, contract terms, maturities, and guidance.
9. Use web search only after ontology searches for direct filing evidence return no answer, or when the user explicitly asks for current post-filing updates. Clearly label web evidence as supplemental.

## Scenario And Sensitivity Questions

For prompts like "what happens if...", "if this falls/rises", "below what level", "이 아래로 내려가면", or "큰일 나는 조건":

1. Identify the external factor, benchmark, and likely financial channels. Example: Henry Hub maps to natural gas price; likely channels are revenue, cost of revenue, operating margin, cash flow, and liquidity.
2. Start with `krw_ontology_query` over `ExternalFactorExposure`, `RiskFactor`, `Headwind`, and `ResearchClaim` for the factor and benchmark terms.
3. Do not stop after the first result. Use terms from returned `impact_channel`, `related_metrics`, and quote text for follow-up queries. For price scenarios, check at least revenue, cost, margin, cash flow, and liquidity if those channels appear relevant.
4. Search separately for explicit threshold terms such as breakeven, covenant, minimum, deadline, termination, impairment, default, settlement, or liability cap when the user asks "how low/high is dangerous".
5. Treat direct quotes and `evidence_grade=direct` as stronger than indirect or derived evidence. If the object is strong but the numeric threshold is not found, say that the ontology has structural evidence but no explicit threshold.
6. If revenue and cost effects point in different directions, answer mixed or uncertain instead of forcing a single net effect.
7. Check `krw_ontology_quality` when the answer depends on completeness, latest-period coverage, rejected objects, or coverage-gap warnings.

## Question Type Protocols

- Factual lookup: query `ResearchClaim`, `EvidenceQuote`, `FinancialMetricValue`, `DerivedMetricValue`, and `NumericEvidence`; then trace the exact or near-exact match. Use high-level objects only as context.
- Risk overview: query `RiskFactor`, `Headwind`, `ExternalFactorExposure`, and `ResearchClaim`; prefer latest 10-K unless the user asks for recent quarterly changes.
- Change over time: query `ChangeEvent`, `TemporalLink`, `TrendObservation`, and comparable-period claims; do not state a trend from one period only.
- Metric explanation: query the metric first, then search related `ResearchClaim`, `ExternalFactorExposure`, `RiskFactor`, and `Headwind` using the metric name and major drivers found in the metric evidence.
- Business model: query `CompanyBusinessProfile`, `BusinessActivity`, and `ExternalFactorExposure`; check quality for coverage gaps before claiming the profile is complete.
- Comparison: use `krw_ontology_compare`, then trace or query the most important differences per ticker/period.

## Answering Rules

- Cite object IDs, ticker, document type, period, and short quote snippets.
- Treat `quality.section_quality=warn` or batch failures as caveats.
- Do not cite rejected objects unless the user explicitly asks for rejected data.
- If evidence is weak, say that the retrieved evidence is weak instead of overstating.
- Prefer direct quotes and `ResearchClaim` objects over high-level theme objects when answering factual questions.
- Use 10-Q evidence for quarterly/current updates and 10-K evidence for annual or long-horizon context.
- For cross-company comparisons, state that comparison is ad-hoc unless a dedicated cross-company edge or temporal/thread object is returned.
- Do not claim that a risk/driver is new, removed, increasing, or decreasing over time unless you queried comparable periods and traced evidence from each period.
- Do not let web search override a direct filing value/date unless the web source is newer, specific, and explicitly updates that same fact.
- If the agent index is missing or stale, ask the user to run `uv run krw-ontology build-agent-index --root <ontology-root>` or rebuild via `build-research-pipeline`.

## Tools

See `references/tools.md` for concise tool selection guidance and parameters.
