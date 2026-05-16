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
For product two-pass workflows, produce internal research synthesis with complete `canonical_answer.units` according to `references/research-synthesis-contract.md`. Display planning belongs to the separate `krw-ontology-answer-composer` skill.

## Workflow

1. Call `krw_ontology_catalog` first when the available companies, document types, or periods are unclear.
2. For evidence questions, call `krw_ontology_query` with explicit `tickers`, `document_types`, `periods` or `period_policy` reasoning, `topics`, and relevant `object_types`.
3. For natural-language convenience, `krw_ontology_retrieve` is available, but prefer structured `krw_ontology_query` when you can infer filters yourself. `retrieve` is a fallback for ambiguous questions, not the primary path.
4. Call `krw_ontology_trace` on important returned object IDs before making a final claim.
5. Call `krw_ontology_chain` for the most important objects when the answer depends on how evidence, claims, semantic objects, and temporal context connect.
6. Call `krw_ontology_quality` when section quality, rejected objects, batch failures, or trustworthiness matter.
7. Call `krw_ontology_compare` for multi-company topic or metric comparisons.

## Two-Pass Product Boundary

In product runtimes, this skill is pass 1: research, evidence validation, analytical judgment, and canonical answer content. It should not choose visual blocks, plan layout, or emit `display_plan`/`answer_blocks`.

1. Use MCP tools to gather and validate evidence.
2. Build a complete `ResearchSynthesis` when the runtime expects structured handoff to a renderer or display planner.
3. Put every user-visible sentence, number, caveat, and conclusion into `canonical_answer.units`.
4. Keep raw object IDs, trace IDs, quote text, diagnostics, and coverage details internal unless the user explicitly requests audit/debug output.
5. Hand the completed synthesis to `krw-ontology-answer-composer` only for `display_plan` generation.
6. If this skill is invoked standalone in a normal chat, answer naturally using the final-answer evidence rules below; do not force structured output.

The research synthesis must be materially complete before display planning. Do not rely on the answer-composer to discover missing evidence, repair weak numeric support, write missing prose, or run broad ontology searches.

## Search Principles

These are the high-priority rules. Follow them before applying the more detailed protocols below.

1. For broad, scenario, sensitivity, and business-model questions, inspect `CompanyBusinessProfile` early to discover company-specific vocabulary, key exposures, activities, and factors.
2. Prefer `krw_ontology_topic_map` as the first search-planning step for broad, scenario, sensitivity, business-model, and external market report impact questions. It is not final evidence; use it to choose focused `krw_ontology_query` terms.
3. Do not query an entire object type without topic, period, metric, or subject constraints just because an initial topic search returned no results.
4. If a topic search returns no results, inspect `search_diagnostics` before broadening scope. If `normalized_terms` is empty, translate the question into English/canonical company exposure terms or call `krw_ontology_topic_map`. If the strict query is too narrow, split it into smaller topic searches.
5. For exact dates, amounts, contract terms, project milestones, guidance, or thresholds, verify direct filing evidence through `EvidenceQuote`, `ResearchClaim`, or quant objects before using web, memory, or inference.
6. If the user provides or references an external market, macro, commodity, policy, or industry report, treat that report as a market premise and map it to company-level ontology exposures. Do not stop at "the ontology has no evidence on the market report topic" unless the user only asked whether the report itself is indexed.
7. Trace and source IDs are internal evidence controls. Use them to verify the answer, but do not expose raw ontology IDs or filing quote text in the final user-facing answer unless the user explicitly asks for raw evidence, trace details, debug output, or exportable citations.

## Chain-First Research Protocol

Use the ontology chain when a conclusion depends on more than one object. The chain is the system's main advantage: filing text supports quotes, quotes support claims, claims support normalized research objects, and those objects connect to related semantic and temporal context.

1. Start with `krw_ontology_query` or `krw_ontology_topic_map` to find candidate objects.
2. For the top object behind each important conclusion, call `krw_ontology_chain` with `max_depth=2`, `direction="both"`, and `include_quote_text=false`.
3. Read the chain in layers:
   - `evidence_chain`: checks whether the object is actually grounded in claims, quotes, and source spans.
   - `semantic_neighbors`: reveals connected risks, drivers, headwinds, activities, exposures, and assumptions.
   - `temporal_context`: reveals related change events, trend observations, and cross-period links.
   - `quality`: shows weak evidence grade, rejected status, section warnings, or missing support.
