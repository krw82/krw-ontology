"""Build structured numeric evidence ledger from quote, XBRL, and metric rows."""

from __future__ import annotations

import logging
from pathlib import Path

from krw_ontology.errors import PipelineStageError
from krw_ontology.schema.id_utils import (
    generate_numeric_evidence_local_id,
    generate_scoped_id,
)
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import read_jsonl, write_jsonl
from krw_ontology.validators.numeric_guard import NumericToken, _extract_numbers

logger = logging.getLogger("krw_ontology")

_FACT_FILES = (
    "xbrl_facts.jsonl",
    "financial_metric_values.jsonl",
    "derived_metric_values.jsonl",
    "calculated_numeric_support.jsonl",
)


def build_numeric_evidence(
    *,
    ontology_dir: Path,
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
) -> dict:
    """Write numeric_evidence.jsonl as the canonical numeric support ledger.

    The ledger is intentionally company-neutral. It records numbers that are
    already grounded in quote text, XBRL facts, metric values, or deterministic
    code-calculated rows. Validators can then match AI claim numbers against
    this structured evidence instead of reparsing company-specific prose.
    """
    try:
        rows: list[dict] = []
        seen: set[tuple[str, str, str, str, float]] = set()

        for quote in read_jsonl(ontology_dir / "evidence_quotes.jsonl"):
            for token in _extract_numbers(str(quote.get("quote_text") or "")):
                _append_row(
                    rows,
                    seen,
                    ticker=ticker,
                    period=period,
                    doc_type_key=doc_type_key,
                    document_type=document_type,
                    source_document_id=source_document_id,
                    token=token,
                    source_object_id=str(quote["id"]),
                    source_field="quote_text",
                    source_method="quote_text",
                    source_quote_id=str(quote["id"]),
                )

        for filename in _FACT_FILES:
            for fact in read_jsonl(ontology_dir / filename):
                token = _token_from_fact(fact)
                if token is None:
                    continue
                _append_row(
                    rows,
                    seen,
                    ticker=ticker,
                    period=period,
                    doc_type_key=doc_type_key,
                    document_type=document_type,
                    source_document_id=source_document_id,
                    token=token,
                    source_object_id=str(fact["id"]),
                    source_field="value",
                    source_method=_source_method(fact),
                    source_quote_id=fact.get("source_quote_id"),
                    formula=fact.get("formula"),
                    input_object_ids=fact.get("input_object_ids") or fact.get("input_metric_ids") or [],
                    unit_override=str(fact.get("unit") or ""),
                )

        output_path = ontology_dir / "numeric_evidence.jsonl"
        write_jsonl(output_path, rows)
        logger.info(
            "build_numeric_evidence: wrote %d rows",
            len(rows),
            extra={"stage": "build_numeric_evidence"},
        )
        return {"numeric_evidence": rows, "output_path": output_path}
    except PipelineStageError:
        raise
    except Exception as exc:
        raise PipelineStageError(f"build_numeric_evidence: {exc}") from exc


def _append_row(
    rows: list[dict],
    seen: set[tuple[str, str, str, str, float]],
    *,
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
    token: NumericToken,
    source_object_id: str,
    source_field: str,
    source_method: str,
    source_quote_id: str | None = None,
    formula: str | None = None,
    input_object_ids: list[str] | None = None,
    unit_override: str = "",
) -> None:
    key = (source_object_id, source_field, source_method, token.kind, token.value)
    if key in seen:
        return
    seen.add(key)

    local_id = generate_numeric_evidence_local_id(
        source_object_id,
        source_field,
        source_method,
        token.kind,
        str(token.value),
        token.label,
    )
    rows.append({
        "id": generate_scoped_id("numeric_evidence", ticker, period, doc_type_key, local_id),
        "type": "NumericEvidence",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "numeric_kind": token.kind,
        "evidence_role": _evidence_role(token),
        "value": token.value,
        "raw_text": token.label,
        "unit": unit_override or _unit_for_token(token),
        "source_object_id": source_object_id,
        "source_field": source_field,
        "source_method": source_method,
        "source_quote_id": source_quote_id,
        "formula": formula,
        "input_object_ids": input_object_ids or [],
        "schema_version": SCHEMA_VERSION,
    })


def _token_from_fact(fact: dict) -> NumericToken | None:
    value = fact.get("value")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    fact_type = str(fact.get("type") or "")
    unit = str(fact.get("unit") or "").lower()
    label = str(fact.get("display_value") or fact.get("value"))
    if unit == "percent":
        return NumericToken("percent", parsed, label)
    if unit in {"usd", "usd_per_share"} or fact_type in {
        "XBRLFact",
        "FinancialMetricValue",
        "CalculatedNumericSupport",
    }:
        return NumericToken("amount", parsed, label)
    return NumericToken("number", parsed, label)


def _source_method(fact: dict) -> str:
    return {
        "XBRLFact": "xbrl_fact",
        "FinancialMetricValue": "financial_metric_value",
        "DerivedMetricValue": "derived_metric_value",
        "CalculatedNumericSupport": "calculated_numeric_support",
    }.get(str(fact.get("type") or ""), "code_calculated")


def _evidence_role(token: NumericToken) -> str:
    if token.kind == "amount":
        lowered = token.label.lower()
        if "share" in lowered:
            return "share_count"
        return "financial_amount"
    if token.kind == "percent":
        return "percentage"
    return "unknown"


def _unit_for_token(token: NumericToken) -> str:
    if token.kind == "amount":
        return "USD"
    if token.kind == "percent":
        return "percent"
    return "number"
