# KRW Ontology Structure

Use this reference to choose the right ontology objects for a research question. Do not treat all returned objects as equally strong evidence.

## One-Line Model

```text
SourceDocument / SourceLocation / SourceSpan / SourceTable / SourceTableCell
-> EvidenceQuote / LanguageSignal
-> SupportLink
-> ResearchClaim / MetricObservation / Calculation
-> BusinessActivity / BusinessFactor / ExternalFactorExposure / AgreementTerm / BusinessEvent / AssumptionCandidate
-> Edge
-> CompanyBusinessProfile / TemporalLink / TrendObservation / ChangeEvent
-> agent_index.sqlite / MCP
```

`SupportLink` handles evidence/provenance. `Edge` handles semantic, business, graph, and temporal relationships. Do not merge these two roles.

## Core Interpretation Rule

Ontology retrieval has three separate questions:

```text
1. Did the system find a related object?
2. Does that object directly match the user's premise?
3. Is that object traceable to filing evidence or numeric lineage?
```

A related object is not automatically a direct answer. A direct-looking object without evidence chain is not strong evidence.

Final strong claims should be based on:

```text
traceable_direct
traceable_metric_lineage
```

Use `traceable_related` only for context. Do not promote `untraced_direct_candidate` or `broad_related_candidate` into direct conclusions.

## Governance Layer

### RunManifest

Run-level provenance for extraction and indexing.

Use for:

- Checking schema version, registry version, taxonomy version, agent index schema version, retrieval text builder version, support link builder version, pipeline version, model metadata, source hashes, and build root.
- Debugging why two extraction runs differ.
- Verifying that a clean-root rebuild was used after a breaking migration.

### OntologyRegistrySnapshot

Frozen registry contract for a run.

Use for:

- Determining which artifacts are canonical.
- Determining text-index fields, filter fields, reference fields, support fields, edge projection rules, compatibility aliases, compact display fields, trace policies, answer-candidate policies, and quality gates.
- Preventing stale JSONL files from entering the index.

### TaxonomyTerm

Versioned controlled term for factors, metrics, channels, roles, event types, agreement types, sectors, and aliases.

Use for:

- Mapping natural-language questions to canonical factor/metric/channel terms.
- Keeping sector packs extensible without adding new object types.
- Supporting query/topic facets such as domain term, mechanism, impact channel, generic modifier, benchmark, and sector.

Avoid:

- Treating taxonomy aliases as proof that a company has exposure. Exposure still requires object evidence and trace.

### ValidationReport

Validation summary for publish readiness.

Use for:

- Checking quote exact match, invalid references, support link resolution, edge resolution, metric validation, stale artifacts, rejected objects, trace coverage, orphan objects, weak-link counts, answerability negative-test failures, and quality warnings.
- Deciding whether an index is safe to use for answer generation.

## Source Layer

### SourceDocument

Filing-level object.

Use for:

- Filing metadata: ticker, CIK, accession number, document type, period end date, filing date, fiscal year/quarter, source URL, raw/clean text hashes.
- Reader-facing source labels such as "AAPL FY2025 10-K".

### SourceLocation

Precise source pointer that can represent text spans, table cells, XBRL facts, footnotes, or other locations.

Use for:

- Tracing evidence back to a document region.
- Keeping text, table, and XBRL evidence under one location model.

### SourceSpan

Clean filing text span.

Use for:

- Evidence quote verification.
- Section-level context and quote exact-match validation.

Avoid:

- Treating a full span as a final citation when a narrower `EvidenceQuote` exists.

### SourceTable

Detected filing table.

Use for:

- Debt maturities, contractual obligations, lease obligations, purchase commitments, market-risk sensitivity, segment revenue, remaining performance obligations, fair value hierarchy, and similar table evidence.

### SourceTableCell

Normalized table cell.

Use for:

- Table-derived numeric and contractual evidence.
- Linking table values to `MetricObservation`, `AgreementTerm`, or `Calculation`.

Rule:

