from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from krw_ontology.cli.main import app
from krw_ontology.guru.curation import curate_guru_candidates
from krw_ontology.guru.models import (
    GuruParsedDocument,
    GuruParsedManifest,
    GuruPrivateSpanRecord,
    utc_now_iso,
)


runner = CliRunner()


def test_curate_guru_candidates_writes_reviewed_artifacts(tmp_path: Path):
    root, running_root = _write_curation_workspace(tmp_path)
    _write_candidates(
        running_root,
        [
            _candidate(
                candidate_id="guru:marks:concept:guru-batch-0001:marks-span:margin-of-safety",
                object_type="concept",
                object_origin="source_grounded",
                label_ko="안전마진",
                summary_ko="불확실성을 가격에 반영해야 한다.",
                supporting_span_ids=["marks:2024-memo:span:0000"],
                label_en="margin of safety",
                applicability={
                    "strong_for": ["valuation_check", "average_down"],
                    "possible_for": ["risk_check"],
                    "weak_for": ["business_quality_check"],
                    "anti_triggers": ["short_term_price_prediction"],
                    "requires_clarification_when": ["no company or price context"],
                    "confidence": "high",
                },
                specificity={
                    "level": "general_principle",
                    "source_case_ko": None,
                    "source_case_tags": [],
                    "generalization_confidence": "high",
                },
                answer_role={
                    "default": "core_lens",
                    "possible_roles": ["checklist", "caution"],
                    "confidence": "high",
                },
            ),
            _candidate(
                candidate_id="guru:marks:data_need:guru-batch-0001:marks-span:valuation-data",
                object_type="data_need",
                object_origin="data_need",
                label_ko="밸류에이션 데이터 필요",
                summary_ko="안전마진을 판단하려면 가격과 가치 추정 데이터가 필요하다.",
                supporting_span_ids=["marks:2024-memo:span:0000"],
                data_need_family="future_company_metric",
                data_need_key="valuation_multiples",
            ),
            _candidate(
                candidate_id="guru:marks:question_template:guru-batch-0001:no-span:margin-check",
                object_type="question_template",
                object_origin="consultation_derived",
                label_ko="안전마진 점검 질문",
                summary_ko="보유 종목의 가격이 불확실성을 충분히 반영하는지 묻는다.",
                related_candidate_ids=[
                    "guru:marks:concept:guru-batch-0001:marks-span:margin-of-safety",
                    "guru:marks:data_need:guru-batch-0001:marks-span:valuation-data",
                ],
                question_pattern_ko="{ticker}는 안전마진이 충분한가요?",
                intent_family="valuation_check",
                decision_stage="holding",
            ),
            _candidate(
                candidate_id="guru:marks:corpus_metadata:guru-batch-0001:marks-index:oaktree",
                object_type="corpus_metadata",
                object_origin="corpus_metadata",
                label_ko="오크트리 메모 아카이브",
                summary_ko="하워드 막스 메모의 공식 인덱스다.",
                supporting_span_ids=["marks:official_index:span:0000"],
            ),
        ],
    )

    report = curate_guru_candidates(root, running_root=running_root)

    reviewed = root / "reviewed"
    assert report.input_candidates == 4
    assert report.accepted_guru_objects == 1
    assert report.accepted_consultation_objects == 1
    assert report.accepted_data_needs == 1
    assert report.accepted_corpus_metadata == 1
    assert report.relationships == 2
    assert report.rejected_candidates == 0
    assert (reviewed / "curation_report.json").exists()
    guru_object = json.loads((reviewed / "guru_objects.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert guru_object["reviewed_id"].startswith("guru:marks:concept:")
    assert "guru-batch" not in guru_object["reviewed_id"]
    assert guru_object["applicability"]["strong_for"] == ["valuation_check", "average_down"]
    assert guru_object["specificity"]["level"] == "general_principle"
    assert guru_object["answer_role"]["default"] == "core_lens"


def test_curate_guru_candidates_rejects_unsafe_candidates(tmp_path: Path):
    root, running_root = _write_curation_workspace(tmp_path)
    _write_candidates(
        running_root,
        [
            _candidate(
                candidate_id="bad:no-support",
                object_type="concept",
                object_origin="source_grounded",
                label_ko="근거 없는 원칙",
                summary_ko="근거가 없다.",
            ),
            _candidate(
                candidate_id="bad:missing-span",
                object_type="principle",
                object_origin="source_grounded",
                label_ko="없는 span",
                summary_ko="존재하지 않는 span을 참조한다.",
                supporting_span_ids=["marks:2024-memo:span:9999"],
            ),
            _candidate(
                candidate_id="bad:index-lens",
                object_type="concept",
                object_origin="source_grounded",
                label_ko="인덱스에서 뽑은 원칙",
                summary_ko="공식 인덱스는 원칙 근거가 아니다.",
                supporting_span_ids=["marks:official_index:span:0000"],
            ),
            _candidate(
                candidate_id="bad:awkward-korean",
                object_type="question_template",
                object_origin="consultation_derived",
                label_ko="자존적 부상 질문",
                summary_ko="자존적 부상이라는 어색한 표현이 있다.",
                related_candidate_ids=["bad:no-support"],
            ),
        ],
    )

    report = curate_guru_candidates(root, running_root=running_root)

    assert report.rejected_candidates == 4
    assert report.accepted_guru_objects == 0
    rejected = [
        json.loads(line)
        for line in (root / "reviewed" / "rejected_candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    reasons = [row["rejection_reason"] for row in rejected]
    assert any("grounded object has no supporting spans" in reason for reason in reasons)
    assert any("missing supporting span ids" in reason for reason in reasons)
    assert any("official index span cannot support lens object" in reason for reason in reasons)
    assert any("awkward Korean" in reason for reason in reasons)


def test_curate_guru_candidates_repairs_incomplete_data_need_metadata(tmp_path: Path):
    root, running_root = _write_curation_workspace(tmp_path)
    _write_candidates(
        running_root,
        [
            _candidate(
                candidate_id="guru:marks:data_need:guru-batch-0001:no-span:valuation-data",
                object_type="data_need",
                object_origin="data_need",
                label_ko="밸류에이션 데이터 필요",
                summary_ko="안전마진을 판단하려면 가격과 가치 추정 데이터가 필요하다.",
                data_need_family="future_company_metric",
                data_need_key=None,
                company_data_hooks=["valuation_multiples"],
            ),
        ],
    )

    report = curate_guru_candidates(root, running_root=running_root)

    assert report.accepted_data_needs == 1
    assert report.rejected_candidates == 0
    row = json.loads((root / "reviewed" / "data_needs.jsonl").read_text(encoding="utf-8"))
    assert row["data_need_family"] == "future_company_metric"
    assert row["data_need_key"] == "valuation_multiples"


def test_guru_curate_cli_outputs_json_report(tmp_path: Path):
    root, running_root = _write_curation_workspace(tmp_path)
    _write_candidates(
        running_root,
        [
            _candidate(
                candidate_id="guru:marks:concept:guru-batch-0001:marks-span:margin-of-safety",
                object_type="concept",
                object_origin="source_grounded",
                label_ko="안전마진",
                summary_ko="불확실성을 가격에 반영해야 한다.",
                supporting_span_ids=["marks:2024-memo:span:0000"],
                label_en="margin of safety",
            )
        ],
    )

    result = runner.invoke(
        app,
        [
            "guru",
            "curate",
            "--root",
            str(root),
            "--running-root",
            str(running_root),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["accepted_guru_objects"] == 1
    assert Path(payload["files"]["guru_objects"]).exists()


def _write_curation_workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    spans_path = running_root / "spans" / "marks" / "memo.jsonl"
    spans_path.parent.mkdir(parents=True)
    spans = [
        GuruPrivateSpanRecord(
            span_id="marks:official_index:span:0000",
            source_id="marks:official_index",
            span_type="paragraph",
            position=0,
            text_hash="index-hash",
            author_key="marks",
            title="Oaktree Insights",
            official_url="https://example.com/insights",
            text="Official archive index.",
            char_count=23,
        ),
        GuruPrivateSpanRecord(
            span_id="marks:2024-memo:span:0000",
            source_id="marks:2024-memo",
            span_type="paragraph",
            position=1,
            text_hash="memo-hash",
            author_key="marks",
            title="2024 Memo",
            official_url="https://example.com/memo",
            text="Margin of safety matters.",
            char_count=25,
        ),
    ]
    spans_path.write_text(
        "".join(json.dumps(span.model_dump(mode="json"), ensure_ascii=False) + "\n" for span in spans),
        encoding="utf-8",
    )
    manifest = GuruParsedManifest(
        generated_at=utc_now_iso(),
        root=str(root),
        running_root=str(running_root),
        parsed_documents=[
            GuruParsedDocument(
                source_id="marks:2024-memo",
                author_key="marks",
                title="2024 Memo",
                source_type="memo",
                official_url="https://example.com/memo",
                spans_path=str(spans_path),
                span_count=2,
                status="parsed",
            )
        ],
    )
    running_root.mkdir(parents=True, exist_ok=True)
    (running_root / "generated").mkdir(parents=True, exist_ok=True)
    (running_root / "parsed_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")),
        encoding="utf-8",
    )
    return root, running_root


def _write_candidates(running_root: Path, rows: list[dict]) -> None:
    path = running_root / "generated" / "ontology_candidates.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _candidate(**overrides) -> dict:
    row = {
        "candidate_id": "guru:marks:concept:guru-batch-0001:marks-span:default",
        "author_key": "marks",
        "object_type": "concept",
        "object_origin": "source_grounded",
        "label_ko": "안전마진",
        "summary_ko": "불확실성을 가격에 반영해야 한다.",
        "supporting_span_ids": [],
        "related_candidate_ids": [],
        "confidence": "high",
    }
    row.update(overrides)
    return row