4. Use `krw_ontology_trace` instead of `chain` when you only need the source evidence for one exact fact. Use `chain` when you need to understand connected business meaning.
5. For factual dates, amounts, project milestones, contract terms, and guidance, do not let semantic neighbors override direct filing evidence. Use the chain to catch context and contradictions, not to replace direct support.
6. For scenario and external-market-report questions, use `chain` on the strongest `ExternalFactorExposure`, `BusinessActivity`, `RiskFactor`, or `Headwind` objects before finalizing the impact bridge.
7. If `quality.warnings` includes `no_supporting_evidence_found`, `object_is_rejected`, or weak evidence grade, omit the unsupported point or narrow the conclusion privately. Do not add a visible evidence-quality disclaimer unless the user asks for audit/debug output.
8. Keep chain details internal by default. The visible answer should summarize the relationship in plain language without raw object IDs or quote text unless the user asks for audit/debug output.

## Broad Research Questions

For open-ended prompts such as "What are the key risks for VG?" or "compare demand and margin pressure across these companies":

1. Build a short private research plan: identify tickers, document scope, periods, likely topics, object types, and the evidence needed for a trustworthy answer.
2. Use `krw_ontology_catalog` to verify the available company/document/period coverage unless the user supplied it and it is already known.
3. For broad or scenario-style questions, call `krw_ontology_topic_map` for the relevant ticker before the first evidence query so the search uses company-specific vocabulary.
4. Start broad with compact search results: use `response_detail="compact"`, small limits, and object types such as `ResearchClaim`, `RiskFactor`, `Headwind`, `GrowthDriver`, and `EvidenceQuote`.
5. Refine with follow-up `krw_ontology_query` calls by topic/category/period. For key risks, prefer latest 10-K first, then use 10-Q only if the question asks for current or recent changes.
6. Trace the most important returned IDs with `krw_ontology_trace`. Trace accepts exact IDs and unique ID prefixes returned by query.
7. Check `krw_ontology_quality` before making a reliability statement.
8. Only use `response_detail="full"` when compact output lacks necessary fields; full output can be very large.

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
2. Call `krw_ontology_topic_map` for the relevant ticker when the factor/channel vocabulary is unclear or the question is broad.
3. Start with `krw_ontology_query` over `ExternalFactorExposure`, `RiskFactor`, `Headwind`, and `ResearchClaim` for the factor and benchmark terms.
4. Do not stop after the first result. Use terms from returned `impact_channel`, `related_metrics`, and quote text for follow-up queries. For price scenarios, check at least revenue, cost, margin, cash flow, and liquidity if those channels appear relevant.
5. Search separately for explicit threshold terms such as breakeven, covenant, minimum, deadline, termination, impairment, default, settlement, or liability cap when the user asks "how low/high is dangerous".
6. Treat direct quotes and `evidence_grade=direct` as stronger than indirect or derived evidence. If the object is strong but the numeric threshold is not found, say that the ontology has structural evidence but no explicit threshold.
7. If revenue and cost effects point in different directions, answer mixed or uncertain instead of forcing a single net effect.
8. Check `krw_ontology_quality` when the answer depends on completeness, latest-period coverage, rejected objects, or coverage-gap warnings.

## External Market Report To Company Impact Questions

Use this protocol when the user provides or references an outside market report, macro view, commodity outlook, policy update, geopolitical event, weather event, supply-demand balance, or industry thesis and asks what it means for companies. Examples include Argus/IEA/EIA/WoodMac gas or LNG reports, oil market balances, AI capex cycle reports, power market reports, regulatory proposals, tariffs, sanctions, supply disruptions, or demand shocks.

The key rule: the external report is a market premise, not SEC filing evidence. The ontology is still useful because it contains company-specific exposures, business activities, contracts, risks, and financial channels. Do not answer only "the ontology does not contain this market report." Instead, use the report premise to search company exposures and explain the bridge.

1. Separate the evidence layers before searching:
   - Market premise: facts, scenarios, prices, volumes, regulations, or events from the external report or user-provided text.
   - Company exposure: SEC filing ontology evidence about how each company is exposed.
   - Analyst bridge: your explicit inference connecting the market premise to company exposure.