- For table-derived amounts, prefer `SourceTableCell` + `MetricObservation` / `Calculation` over flattened text when both exist.

## Evidence Layer

### EvidenceQuote

Direct filing text. This is the strongest normal text evidence object.

Use for:

- Exact dates, amounts, project milestones, contract terms, targets, deadlines, named-asset details, and direct risk/driver disclosures.
- Verifying whether a claim is direct, indirect, broad-related, or over-interpreted.
- Final evidence verification before answering factual questions.

Avoid:

- Building a whole broad, comparative, or multi-period answer from one quote.

### LanguageSignal

Structured language signal inside or around a quote.

Use for:

- Distinguishing actual versus potential impact.
- Distinguishing targeted, expected, guided, estimated, conditional, or completed statements.
- Detecting legal/cautionary tone, uncertainty, mitigation, and obligation language.

### SupportLink

Evidence/provenance relationship object.

Use for:

- Tracing why a claim, metric, business factor, exposure, event, agreement term, or assumption is supported.
- Distinguishing direct, indirect, derived, contextual, qualifying, or refuting support.
- Connecting objects to `EvidenceQuote`, `ResearchClaim`, `MetricObservation`, `Calculation`, `SourceTableCell`, `XBRLFact`, or other support objects.

Direction rule:

```text
from_id = supporting evidence object
to_id   = supported target object
```

Examples:

```text
EvidenceQuote -> ResearchClaim
ResearchClaim -> BusinessFactor
ResearchClaim -> ExternalFactorExposure
XBRLFact -> MetricObservation
Calculation -> MetricObservation
```

Rule:

- For evidence tracing, follow `SupportLink` before using semantic `Edge` relationships.
- If a semantic object has no support chain, it is not strong evidence even if its text says it is evidence-backed.

## Entity Layer

### CanonicalEntity

Stable entity identity across filings and periods.

Use for:

- Companies, segments, products, services, projects, facilities, contracts, counterparties, regulatory authorities, benchmarks, geographies, and named assets.
- Multi-period matching and alias normalization.

Avoid:

- Returning `CanonicalEntity` as a top answer candidate for normal research. It is a support/search object, not a conclusion.

### EntityMention

Document-level mention of a canonical entity.

Use for:

- Checking which string appeared in which filing location.
- Debugging entity normalization and alias mapping.

## Claim Layer

### ResearchClaim

Atomic semantic statement extracted from one or more evidence quotes.

Important fields:

- `claim_text`: normalized claim.
- `claim_type`: examples include factual, risk_assessment, forward_looking, strategic, assumption, exposure, event, or agreement-related.
- Support links to quotes, metrics, calculations, or source locations.
- Semantic hints such as factor, channel, activity, materiality, related entity, or related metrics when available.
- Inference markers such as direct versus inferred support when available.

Use for:

- Factual lookup after query, before final citation.
- Risk, driver, event, agreement, and exposure summaries where quote-level detail is too granular.
- Forward-looking company targets, if you label them as targeted/expected/guided.

Rule:

- For exact factual answers, trace the claim before answering. A claim snippet alone is not enough when a date, amount, contract term, or project status is central.

## Numeric Layer

### XBRLFact

Raw XBRL fact. Use when you need reported facts but expect detail, dimensions, contexts, and duplicates.

Use for:

- Source-level numeric trace.
- Checking XBRL concept, context, unit, period, and dimensions.

Avoid:

- Showing raw XBRL facts before normalized `MetricObservation` unless debugging or auditing.

### MetricObservation

Canonical numeric observation for reported, table-backed, or derived metrics.

Use for:

- Revenue, net income, cash, debt, capex, operating cash flow, segment metrics, and similar financial statement values.
- Segment, geography, product, or customer-type dimensions.
- Reported, normalized, derived, or table-backed metrics when unit/period/context are explicit.

Important fields:

- metric term/name
- value
- unit
- scale
- period / period start / period end / period type
- fiscal year / quarter
- source type
- source fact IDs / source table cell IDs / calculation ID
- dimensions

Rule:

