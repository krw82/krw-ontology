import json
import pytest
from pathlib import Path

from krw_ontology.eval_gold.schema import (
    EVIDENCE_GOLD_FORMAT_VERSION,
    EvidenceGold,
    EvidenceGoldCase,
    ExpectedEvidence,
    load_evidence_gold,
)

def _plan() -> dict:
    return {
        "question": "What was NVDA revenue for FY2025?",
        "intent": "metric_lookup",
        "clauses": [
            {
                "clause_id": "revenue",
                "retrieval_query": "NVIDIA total revenue fiscal 2025",
                "required_concepts": ["revenue"],
                "tickers": ["NVDA"],
            }
        ],
        "tickers": ["NVDA"],
    }

def _case(**over) -> dict:
    base = {
        "id": "case-001",
        "question": "What was NVDA revenue for FY2025?",
        "search_plan": _plan(),
        "strata": ["template"],
        "expected": [
            {"ticker": "NVDA", "period": "FY2025", "object_ids": ["obj-1"]}
        ],
    }
    base.update(over)
    return base

def test_valid_gold_roundtrip(tmp_path: Path):
    gold = EvidenceGold(
        source_release={"release_id": "r-test", "source_manifest_hash": "deadbeef"},
        cases=[EvidenceGoldCase(**_case())],
    )
    assert gold.format_version == EVIDENCE_GOLD_FORMAT_VERSION
    p = tmp_path / "gold.json"
    p.write_text(gold.model_dump_json(indent=2))
    loaded = load_evidence_gold(p)
    assert loaded.cases[0].expected[0].object_ids == ["obj-1"]

def test_expected_item_requires_anchor():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ExpectedEvidence(ticker="NVDA")  # object_ids/text_fragments 모두 비면 거부

def test_duplicate_case_ids_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EvidenceGold(
            source_release={"release_id": "r"},
            cases=[EvidenceGoldCase(**_case()), EvidenceGoldCase(**_case(id="case-001"))],
        )

def test_not_disclosed_requires_forbidden_fragments():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EvidenceGoldCase(**_case(expected=[], expect_not_disclosed=True))

def test_invalid_search_plan_rejected_with_case_id():
    from pydantic import ValidationError
    bad = _plan()
    bad["clauses"] = []  # 최소 1개 필요 (contracts._validate_plan)
    with pytest.raises(ValidationError, match="case-002"):
        EvidenceGoldCase(**_case(id="case-002", search_plan=bad))

def test_load_evidence_gold_rejects_wrong_format(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"format_version": "krw-ontology-evidence-gold/v0"}))
    with pytest.raises(ValueError, match="format_version"):
        load_evidence_gold(p)