2. Extract the market premise into normalized factors and channels. Examples:
   - TTF, JKM, LNG spot tightness, EU storage deficit, Hormuz disruption, Qatar supply outage -> `global_lng_price`, `international_lng_price`, `natural_gas_price`, `lng_demand`, `commodity_price_realization`, `supply_disruption`.
   - Henry Hub, feed gas, basis differential, transport cost -> `natural_gas_price`, `feed_gas_cost`, `cost_of_revenue`, `operating_margin`.
   - Carbon rules, permitting, sanctions, import bans, tariffs -> `regulatory_approval`, `environmental_regulation`, `trade_policy`, `sanctions`, `capital_expenditures`, `revenue`.
3. Select the company universe deliberately. Use the user's tickers if supplied. If not supplied, use `krw_ontology_catalog` and then screen likely exposed companies with `CompanyBusinessProfile`, `BusinessActivity`, and `ExternalFactorExposure`. Do not assume all energy or industrial companies have the same exposure.
4. For each relevant ticker, call `krw_ontology_topic_map` and search ontology by company exposure terms rather than only the market report's vocabulary. If "TTF" or "EU storage" returns nothing, search related company terms such as LNG price, international LNG, natural gas price, commodity price realization, feed gas, SPA, spot cargo, liquefaction, export terminal, customer demand, derivatives, fuel switching, or the asset/project names.
5. Query at least these object types for company impact: `CompanyBusinessProfile`, `BusinessActivity`, `ExternalFactorExposure`, `RiskFactor`, `Headwind`, `GrowthDriver`, `ResearchClaim`, and `EvidenceQuote`. For exact volumes, prices, capacity, dates, or guidance, also query quant objects.
6. Trace the strongest company-specific objects before finalizing. Prefer direct filing quotes and `ResearchClaim` evidence for factual company exposure. Use `ExternalFactorExposure` to organize direction and channels, but do not cite it alone if the conclusion depends on a precise fact.
7. Analyze both benefit and risk channels. For commodity or market-tightness reports, always check revenue/realized price, cost/feedstock, margin, cash flow/liquidity, capex/project timing, contracts/SPAs, customer performance, hedging/derivatives, and regulatory/geopolitical risk where relevant.
8. Keep the conclusion company-specific and avoid sector-general shortcuts:
   - LNG exporter with spot exposure may benefit from LNG price spikes, but feed gas cost, basis, shipping cost, SPA pricing, COD timing, or volatility may offset.
   - Integrated oil/gas companies may have indirect LNG or commodity realization exposure, but the effect can be diluted by upstream oil, refining, chemicals, or downstream segments.
   - Oilfield service companies may benefit from upstream capex cycles, not necessarily from gas prices directly.
9. Label inference strength:
   - Direct: the filing explicitly links the factor to a financial channel or contract/project exposure.
   - Indirect: the filing shows relevant exposure, but the market report factor is one step removed.
   - Inferred: the bridge is economically plausible but not directly disclosed in the filing.
   - Unsupported: do not present as a conclusion.
10. Final answer structure should separate:
   - Market premise: from the external report/user text.
   - Company filing evidence: company-specific disclosures summarized in plain language.
   - Impact bridge: your inference and confidence.
   - Business uncertainties and offsets: only substantive market, operational, contract, regulatory, or timing issues that change interpretation.
11. If ontology searches find no company-specific exposure for a ticker, say that the indexed filings did not surface a material exposure and explain why the company is likely less directly affected. Do not force a thesis.
12. If the user asks for investment implications, distinguish operational exposure from stock-price recommendation. The ontology supports exposure analysis, not a complete valuation call unless valuation inputs are separately provided.

## Question Type Protocols

- Factual lookup: query `ResearchClaim`, `EvidenceQuote`, `FinancialMetricValue`, `DerivedMetricValue`, and `NumericEvidence`; then trace the exact or near-exact match. Use high-level objects only as context.
- Risk overview: query `RiskFactor`, `Headwind`, `ExternalFactorExposure`, and `ResearchClaim`; prefer latest 10-K unless the user asks for recent quarterly changes.
- Change over time: query `ChangeEvent`, `TemporalLink`, `TrendObservation`, and comparable-period claims; do not state a trend from one period only.
- Metric explanation: query the metric first, then search related `ResearchClaim`, `ExternalFactorExposure`, `RiskFactor`, and `Headwind` using the metric name and major drivers found in the metric evidence.
- Business model: query `CompanyBusinessProfile`, `BusinessActivity`, and `ExternalFactorExposure`; check quality for coverage gaps before claiming the profile is complete.
- External market report impact: treat the external report as a premise, map it to company exposure factors and financial channels, query company-specific ontology evidence, then separate market premise, SEC filing evidence, and analyst bridge in the answer.
- Comparison: use `krw_ontology_compare`, then trace or query the most important differences per ticker/period.