- `MetricObservation` can be traceable without quotes when it has XBRL, table, or calculation lineage.
- Do not compare metrics across annual and quarterly periods unless the object or calculation explicitly supports comparability.

### Calculation

Deterministic calculation object.

Use for:

- Derived metrics, ratios, growth rates, deltas, margins, percentages, and bridges.
- Checking formula, input metrics, output metric, rounding policy, and method.

Rule:

- Never let an LLM-derived numeric claim pass without `MetricObservation` or `Calculation` support.

## Business Semantic Layer

### BusinessActivity

What the company does or what economics it participates in.

Use for:

- Business model explanations.
- Revenue sources, cost sources, projects, operations, production, procurement, sales, financing, or customer relationships.

Rule:

- BusinessActivity is useful context but should not be cited alone for exact facts.

### BusinessFactor

Unified semantic object for risk, driver, headwind, tailwind, pressure, opportunity, or watch item.

Use for:

- Risk overview.
- Growth driver overview.
- Headwind/tailwind analysis.
- Broad business implications tied to claims and quotes.

Important fields:

- `factor_roles`: risk, growth_driver, driver, headwind, tailwind, watch_item, etc.
- category
- affected channels
- occurrence status
- materiality basis
- specificity / boilerplate indicators when available.

Rule:

- `RiskFactor`, `GrowthDriver`, and `Headwind` are presentation views over `BusinessFactor`, not independent canonical objects.
- A BusinessFactor without support chain can be a candidate, but not strong evidence.

### ExternalFactorExposure

How an external factor affects the company through business or financial channels.

Use for:

- Scenario and sensitivity questions.
- Commodity, rate, FX, regulatory, demand, supply, geopolitical, climate, or policy exposure.
- Questions like "what happens if factor X rises/falls/changes?"

Important fields:

- external factor / benchmark
- affected channels
- mechanism
- `scenario_effects`
- pass-through / offsets / conditions
- evidence grade
- materiality basis

Rule:

- Do not treat the existence of an `ExternalFactorExposure` object as direct exposure to any user premise. Directness depends on whether the query premise matches the object evidence.
- A broad commodity exposure is not direct evidence for a semiconductor/GPU/HBM price-cycle question unless those required facets are present.
- Publishable ExternalFactorExposure should have `scenario_effects` unless explicitly incomplete/rejected.

### AssumptionCandidate

Potential modeling input candidate, not a valuation conclusion.

Use for:

- DCF or model assumption support.
- Growth, margin, capex, tax, WACC, utilization, volume, or project timing assumption candidates.

Rule:

- Do not present an AssumptionCandidate as a recommendation or final valuation input unless downstream modeling explicitly chooses it.

### AgreementTerm

Specific economic, legal, financing, lease, covenant, purchase, or contract term.

Use for:

- SPA, offtake, supply, debt, credit facility, lease, covenant, take-or-pay, purchase commitment, maturity, pricing formula, termination, renewal, counterparty, collateral, or obligation terms.

Rule:

- Do not create or cite visible agreement-term units from generic contract language without specific party, amount, term, economic role, timing, or obligation detail.

### BusinessEvent

Business event or disclosed event-like item.

Use for:

- Project milestones, regulatory proceedings, litigation events, financing events, guidance updates, product launches, approvals, denials, delays, completions, or status changes.

Rule:

- Label status precisely: targeted, expected, pending, approved, denied, completed, delayed, accelerated, withdrawn, updated.
- Use `ChangeEvent` instead when the subject is disclosure change across filings rather than the business event itself.

## Company Context Layer

### CompanyBusinessProfile

Company-level summary and query map.

Use for:

- Search planning.
- Business model orientation.
- Finding key activities, exposures, metrics, sectors, and vocabulary.

Rule:

- It is not final evidence for exact facts. Trace supporting objects before answering.

### CompanyTopicProfile / company_topic_index

Serving-derived company topic row used for large-universe discovery. This may live inside `agent_index.sqlite`, not canonical JSONL.

Use for:

