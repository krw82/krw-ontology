from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from krw_ontology.mcp_server.contracts import validate_search_plan

EVIDENCE_GOLD_FORMAT_VERSION = "krw-ontology-evidence-gold/v1"

ALLOWED_STRATA = {
    "template",
    "vocabulary_mismatch",
    "fiscal_offset",
    "multi_period",
    "dimensioned",
    "multi_span",
    "not_disclosed",
    "curated",
}


class ExpectedEvidence(BaseModel):
    ticker: str
    period: str | None = None
    document_type: str | None = None
    object_ids: list[str] = Field(default_factory=list)
    text_fragments: list[str] = Field(default_factory=list)
    required: bool = True

    @model_validator(mode="after")
    def _require_anchor(self) -> "ExpectedEvidence":
        if not self.object_ids and not self.text_fragments:
            raise ValueError(
                "expected item needs an anchor: object_ids or text_fragments"
            )
        return self


class EvidenceGoldCase(BaseModel):
    id: str
    question: str
    search_plan: dict[str, Any]
    strata: list[str] = Field(default_factory=list)
    expected: list[ExpectedEvidence] = Field(default_factory=list)
    expect_not_disclosed: bool = False
    forbidden_fragments: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _validate_case(self) -> "EvidenceGoldCase":
        if self.expect_not_disclosed and not self.forbidden_fragments:
            raise ValueError("expect_not_disclosed requires forbidden_fragments")
        if not self.expect_not_disclosed and not self.expected:
            raise ValueError("positive case requires at least one expected item")
        try:
            validate_search_plan(self.search_plan)
        except Exception as exc:  # pydanticValidationError 등 — 케이스 id 부착
            raise ValueError(f"case {self.id}: invalid search_plan: {exc}") from exc
        unknown = set(self.strata) - ALLOWED_STRATA
        if unknown:
            raise ValueError(f"case {self.id}: unknown strata {sorted(unknown)}")
        return self


class EvidenceGold(BaseModel):
    format_version: Literal[EVIDENCE_GOLD_FORMAT_VERSION] = EVIDENCE_GOLD_FORMAT_VERSION
    source_release: dict[str, Any]
    cases: list[EvidenceGoldCase]

    @model_validator(mode="after")
    def _validate_gold(self) -> "EvidenceGold":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate case ids: {dupes}")
        if not self.source_release.get("release_id"):
            raise ValueError("source_release.release_id is required")
        return self


def load_evidence_gold(path: Path) -> EvidenceGold:
    raw = json.loads(Path(path).read_text())
    if raw.get("format_version") != EVIDENCE_GOLD_FORMAT_VERSION:
        raise ValueError(
            f"format_version must be {EVIDENCE_GOLD_FORMAT_VERSION}, "
            f"got {raw.get('format_version')!r}"
        )
    return EvidenceGold.model_validate(raw)