## Final Answer Evidence Contract

The web UI may render evidence separately. The final natural-language answer should therefore be clean and reader-facing by default.

- Do not include raw ontology object IDs such as `claim:...`, `quote:...`, `risk:...`, `external_factor_exposure:...`, or `numeric_evidence:...` in the final answer unless the user explicitly asks for IDs, trace output, citations, or debugging.
- Do not paste original filing quote text by default. Summarize the evidence in your own words.
- Do not add lines like `근거: claim:...`, `Quote ...`, or `source_object_id: ...` in the visible answer.
- Do not include data coverage, index inventory, object counts, section-quality status, batch-failure counts, rejected-object counts, or catalog summaries in the final answer unless the user explicitly asks about coverage, quality, indexing, debugging, auditability, or data availability.
- Do not include a visible "quality note", "품질 노트", "coverage note", "debug note", or similar operational footer in normal customer-facing answers.
- Do not expose internal pipeline/tool terms in normal answers. Forbidden customer-facing terms include `section quality`, `batch failure`, `extract_assumption_candidates`, `rejected objects`, `agent_index`, `catalog`, `trace id`, `raw ontology ID`, `MCP`, `JSONL`, and Korean equivalents such as `섹션 품질`, `배치 실패`, `거절 객체`, `인덱스 상태`, `카탈로그`, `품질 노트`, and `디버그`.
- If an internal quality signal materially weakens a candidate point, omit that point or narrow the conclusion privately. Do not add visible generic caveats about evidence availability, missing quantification, extraction status, filing coverage, or quality unless the user explicitly asks for audit/debug/quality details.
- Do not expose ontology object type names in normal answers. Forbidden customer-facing type names include `ResearchClaim`, `EvidenceQuote`, `RiskFactor`, `GrowthDriver`, `Headwind`, `BusinessActivity`, `ExternalFactorExposure`, `NumericEvidence`, `XBRLFact`, `CompanyBusinessProfile`, `객체`, and `온톨로지 객체`.
- Do not paste source labels followed by original quote text. Use short source labels such as "출처: AAPL FY2025 10-K Item 7" only when useful; the quoted filing sentence belongs in hidden evidence or explicit audit/debug output.
- Still use `krw_ontology_trace` internally when the question requires it. Hidden trace verification should improve accuracy, not clutter the answer.
- Use human-readable source labels instead, such as "NVDA FY2026 10-K", "latest 10-Q", "company filing evidence", or "direct filing evidence".
- For exact dates, amounts, contract terms, and project milestones, say whether the support is direct filing evidence, indirect filing evidence, or an inference.
- If the user asks "근거 보여줘", "trace 해줘", "object id 줘", "원문 보여줘", "데이터 커버리지", "품질 상태", or requests audit/debug/export output, then provide the relevant IDs, short source excerpts, coverage, and quality details.

## Answering Rules

- Use ticker, document type, and period as reader-facing source labels; hide raw object IDs and quote snippets by default.
- Treat quality signals as internal controls. Do not mention them in customer-facing answers unless the user explicitly asks for quality, coverage, audit, or debug details.
- Do not cite rejected objects unless the user explicitly asks for rejected data.
- If evidence is weak, omit the unsupported point or phrase the substantive conclusion more narrowly. Do not add a separate evidence-quality disclaimer unless the user explicitly asks for evidence quality.
- Do not end customer-facing answers with operational follow-up prompts such as "더 구체적인 주제가 궁금하면 말씀해 주세요" unless the user is explicitly exploring next research directions.
- Prefer direct quotes and `ResearchClaim` objects over high-level theme objects when answering factual questions.
- Use 10-Q evidence for quarterly/current updates and 10-K evidence for annual or long-horizon context.
- For cross-company comparisons, state that comparison is ad-hoc unless a dedicated cross-company edge or temporal/thread object is returned.
- Do not claim that a risk/driver is new, removed, increasing, or decreasing over time unless you queried comparable periods and traced evidence from each period.
- Do not let web search override a direct filing value/date unless the web source is newer, specific, and explicitly updates that same fact.
- If the agent index is missing or stale, ask the user to run `uv run krw-ontology build-agent-index --root <ontology-root>` or rebuild via `build-research-pipeline`.

## Tools

See `references/tools.md` for concise tool selection guidance and parameters.