- Matching a question's context to evidence-derived company topics.
- Ticker discovery before focused query.
- Avoiding brute-force topic_map/query calls across every ticker.

A good topic is specific and evidence-backed:

```text
LNG and feed gas price exposure
SPA termination and debt acceleration risk
FERC/DOE permitting delay risk
Henry Hub feed gas margin pressure
```

A weak topic is generic:

```text
business_activity
project
revenue_source
risk
external_factor
```

Rule:

- Company topics are planning objects. Final answers must trace selected source objects.

### TemporalLink

Cross-period identity link between objects.

Use for:

- Determining whether an issue, exposure, project, event, agreement, or metric is the same across filings.

Rule:

- Do not infer a period-over-period change without temporal linkage or direct comparable evidence.

### TrendObservation

Multi-period metric or qualitative trend observation.

Use for:

- Trends only when comparable periods are represented.
- Metric series, factor recurrence, or repeated disclosure direction.

Rule:

- Do not state an increasing/decreasing trend from one period or from non-comparable annual-vs-quarterly periods unless the object explicitly supports that comparison.

### ChangeEvent

Ontology-detected change between filings.

Use for:

- "What changed?" questions.
- New, removed, intensified, reduced, delayed, accelerated, or materially modified disclosures.

Rule:

- Trace back to source period evidence before saying something changed.

## Graph Layer

### Edge

Semantic graph relationship object.

Use for:

- Business factor to exposure relationships.
- Claim to semantic object relationships.
- Entity, activity, event, agreement, metric, temporal, and context relationships.
- Chain exploration.

Rule:

- Use `Edge` to understand connected business meaning. Use `SupportLink` to verify evidence.

## Answerability And Evidence Strength

### Core Tiers

```text
traceable_direct
  The question premise directly matches the evidence premise, and SupportLink or metric lineage resolves.

traceable_related
  The evidence is traceable but broader, adjacent, or indirect relative to the question.

untraced_direct_candidate
  The object appears directly relevant but lacks explicit support chain.

broad_related_candidate
  The object is broadly related, but it does not answer the direct premise.

no_direct_evidence
  Direct evidence was not found; negative answer may be supported if search coverage is adequate.

not_answerable
  The index cannot support a reliable answer.
```

### Direct Exposure Checks

When the question asks whether a company is directly exposed to a specific factor, product, commodity, market, customer, technology, country, project, or contract:

- Require the requested premise or its evidence-derived facet to appear in traceable evidence.
- Do not promote broader commodity, margin, cost, revenue, geopolitical, supply-chain, or market-volatility evidence into direct evidence.
- If broader related evidence exists, answer with `no_direct_evidence` plus related context.

Example:

```text
Question: Is VG directly exposed to semiconductor memory price cycle or GPU HBM price volatility?
Correct: No direct evidence. Related broad commodity/feed gas/LNG price risk may exist.
Incorrect: VG has commodity price exposure, therefore it is directly exposed to HBM prices.
```

### Numeric Evidence

Strong numeric evidence can be:

```text
MetricObservation -> XBRLFact -> SourceDocument
MetricObservation -> SourceTableCell -> SourceDocument
MetricObservation -> Calculation -> input MetricObservation(s) -> XBRLFact / SourceTableCell / SourceDocument
```

Quote support is useful but not required for metric traceability.

## Object Priority By Question Type

### Exact Facts, Dates, Amounts, Contract Terms, Project Milestones

Search first:

- `ResearchClaim`
- `EvidenceQuote`
- `MetricObservation`
- `Calculation`
- `XBRLFact`
- `AgreementTerm`
- `BusinessEvent`
- `SourceTableCell` when table-derived

Then use:

- `BusinessActivity`
- `BusinessFactor`
- `ExternalFactorExposure`

Trace before final answer.

### Scenario And Sensitivity

Search first:

- `ExternalFactorExposure`
- `BusinessFactor`
- `ResearchClaim`
- `EvidenceQuote`

Then use:

- `BusinessActivity`
- `AgreementTerm` for contract pass-through, hedging, commitments, or debt/covenant conditions.
- `MetricObservation` or `Calculation` for thresholds and amounts.

