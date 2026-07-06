from __future__ import annotations

import asyncio
import json
from pathlib import Path

from krw_ontology.errors import RateLimitError
from krw_ontology.guru.extractor import (
    _build_batches,
    _normalize_candidate_payload,
    build_extraction_prompt,
    extract_guru_ontology,
)
from krw_ontology.guru.models import (
    GuruParsedDocument,
    GuruParsedManifest,
    GuruPrivateSpanRecord,
    utc_now_iso,
)


def test_extract_guru_ontology_dry_run_does_not_call_agent_sdk(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path)

    manifest = extract_guru_ontology(root, running_root=running_root, max_batches=1)

    assert manifest.execution_mode == "dry_run"
    assert manifest.agent_sdk_called is False
    assert manifest.extraction_started is False
    assert len(manifest.batches) == 1
    prompt_path = Path(manifest.batches[0].prompt_path)
    assert prompt_path.exists()
    assert "Margin of safety matters." in prompt_path.read_text(encoding="utf-8")
    assert not (running_root / "generated" / "ontology_candidates.jsonl").exists()


def test_extract_guru_ontology_can_execute_with_injected_worker(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path)

    class FakeWorker:
        async def extract(self, *args, **kwargs):
            return [
                {
                    "candidate_id": "marks:concept:margin-of-safety",
                    "author_key": "marks",
                    "object_type": "concept",
                    "label_ko": "안전마진",
                    "summary_ko": "불확실성을 가격에 반영해야 한다.",
                    "supporting_span_ids": ["marks:2024-memo:span:0000"],
                    "confidence": "high",
                }
            ]

    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        worker=FakeWorker(),
    )

    assert manifest.execution_mode == "agent_sdk"
    assert manifest.agent_sdk_called is True
    assert manifest.extraction_started is True
    assert manifest.batches[0].agent_sdk_called is True
    candidates_path = running_root / "generated" / "ontology_candidates.jsonl"
    candidate = json.loads(candidates_path.read_text(encoding="utf-8").splitlines()[0])
    assert candidate["candidate_id"] == "marks:concept:margin-of-safety"


def test_extract_guru_ontology_skips_invalid_candidate_without_failing_batch(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path)

    class FakeWorker:
        async def extract(self, *args, **kwargs):
            return [
                {
                    "candidate_id": "marks:concept:valid",
                    "author_key": "marks",
                    "object_type": "concept",
                    "label_ko": "유효 후보",
                    "summary_ko": "유효한 후보는 보존한다.",
                    "supporting_span_ids": ["marks:2024-memo:span:0000"],
                    "confidence": "high",
                },
                {
                    "candidate_id": "marks:concept:invalid-author",
                    "author_key": "unknown",
                    "object_type": "concept",
                    "label_ko": "무효 후보",
                    "summary_ko": "이 후보만 건너뛴다.",
                    "confidence": "high",
                },
            ]

    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        worker=FakeWorker(),
    )

    assert manifest.batches[0].status == "completed"
    candidates_path = running_root / "generated" / "ontology_candidates.jsonl"
    rows = [
        json.loads(line)
        for line in candidates_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["candidate_id"] for row in rows] == ["marks:concept:valid"]


def test_extract_guru_ontology_executes_batches_concurrently(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path, span_count=3)

    class FakeWorker:
        def __init__(self):
            self.active_calls = 0
            self.max_active_calls = 0

        async def extract(self, *args, **kwargs):
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
            await asyncio.sleep(0.01)
            self.active_calls -= 1
            batch_id = kwargs["call_metadata"]["batch_id"]
            return [
                {
                    "candidate_id": f"marks:concept:{batch_id}",
                    "author_key": "marks",
                    "object_type": "concept",
                    "label_ko": f"개념 {batch_id}",
                    "summary_ko": "병렬 추출 후보.",
                    "supporting_span_ids": [f"marks:2024-memo:span:{int(batch_id[-4:]) - 1:04d}"],
                    "confidence": "medium",
                }
            ]

    worker = FakeWorker()
    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        batch_spans=1,
        concurrency=2,
        worker=worker,
    )

    assert len(manifest.batches) == 3
    assert [batch.status for batch in manifest.batches] == [
        "completed",
        "completed",
        "completed",
    ]
    assert manifest.concurrency == 2
    assert worker.max_active_calls == 2


