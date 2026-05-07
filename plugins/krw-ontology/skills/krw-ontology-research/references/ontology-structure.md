# KRW Ontology Structure

Use this reference to choose the right ontology objects for a research question. Do not treat all returned objects as equally strong evidence.

## Evidence Layer

### SourceSpan

`SourceSpan` is a chunk of source filing text. It is useful when the user needs context around a quote or when a quote snippet is too short.

Use for:
- Checking surrounding wording.
- Distinguishing a company disclosure from a table heading, footnote, or unrelated paragraph.
- Verifying whether a statement is qualified by words such as "targeted", "expected", "may", "could", or "subject to".

### EvidenceQuote

`EvidenceQuote` is the strongest normal evidence object because it is directly anchored to filing text.

Use for:
- Exact dates, amounts, project milestones, contract terms, targets, deadlines, and named-asset details.
- Final citations in factual answers.
- Verifying whether a claim is direct, indirect, or over-interpreted.

Avoid:
- Building a whole answer from one quote if the question is broad, comparative, or multi-period.

## Claim Layer

### ResearchClaim

`ResearchClaim` is an atomic semantic statement extracted from one or more evidence quotes.

Important fields:
- `claim_text`: normalized claim.
- `claim_type`: examples include factual, risk_assessment, forward_looking, strategic, assumption.
- `supported_by_quotes`: quote IDs that should be traced for important answers.
- semantic hints such as factor, channel, activity, materiality, or related metrics when available.

Use for:
- Factual lookup after query, before final citation.
- Risk and driver summaries where quote-level detail is too granular.
- Forward-looking company targets, if you label them as targeted/expected/guided.

Rule:
- For exact factual answers, trace the claim before answering. A claim snippet alone is not enough when a date, amount, or contract term is central.

## Quant Layer

### XBRLFact

Raw XBRL fact. Use when you need reported facts but expect lots of detail and duplicates.

### FinancialMetricValue

Canonical financial metric derived from XBRL. Prefer this over raw `NumericEvidence` for reported financial statement values when available.

Use for:
- Revenue, net income, cash, debt, capex, operating cash flow, segment metrics, and similar financial statement values.

### DerivedMetricValue

Calculated metric derived from one or more inputs. Use only when the formula and input objects are clear enough for the answer.

### NumericEvidence

Number extracted from text or quotes. It has broader recall but weaker meaning than `FinancialMetricValue`.

Use for:
- Tables or narrative disclosures not captured as canonical XBRL metrics.
- Capacity, volumes, phase sizes, contract counts, rates, dates, and percentages.

Rule:
- For important numbers, trace or inspect the source quote/span. Do not trust isolated numeric evidence without context.

## Semantic Business Layer

### ExternalFactorExposure

Normalized exposure to external factors such as commodity prices, rates, regulation, demand, supply, FX, inflation, weather, or counterparty conditions.

Use for:
- Scenario and sensitivity questions.
- "What happens if X rises/falls?" questions.
- Finding channels such as revenue, cost_of_revenue, operating_margin, cash_flow, liquidity, capex, or debt service.

Evidence interpretation:
- `evidence_grade=direct`: factor/channel is close to quote evidence; suitable as core support.
- `evidence_grade=indirect`: useful support but trace before relying on it.
- `evidence_grade=derived`: structural interpretation. Use cautiously and say the evidence is structural, not an explicit threshold.
- `evidence_grade=unsupported`: do not use as final support.

### BusinessActivity

Company activity, segment, project, product line, or revenue/cost activity.

Use for:
- Business model questions.
- Connecting exposures to what the company actually does.
- Checking whether a scenario affects revenue, costs, production, capacity, or operations.

### RiskFactor

Specific risk object supported by claims.

Use for:
- Risk overview.
- Legal, regulatory, operational, financial, commodity, market, execution, cyber, and liquidity risks.

Avoid:
- Using high-level risk objects as the first source for exact dates or amounts. Query claims/quotes/quant objects first for factual lookup.

### Headwind

Aggregated negative theme, often tied to metrics or business performance.

Use for:
- Summarizing risk themes and pressure areas.
- Prioritizing follow-up tracing.

Avoid:
- Treating a headwind as direct proof without tracing its supporting claims and quotes.

### GrowthDriver

Positive business driver or tailwind.

Use for:
- Growth thesis, business momentum, demand, pricing, volume, project ramp, or margin expansion questions.

### AssumptionCandidate

Extracted assumption or model-relevant dependency.

Use for:
- Building investment model assumptions.
- Identifying key dependencies that need external validation.

## Company Context Layer

### CompanyBusinessProfile

Company-level synthesis built from multiple objects and periods.

Use for:
- Business model overview.
- Key activities, key exposures, and company-level context.

Rule:
- Treat it as a map, not final proof. Trace its key supporting objects for important claims.

### TemporalLink

Link between related objects across periods.

Use for:
- Determining whether a risk, exposure, activity, or claim continues across filings.

### TrendObservation

Multi-period observation. Use for trends only when comparable periods are represented.

Rule:
- Do not state an increasing/decreasing trend from one period or from non-comparable annual-vs-quarterly periods unless the object explicitly supports that comparison.

### ChangeEvent

Company-level change detected between filings.

Use for:
- "What changed?" questions.
- New, removed, intensified, reduced, or materially modified disclosures.

Rule:
- Trace back to source period evidence before saying something changed.

## Object Priority By Question Type

### Exact Facts, Dates, Amounts, Contract Terms, Project Milestones

Search first:
- `ResearchClaim`
- `EvidenceQuote`
- `FinancialMetricValue`
- `DerivedMetricValue`
- `NumericEvidence`

Then use:
- `BusinessActivity`
- `RiskFactor`
- `Headwind`

Common attributes:
- COD, commercial operation date, FID, final investment decision, financial close, in-service date.
- capacity, volume, price, fee, maturity, covenant, deadline, termination, liability cap.
- target, guidance, expected, estimated, planned, forecast.

### Scenario And Sensitivity

Search first:
- `ExternalFactorExposure`
- `ResearchClaim`
- `EvidenceQuote`

Then use:
- `RiskFactor`
- `Headwind`
- `BusinessActivity`
- quant objects for thresholds and amounts.

### Risk Overview

Search first:
- `RiskFactor`
- `Headwind`
- `ExternalFactorExposure`
- `ResearchClaim`

Trace:
- the top risks, any surprising risks, and any risks tied to explicit metrics.

### Business Model

Search first:
- `CompanyBusinessProfile`
- `BusinessActivity`
- `ExternalFactorExposure`

Then trace:
- claims/quotes behind key activities and exposures.

### Metric Explanation

Search first:
- `FinancialMetricValue`
- `DerivedMetricValue`
- `NumericEvidence`

Then use:
- `ResearchClaim`
- `ExternalFactorExposure`
- `RiskFactor`
- `Headwind`

### Change Over Time

Search first:
- `ChangeEvent`
- `TrendObservation`
- `TemporalLink`

Then query:
- comparable-period claims, quotes, and metrics.

## Evidence Strength Rules

1. Direct quote plus traced claim is strongest.
2. FinancialMetricValue is stronger than raw NumericEvidence for standard financial metrics.
3. Semantic objects are useful for retrieval and synthesis, but final factual answers should trace back to quotes or quant evidence.
4. Company context objects are maps and summaries; do not cite them alone for exact facts.
5. Web evidence is supplemental unless it is newer and directly updates the filing disclosure.