Require `scenario_effects` or an explicit traced analyst bridge before stating metric direction or company effect.

### Direct Exposure / Negative Check

Search broadly, but answer strictly:

- Use `ResearchClaim`, `EvidenceQuote`, `ExternalFactorExposure`, and `BusinessFactor`.
- Evaluate matched required facets and missing required facets.
- If traceable evidence is only broad-related, answer `no_direct_evidence` and provide related context separately.

### Risk Overview

Search first:

- `BusinessFactor`
- `ExternalFactorExposure`
- `ResearchClaim`

Trace:

- top risks
- surprising risks
- risks tied to explicit metrics, contracts, events, or projects.

### Growth / Driver

Search first:

- `BusinessFactor`
- `BusinessActivity`
- `ResearchClaim`
- `EvidenceQuote`
- `ExternalFactorExposure` when a driver depends on an external factor.

Avoid:

- Using a driver label alone without quote/claim support.

### Business Model

Search first:

- `CompanyBusinessProfile`
- `BusinessActivity`
- `BusinessFactor`
- `ExternalFactorExposure`

Then trace:

- claims/quotes behind key activities, factors, and exposures.

### Metric Explanation

Search first:

- `MetricObservation`
- `Calculation`
- `XBRLFact`
- relevant `SourceTableCell` evidence

Then use:

- `ResearchClaim`
- `ExternalFactorExposure`
- `BusinessFactor`

### Contract / Obligation / Capital Structure

Search first:

- `AgreementTerm`
- `MetricObservation`
- `Calculation`
- `SourceTableCell`
- `EvidenceQuote`
- `ResearchClaim`

Then use:

- `BusinessFactor` for liquidity, covenant, interest-rate, or maturity pressure context.

### Project / Regulatory / Guidance Event

Search first:

- `BusinessEvent`
- `ResearchClaim`
- `EvidenceQuote`
- `AgreementTerm` when contract-linked
- `MetricObservation` when amount/capacity/capex-linked

Then use:

- `BusinessFactor` and `ExternalFactorExposure` for risk/channel interpretation.

### Change Over Time

Search first:

- `ChangeEvent`
- `TemporalLink`
- `TrendObservation`

Then query:

- comparable-period claims, quotes, metrics, business factors, exposures, events, and agreement terms.

### Large-Universe Company Discovery

Do not call topic_map or full query for every ticker.

Use this order:

```text
QueryFrame / question context
-> company_topic_index or global compact discovery
-> candidate ticker narrowing
-> focused query for shortlisted tickers
-> trace selected objects
```

## Compatibility Mapping

The internal canonical structure is:

```text
Governance: RunManifest, OntologyRegistrySnapshot, TaxonomyTerm, ValidationReport
Source: SourceDocument, SourceLocation, SourceSpan, SourceTable, SourceTableCell
Evidence: EvidenceQuote, LanguageSignal, SupportLink
Entity: CanonicalEntity, EntityMention
Claim: ResearchClaim
Numeric: XBRLFact, MetricObservation, Calculation
Business: BusinessActivity, BusinessFactor, ExternalFactorExposure, AssumptionCandidate, AgreementTerm, BusinessEvent
Context: CompanyBusinessProfile, TemporalLink, TrendObservation, ChangeEvent
Serving-derived: CompanyTopicProfile / company_topic_index, QueryFrame, EvidenceFrame, retrieval_text, answerability tiers
Graph: Edge
```

Compatibility mapping:

```text
RiskFactor / GrowthDriver / Headwind -> BusinessFactor role views
FinancialMetricValue / DerivedMetricValue -> MetricObservation views
NumericEvidence / CalculatedNumericSupport -> MetricObservation + Calculation + SupportLink views
ProjectMilestone / RegulatoryProceeding / GuidanceItem -> BusinessEvent subtypes
ContractTerm / CapitalStructureItem / Lease / Covenant -> AgreementTerm subtypes
SegmentPerformance -> MetricObservation dimensions
```
