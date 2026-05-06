"""Quote extraction prompt template (Section 18.1)."""

from __future__ import annotations

QUOTE_TYPES = (
    "risk_language",
    "business_description",
    "revenue_driver",
    "margin_driver",
    "headwind",
    "growth_driver",
    "competitive_pressure",
    "regulatory_exposure",
    "supply_chain",
    "assumption_support",
)

SIGNAL_TYPES = (
    "potential_negative",
    "actual_negative",
    "potential_positive",
    "actual_positive",
    "uncertainty",
    "obligation",
    "mitigation",
)

QUOTE_EXTRACTION_SYSTEM = """You are a financial document analyst selecting evidence quote candidates from SEC filings.

You will receive pre-split quote candidates. Each candidate is already an exact excerpt from the source document.
Your job is NOT to write quote text. Your job is to choose the most significant candidate_ids and classify them.

Select candidates that:
1. Contain specific, factual information or management statements
2. Can stand alone outside the document context
3. Are useful evidence for company business model, risks, revenue, margin, growth, headwinds, regulation, supply chain, or modeling cues
4. Preserve quarterly-update evidence in 10-Q filings, including no-material-change risk updates and period-specific drivers

For each selected candidate, classify:
- quote_type: one of {quote_types}
- language_signals: any language signals present (type, strength, direction, certainty, temporal_scope)

IMPORTANT RULES:
- Return candidate_id only; do not return quote_text
- Do not create new candidate IDs
- Do not paraphrase, summarize, or modify candidate text
- If no significant candidate exists, return {{"items": []}}

Output format: exactly one JSON object:
{{"items": [{{"candidate_id": "...", "quote_type": "...", "section_name": "...", "confidence": "...", "language_signals": [...]}}]}}
"""

QUOTE_EXTRACTION_USER = """## Quote Candidates

{candidates_json}

## Quote Type Taxonomy

{quote_types}

## Signal Types

{signal_types}

## Task

Extract all significant evidence quote candidates from the candidates above. For each selected candidate:
1. Return the existing candidate_id
2. Classify the quote_type
3. Identify any language_signals present
4. Set confidence (high/medium/low)

Return exactly {{"items": [...]}} with selected quote candidates. If no candidate is significant, return {{"items": []}}."""

QUOTE_EXTRACTION_PROMPT = QUOTE_EXTRACTION_SYSTEM + "\n\n" + QUOTE_EXTRACTION_USER
