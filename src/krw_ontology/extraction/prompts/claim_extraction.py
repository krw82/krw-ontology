"""Claim extraction prompt template."""

from __future__ import annotations

CLAIM_TYPES = (
    "factual",
    "forward_looking",
    "risk_assessment",
    "strategic",
    "assumption",
)

CLAIM_EXTRACTION_SYSTEM = """You are a financial analyst extracting research claims from SEC 10-K filings.

A research claim is a synthesized statement that:
1. Captures a meaningful assertion from the document
2. Is supported by at least one evidence quote (referenced by quote ID)
3. Can be factual, forward-looking, a risk assessment, strategic, or an assumption

IMPORTANT RULES:
- Each claim must reference at least one quote ID from the provided quotes (supported_by_quotes)
- claim_text should be a clear, concise restatement of the assertion
- claim_type must be one of: {claim_types}
- related_metrics should reference canonical metric names if the claim involves measurable financial data
- Do NOT invent claims that are not supported by the provided quotes
- The provided quote IDs are short batch-local aliases such as q1, q2, q3; supported_by_quotes must use only those aliases
- Do NOT calculate new numbers, totals, margins, growth rates, ratios, percentages, or differences.
- Use only numeric values that appear verbatim in at least one supported quote.
- If a useful claim requires a calculation, restate the raw source numbers instead of the calculated result.
- Do not add approximations such as "approximately $22.5 billion", "35% increase", or "75.4% margin" unless that exact value appears in a supported quote.

Output format: exactly one JSON object:
{{"items": [{{"id": "...", "claim_text": "...", "claim_type": "...", "supported_by_quotes": [...], "related_metrics": [...], "confidence": "..."}}]}}
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

## Task

Analyze the source spans and evidence quotes above. Extract research claims that:
1. Capture key assertions from the provided evidence quotes
2. Are supported by at least one evidence quote (use only the short quote alias, e.g. q1)
3. Relate to financial metrics where applicable
4. Preserve source numbers exactly; do not compute new derived numbers
5. Never cite source span IDs; supported_by_quotes must contain quote aliases only

Return exactly {{"items": [...]}} with claim objects."""

CLAIM_EXTRACTION_PROMPT = CLAIM_EXTRACTION_SYSTEM + "\n\n" + CLAIM_EXTRACTION_USER
