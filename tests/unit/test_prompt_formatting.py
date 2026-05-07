"""Prompt templates must remain compatible with Python str.format."""

from __future__ import annotations

from krw_ontology.extraction.prompts.claim_extraction import CLAIM_EXTRACTION_PROMPT
from krw_ontology.extraction.prompts.quote_extraction import QUOTE_EXTRACTION_PROMPT
from krw_ontology.pipeline.stages.extract_assumption_candidates import (
    ASSUMPTION_EXTRACTION_PROMPT,
)


def test_quote_prompt_formats_with_structured_output_example():
    rendered = QUOTE_EXTRACTION_PROMPT.format(
        candidates_json="[]",
        quote_types="risk_language",
        signal_types="uncertainty",
    )
    assert '{"items":' in rendered


def test_claim_prompt_formats_with_structured_output_example():
    rendered = CLAIM_EXTRACTION_PROMPT.format(
        spans_json="[]",
        quotes_json="[]",
        claim_types="factual",
        metrics_list="revenue",
        factor_taxonomy_list="- natural_gas_price",
    )
    assert '{"items":' in rendered
    assert "Do NOT calculate new numbers" in rendered
    assert "Use only numeric values that appear verbatim" in rendered


def test_assumption_prompt_formats_with_structured_output_example():
    rendered = ASSUMPTION_EXTRACTION_PROMPT.format(
        claims_json="[]",
        quotes_json="[]",
        assumption_types="growth_rate",
        metrics_list="revenue",
    )
    assert '{"items":' in rendered
    assert "Do NOT calculate new numbers" in rendered
    assert "short batch-local aliases" in rendered