def test_extract_guru_ontology_splits_failed_batch(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path, span_count=2)

    class FakeWorker:
        def __init__(self):
            self.calls: list[int] = []

        async def extract(self, _prompt_template, input_data, _output_schema, _stage_name, **_kwargs):
            spans = json.loads(input_data["span_payload_json"])
            self.calls.append(len(spans))
            if len(spans) > 1:
                raise TimeoutError("batch too large")
            span = spans[0]
            return [
                {
                    "candidate_id": f"marks:concept:{span['span_id'].split(':')[-1]}",
                    "author_key": "marks",
                    "object_type": "concept",
                    "label_ko": "분할 재시도 후보",
                    "summary_ko": "큰 배치를 쪼갠 뒤 보존된 후보.",
                    "supporting_span_ids": [span["span_id"]],
                    "confidence": "medium",
                }
            ]

    worker = FakeWorker()
    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        batch_spans=2,
        worker=worker,
    )

    assert worker.calls == [2, 1, 1]
    assert manifest.batches[0].status == "completed"
    assert manifest.batches[0].error is None
    rows = [
        json.loads(line)
        for line in (running_root / "generated" / "ontology_candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(rows) == 2


def test_extract_guru_ontology_does_not_split_rate_limited_batch(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path, span_count=2)

    class FakeWorker:
        def __init__(self):
            self.calls: list[int] = []

        async def extract(self, _prompt_template, input_data, _output_schema, _stage_name, **_kwargs):
            spans = json.loads(input_data["span_payload_json"])
            self.calls.append(len(spans))
            raise RateLimitError("HTTP 529 overloaded")

    worker = FakeWorker()
    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        batch_spans=2,
        worker=worker,
    )

    assert worker.calls == [2]
    assert manifest.batches[0].status == "error"
    assert "HTTP 529 overloaded" in str(manifest.batches[0].error)


def test_extract_guru_ontology_reuses_existing_batch_response(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path)
    response_path = running_root / "generated" / "responses" / "guru-batch-0001.jsonl"
    response_path.parent.mkdir(parents=True)
    response_path.write_text(
        json.dumps(
            {
                "candidate_id": "marks:concept:existing",
                "author_key": "marks",
                "object_type": "concept",
                "label_ko": "기존 후보",
                "summary_ko": "이미 완료된 SDK 응답.",
                "supporting_span_ids": ["marks:2024-memo:span:0000"],
                "confidence": "high",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    class FailingWorker:
        async def extract(self, *args, **kwargs):
            raise AssertionError("existing response should be reused")

    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        worker=FailingWorker(),
    )

    assert manifest.batches[0].status == "completed"
    candidates_path = running_root / "generated" / "ontology_candidates.jsonl"
    candidate = json.loads(candidates_path.read_text(encoding="utf-8").splitlines()[0])
    assert candidate["candidate_id"] == "marks:concept:existing"


def test_extract_guru_ontology_normalizes_existing_batch_response(tmp_path: Path):
    root, running_root = _write_parsed_workspace(tmp_path)
    response_path = running_root / "generated" / "responses" / "guru-batch-0001.jsonl"
    response_path.parent.mkdir(parents=True)
    response_path.write_text(
        json.dumps(
            {
                "candidate_id": "guru:marks:data_need:guru-batch-0001:no-span:valuation-data",
                "author_key": "marks",
                "object_type": "data_need",
                "object_origin": "data_need",
                "label_ko": "밸류에이션 데이터 필요",
                "summary_ko": "안전마진을 판단하려면 가격과 가치 추정 데이터가 필요하다.",
                "data_need_family": "future_company_metric",
                "data_need_key": None,
                "company_data_hooks": ["valuation_multiples"],
                "confidence": "high",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    class FailingWorker:
        async def extract(self, *args, **kwargs):
            raise AssertionError("existing response should be reused")

    extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=True,
        model="claude-test",
        worker=FailingWorker(),
    )

    candidates_path = running_root / "generated" / "ontology_candidates.jsonl"
    candidate = json.loads(candidates_path.read_text(encoding="utf-8").splitlines()[0])
    assert candidate["data_need_key"] == "valuation_multiples"
    assert candidate["answer_role"]["default"] == "data_need"


def test_extraction_prompt_requests_consultation_objects(tmp_path: Path):
    _root, running_root = _write_parsed_workspace(tmp_path)
    span_path = running_root / "spans" / "marks" / "memo.jsonl"
    span = GuruPrivateSpanRecord.model_validate_json(span_path.read_text(encoding="utf-8"))

    prompt = build_extraction_prompt([span])

    assert "investor-guru consultation ontology" in prompt
    assert "already bought X" in prompt
    assert "object_origin must be exactly one of" in prompt
    assert "Never use generic sequential ids" in prompt
    assert "official_index" in prompt
    assert "answer_playbook" in prompt
    assert "data_need" in prompt
    assert "requires_company_data" in prompt
    assert "Do not assume, query, or bind" in prompt
    assert "Soft metadata is for retrieval ranking and answer assembly only" in prompt
    assert "hard guru framework" in prompt
    assert "Do not fill these fields from generic knowledge about the author" in prompt


def test_normalize_candidate_payload_accepts_agent_sdk_shape_drift():
    payload = _normalize_candidate_payload(
        {
            "candidate_id": "guru:marks:risk_frame:guru-batch-0001:span:risk",
            "author_key": "marks",
            "object_type": "risk_frame",
            "object_origin": "source_grounded",
            "label_ko": "리스크 점검",
            "summary_ko": "리스크를 먼저 본다.",
            "supporting_span_ids": "marks:2024-memo:span:0000",
            "intent_family": "risk_assessment",
            "decision_stage": "hold",
            "answer_section_type": "risk",
            "company_data_hooks": ["risk_factors"],
            "generalization_confidence": "high",
            "applicability": {
                "strong_for": "risk_check",
                "possible_for": ["holding_review"],
                "confidence": "strong",
            },
            "specificity": {
                "level": "unexpected",
                "source_case_tags": "private credit",
                "generalization_confidence": "very_high",
            },
            "answer_role": {
                "default": "warning",
                "possible_roles": "questions",
                "confidence": "strong",
            },
        }
    )

    assert payload["supporting_span_ids"] == ["marks:2024-memo:span:0000"]
    assert payload["intent_family"] == "risk_check"
    assert payload["decision_stage"] == "holding"
    assert payload["answer_section_type"] == "risk_checks"
    assert payload["company_data_hooks"] == [
        {"key": "risk_factors", "reason": "agent_sdk_string_hook"}
    ]
    assert "generalization_confidence" not in payload
    assert payload["applicability"]["strong_for"] == ["risk_check"]
    assert payload["specificity"]["level"] == "general_principle"
    assert payload["specificity"]["source_case_tags"] == ["private credit"]
    assert payload["answer_role"]["default"] == "caution"
    assert payload["answer_role"]["possible_roles"] == ["checklist"]


def test_normalize_candidate_payload_repairs_data_need_family_and_key():
    payload = _normalize_candidate_payload(
        {
            "candidate_id": "guru:buffett:data_need:guru-batch-0001:no-span:cash-flow",
            "author_key": "buffett",
            "object_type": "data_need",
            "object_origin": "consultation_derived",
            "label_ko": "현금흐름 품질 데이터",
            "summary_ko": "회사 공시에서 현금흐름, 자본배분, 위험요인 근거가 필요하다.",
            "requires_company_data": True,
            "company_data_hooks": ["cash_flow", "capital_allocation"],
            "answer_role": {"default": "supporting_lens"},
        }
    )

    assert payload["object_type"] == "data_need"
    assert payload["object_origin"] == "data_need"
    assert payload["data_need_family"] == "future_company_metric"
    assert payload["data_need_key"] == "cash_flow"
    assert payload["answer_role"]["default"] == "data_need"
    assert "checklist" in payload["answer_role"]["possible_roles"]


def test_normalize_candidate_payload_maps_object_type_aliases():
    payload = _normalize_candidate_payload(
        {
            "candidate_id": "guru:ackman:thesis:guru-batch-0001:span:thesis",
            "author_key": "ackman",
            "object_type": "Thesis",
            "object_origin": "source_grounded",
            "label_ko": "투자 논지 점검",
            "summary_ko": "투자 논지를 점검한다.",
        }
    )

    assert payload["object_type"] == "decision_criterion"


def test_build_batches_excludes_official_index_spans():
    """official_index spans are link listings and must not consume batch budget."""
    substantive = GuruPrivateSpanRecord(
        span_id="marks:2024-memo:span:0000",
        source_id="marks:2024-memo",
        span_type="paragraph",
        position=0,
        text_hash="hash-0",
        author_key="marks",
        title="2024 Memo",
        official_url="https://example.com/memo",
        text="Margin of safety matters.",
        char_count=26,
    )
    index_span = GuruPrivateSpanRecord(
        span_id="marks:official_index:span:0000",
        source_id="marks:official_index",
        span_type="paragraph",
        position=0,
        text_hash="hash-idx",
        author_key="marks",
        title="Howard Marks official source index",
        official_url="https://example.com/index",
        text="Archive of memos. Links to PDFs.",
        char_count=31,
    )

    batches = _build_batches(
        [substantive, index_span],
        batch_spans=8,
        batch_chars=14_000,
    )

    assert len(batches) == 1
    span_ids = [span.span_id for span in batches[0]]
    assert "marks:2024-memo:span:0000" in span_ids
    assert "marks:official_index:span:0000" not in span_ids


def _write_parsed_workspace(tmp_path: Path, span_count: int = 1) -> tuple[Path, Path]:
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    spans_path = running_root / "spans" / "marks" / "memo.jsonl"
    spans_path.parent.mkdir(parents=True)
    spans = (
        GuruPrivateSpanRecord(
            span_id=f"marks:2024-memo:span:{index:04d}",
            source_id="marks:2024-memo",
            span_type="paragraph",
            position=index,
            text_hash=f"hash-{index}",
            author_key="marks",
            title="2024 Memo",
            official_url="https://example.com/memo",
            text=f"Margin of safety matters. Span {index}.",
            char_count=32,
        )
        for index in range(span_count)
    )
    spans_path.write_text(
        "".join(
            json.dumps(span.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for span in spans
        ),
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
                span_count=span_count,
                status="parsed",
            )
        ],
    )
    running_root.mkdir(parents=True, exist_ok=True)
    (running_root / "parsed_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")),
        encoding="utf-8",
    )
    return root, running_root
