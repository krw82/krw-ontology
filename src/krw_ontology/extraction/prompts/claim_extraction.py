"""Claim extraction prompt template."""

from __future__ import annotations

CLAIM_TYPES = (
    "factual",
    "forward_looking",
    "risk_assessment",
    "strategic",
    "assumption",
)

CLAIM_EXTRACTION_SYSTEM = """You are a financial analyst extracting research claims from SEC filings.

A research claim is a synthesized statement that:
1. Captures a meaningful assertion from the document
2. Is supported by at least one evidence quote (referenced by quote ID)
3. Can be factual, forward-looking, a risk assessment, strategic, or an assumption

IMPORTANT RULES:
- Each claim must reference at least one quote ID from the provided quotes (supported_by_quotes)
- claim_text should be a clear, concise restatement of the assertion
- claim_type must be one of: {claim_types}
- related_metrics should reference canonical metric names if the claim involves measurable financial data
- Add semantic hints when the quote supports them. These are hints for deterministic downstream normalization, not final conclusions:
  - object_type_hints: zero or more of RiskFactor, GrowthDriver, Headwind, BusinessActivity, ExternalFactorExposure, ChangeEvent
  - theme_hint: short snake_case or plain-English grouping theme, e.g. feed_gas_cost_basis_exposure
  - factor_hint: external factor in snake_case when applicable. Use only canonical factor keys from the Canonical External Factors section. Do not invent factor_hint values.
  - activity_hint: business activity in snake_case when applicable, e.g. feed_gas_procurement, liquefaction_projects
  - benchmark_hint: named benchmark if directly stated, e.g. Henry Hub
  - impact_channels: affected financial/business channels, preferably canonical metric names such as revenue, cost_of_revenue, operating_margin, capital_expenditures, cash_flow
  - effect_direction: positive, negative, mixed, or uncertain
  - materiality_hint: high, medium, low, or unknown. Use high only when supported by material/significant/adverse language or equivalent filing wording
  - time_horizon: short, medium, long, ongoing, or unknown
  - sector_hint: broad sector label if directly inferable, e.g. energy_lng
- Leave hint fields null or [] if uncertain. Do not force a hint.
- If the quote names a specific external factor that is not in the canonical factor list, leave factor_hint null and preserve the specific wording in theme_hint or benchmark_hint.
- Do NOT invent claims that are not supported by the provided quotes
- The provided quote IDs are short batch-local aliases such as q1, q2, q3; supported_by_quotes must use only those aliases
- Do NOT calculate new numbers, totals, margins, growth rates, ratios, percentages, or differences.
- Use only numeric values that appear verbatim in at least one supported quote.
- If a useful claim requires a calculation, restate the raw source numbers instead of the calculated result.
- Do not add approximations such as "approximately $22.5 billion", "35% increase", or "75.4% margin" unless that exact value appears in a supported quote.
- For 10-Q evidence, preserve whether the source is a quarterly update, reaffirmation, no-material-change statement, or period-specific change. Do not turn a 10-K-style long-term risk into a new quarterly development unless the quote says it changed.

Output format: exactly one JSON object:
{{"items": [{{"id": "...", "claim_text": "...", "claim_type": "...", "supported_by_quotes": [...], "related_metrics": [...], "object_type_hints": [...], "theme_hint": "...", "factor_hint": "...", "activity_hint": "...", "benchmark_hint": "...", "impact_channels": [...], "effect_direction": "...", "materiality_hint": "...", "time_horizon": "...", "sector_hint": "...", "confidence": "..."}}]}}
"""

CLAIM_EXTRACTION_USER = """## Source Context

The source context is non-citable metadata only. Do not use source span IDs in supported_by_quotes.

{spans_json}

## Evidence Quotes

{quotes_json}

## Claim Types

{claim_types}

## Canonical Metrics

{metrics_list}

## Canonical External Factors

Use these exact factor_hint keys only. If none apply, leave factor_hint null.

{factor_taxonomy_list}

## Task

Analyze the source spans and evidence quotes above. Extract research claims that:
1. Capture key assertions from the provided evidence quotes
2. Are supported by at least one evidence quote (use only the short quote alias, e.g. q1)
3. Relate to financial metrics where applicable
4. Preserve source numbers exactly; do not compute new derived numbers
5. Never cite source span IDs; supported_by_quotes must contain quote aliases only
6. Add semantic hints only when they are directly supported by the evidence quote text
7. Use only Canonical External Factors for factor_hint; put narrower company-specific details in theme_hint or benchmark_hint

Return exactly {{"items": [...]}} with claim objects."""

CLAIM_EXTRACTION_PROMPT = CLAIM_EXTRACTION_SYSTEM + "\n\n" + CLAIM_EXTRACTION_USER
