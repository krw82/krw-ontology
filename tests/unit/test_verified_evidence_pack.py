from __future__ import annotations

from typing import Any

import pytest

from krw_ontology.mcp_server.evidence_pack import (
    build_verified_company_evidence_pack,
    evidence_pack_hash,
    is_verified_company_evidence_pack,
)


class _FakeStore:
    def __init__(self, traces: dict[str, dict[str, Any] | None]) -> None:
        self.traces = traces

    def list_documents(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "ticker": "AAPL",
                "document_type": "10-K",
                "doc_type_key": "10K",
                "period": "CY2025",
            },
            {
                "ticker": "AAPL",
                "document_type": "10-Q",
                "doc_type_key": "10Q",
                "period": "CY2026Q1",
            },
        ]

    def trace(self, object_id: str) -> dict[str, Any] | None:
        return self.traces.get(object_id)


def test_verified_pack_uses_latest_quarter_and_metric_lineage() -> None:
    metric_id = "metric:AAPL:CY2026Q1:10Q:services-revenue"
    store = _FakeStore(
        {
            metric_id: {
                "object": {
                    "id": metric_id,
                    "type": "MetricObservation",
                    "ticker": "AAPL",
                    "document_type": "10-Q",
                    "period": "CY2026Q1",
                    "source_document_id": "source:AAPL:CY2026Q1:10Q",
                    "review_status": "accepted",
                },
                "document": {
                    "ticker": "AAPL",
                    "document_type": "10-Q",
                    "period": "CY2026Q1",
                },
                "evidence": {
                    "claims": [],
                    "quotes": [],
                    "spans": [],
                    "metric_lineage": {
                        "trace_type": "metric_lineage",
                        "formatted_value": "$26.3B",
                        "calculation": None,
                        "input_metrics": [],
                        "xbrl_facts": [
                            {
                                "id": "xbrl:AAPL:CY2026Q1:services-revenue",
                                "source_document_id": "source:AAPL:CY2026Q1:10Q",
                            }
                        ],
                        "source_document_ids": ["source:AAPL:CY2026Q1:10Q"],
                    },
                },
                "quality": {"object_status": "accepted"},
            }
        }
    )

    pack = build_verified_company_evidence_pack(
        store=store,  # type: ignore[arg-type]
        ticker="aapl",
        questions=[{"question_id": "q_services", "object_ids": [metric_id]}],
        release_id="release-1",
    )

    assert pack["current_driver"]["period"] == "CY2026Q1"
    assert pack["current_driver"]["document_type"] == "10-Q"
    assert pack["annual_baseline"]["period"] == "CY2025"
    item = pack["evidence_by_question"][0]["evidence"][0]
    assert item["trace_status"] == "traceable_metric_lineage"
    assert item["document"]["anchor_roles"] == ["current_driver"]
    assert item["metric_lineage"]["xbrl_fact_ids"] == [
        "xbrl:AAPL:CY2026Q1:services-revenue"
    ]
    assert item["verified_excerpt"] == "$26.3B"
    assert is_verified_company_evidence_pack(pack) is True


def test_verified_pack_hash_detects_modified_payload() -> None:
    span_id = "span:AAPL:CY2025:10K:1"
    store = _FakeStore(
        {
            span_id: {
                "object": {
                    "id": span_id,
                    "type": "SourceSpan",
                    "ticker": "AAPL",
                    "document_type": "10-K",
                    "period": "CY2025",
                    "source_document_id": "source:AAPL:CY2025:10K",
                    "text": "Services revenue increased during the year.",
                    "review_status": "accepted",
                },
                "document": {
                    "ticker": "AAPL",
                    "document_type": "10-K",
                    "period": "CY2025",
                },
                "evidence": {"claims": [], "quotes": [], "spans": []},
                "quality": {"object_status": "accepted"},
            }
        }
    )
    pack = build_verified_company_evidence_pack(
        store=store,  # type: ignore[arg-type]
        ticker="AAPL",
        questions=[{"question_id": "q_services", "object_ids": [span_id]}],
    )

    assert pack["pack_hash"] == evidence_pack_hash(pack)
    pack["evidence_by_question"][0]["evidence"][0]["verified_excerpt"] = (
        "Invented company fact."
    )
    assert is_verified_company_evidence_pack(pack) is False


def test_verified_pack_rejects_non_filing_objects() -> None:
    object_id = "profile:AAPL:ALL"
    store = _FakeStore(
        {
            object_id: {
                "object": {
                    "id": object_id,
                    "type": "CompanyBusinessProfile",
                    "ticker": "AAPL",
                    "document_type": "COMPANY",
                    "period": "ALL",
                    "review_status": "accepted",
                },
                "document": None,
                "evidence": {"claims": [], "quotes": [], "spans": []},
                "quality": {"object_status": "accepted"},
            }
        }
    )

    pack = build_verified_company_evidence_pack(
        store=store,  # type: ignore[arg-type]
        ticker="AAPL",
        questions=[{"question_id": "q_profile", "object_ids": [object_id]}],
    )

    assert pack["verification_summary"]["verified_object_count"] == 0
    assert pack["rejected_refs"][0]["reason"] == "unsupported_document_type"


def test_verified_pack_keeps_unanswered_question_ids() -> None:
    store = _FakeStore({})
    pack = build_verified_company_evidence_pack(
        store=store,  # type: ignore[arg-type]
        ticker="AAPL",
        questions=[{"question_id": "q_missing", "object_ids": []}],
    )

    assert pack["evidence_by_question"] == [
        {
            "question_id": "q_missing",
            "evidence": [],
            "verified_count": 0,
            "answerability": "not_verified",
        }
    ]
    assert pack["verification_summary"]["all_questions_have_verified_evidence"] is False


def test_verified_pack_rejects_duplicate_questions_and_oversized_batches() -> None:
    store = _FakeStore({})
    with pytest.raises(ValueError, match="duplicate question_id"):
        build_verified_company_evidence_pack(
            store=store,  # type: ignore[arg-type]
            ticker="AAPL",
            questions=[
                {"question_id": "q_same", "object_ids": ["one"]},
                {"question_id": "q_same", "object_ids": ["two"]},
            ],
        )

    with pytest.raises(ValueError, match="at most 4 object ids"):
        build_verified_company_evidence_pack(
            store=store,  # type: ignore[arg-type]
            ticker="AAPL",
            questions=[
                {
                    "question_id": "q_many",
                    "object_ids": ["one", "two", "three", "four", "five"],
                }
            ],
        )
