from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from krw_ontology.cli.main import app
from krw_ontology.guru.eval_quality import (
    evaluate_guru_answer_contract,
    run_guru_answer_eval,
    run_guru_answer_eval_batch,
    run_guru_quality_eval,
)


runner = CliRunner()


def test_run_guru_quality_eval_scores_research_pack_and_writes_report(tmp_path: Path):
    root = _write_quality_eval_fixture(tmp_path)
    eval_path = tmp_path / "gold.jsonl"
    eval_path.write_text(
        json.dumps(
            {
                "id": "guru_gold_test_001",
                "question": "버핏식으로 좋은 사업과 좋은 투자를 구분해줘. 특정 종목은 없어.",
                "author_keys": ["buffett"],
                "expected_statuses": ["sufficient_lens", "partial_lens"],
                "expected_intent": "business_quality_check",
                "requires_company_evidence": False,
                "min_lenses": 1,
                "must_include_any": [["사업", "경제성"], ["가격", "투자"]],
                "expected_answer_roles": ["core_lens"],
                "expect_soft_metadata": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_guru_quality_eval(root, eval_path=eval_path)

    assert report["format"] == "krw-guru-quality-report/v1"
    assert report["cases"] == 1
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["case_results"][0]["selected_lens_labels"] == ["좋은 사업과 좋은 투자의 구분"]
    assert Path(report["output_path"]).exists()
    written_report = json.loads(Path(report["output_path"]).read_text(encoding="utf-8"))
    assert written_report["output_path"] == report["output_path"]


def test_run_guru_quality_eval_checks_company_bridge_text(monkeypatch, tmp_path: Path):
    root = tmp_path / "guru"
    root.mkdir()
    eval_path = tmp_path / "gold.jsonl"
    eval_path.write_text(
        json.dumps(
            {
                "id": "guru_gold_bridge_001",
                "question": "TSLA 리스크를 막스 관점으로 봐줘.",
                "author_keys": ["marks"],
                "expected_statuses": ["needs_company_evidence"],
                "expected_intent": "risk_check",
                "requires_company_evidence": True,
                "min_lenses": 1,
                "must_include_any": [["risk_factors", "balance_sheet"]],
                "expected_answer_roles": ["core_lens"],
                "expect_soft_metadata": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_query_context_tool(**kwargs):
        return json.dumps(
            {
                "research_status": "needs_company_evidence",
                "intent": {"family": "risk_check"},
                "requires_company_evidence": True,
                "filing_evidence_requirements": ["risk_factors", "balance_sheet"],
                "research_pack": {
                    "selected_lenses": [
                        {
                            "author_key": "marks",
                            "label_ko": "리스크 렌즈",
                            "answer_role": {"default": "core_lens"},
                            "applicability": {"strong_for": ["risk_check"]},
                        }
                    ],
                    "consultation_moves": [],
                    "data_needs": [],
                    "company_bridge": {
                        "filing_evidence_requirements": ["risk_factors", "balance_sheet"]
                    },
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "krw_ontology.guru.eval_quality.guru_query_context_tool",
        fake_query_context_tool,
    )

    report = run_guru_quality_eval(root, eval_path=eval_path)

    assert report["passed"] == 1
    checks = report["case_results"][0]["checks"]
    assert next(check for check in checks if check["name"] == "must_include_any")["passed"]


def test_evaluate_guru_answer_contract_allows_autonomous_bounded_answer():
    payload = {
        "research_status": "needs_company_evidence",
        "answerability": {
            "direct_source_match": False,
            "source_match_strength": "related",
            "recommended_answer_mode": "lens_with_company_bridge",
        },
        "intent": {"requires_company_evidence": True},
        "research_pack": {
            "clarifying_questions": [],
            "company_bridge": {"requires_company_evidence": True},
        },
    }
    answer = (
        "이 렌즈로 보면 아직 공시 근거 없이 결론을 내리기는 어렵습니다. "
        "먼저 현금흐름, 자본배분, 리스크 요인, 밸류에이션 근거를 확인해야 합니다. "
        "그 다음 좋은 사업인지와 좋은 투자 가격인지를 분리해서 점검하는 방식이 맞습니다."
    )

    result = evaluate_guru_answer_contract(
        question="AAPL을 버핏 관점에서 장기 보유해도 되는지 봐줘.",
        answer=answer,
        research_payload=payload,
    )

    assert result["passed"] is True
    assert not result["failures"]
    assert result["context"]["requires_company_evidence"] is True
    assert result["context"]["direct_source_match"] is False


def test_evaluate_guru_answer_contract_rejects_impersonation_and_orders():
    answer = (
        "버핏은 지금 AAPL을 사라고 말할 겁니다. 목표가 300입니다. "
        "reviewed_id guru:buffett:test 를 보면 매수하세요."
    )

    result = evaluate_guru_answer_contract(
        question="AAPL을 버핏 관점에서 봐줘.",
        answer=answer,
        research_payload={
            "research_status": "needs_company_evidence",
            "intent": {"requires_company_evidence": True},
        },
    )

    assert result["passed"] is False
    failed = {check["name"] for check in result["failures"]}
    assert "no_real_guru_claim" in failed
    assert "no_personalized_investment_order" in failed
    assert "no_raw_internals" in failed


def test_run_guru_answer_eval_writes_report_with_research_payload(tmp_path: Path):
    payload_path = tmp_path / "research_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "research_status": "needs_identifier_clarification",
                "requires_identifier_clarification": True,
                "requires_company_evidence": True,
                "clarifying_questions": ["공시 조회를 위해 정확한 티커와 거래소를 확인해 주세요."],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer = (
        "먼저 확인이 필요합니다. 삼성전자 보통주인지 우선주인지, 그리고 조회할 티커와 "
        "거래소를 알려주세요. 그 전에는 공시 근거 없이 강한 판단을 내리기 어렵습니다."
    )

    report = run_guru_answer_eval(
        tmp_path / "guru",
        question="삼성전자를 버핏 관점에서 봐줘.",
        answer=answer,
        research_payload_path=payload_path,
    )

    assert report["format"] == "krw-guru-answer-eval-report/v1"
    assert report["passed"] is True
    assert report["context"]["requires_identifier_clarification"] is True
    assert Path(report["output_path"]).exists()


def test_guru_eval_answer_cli_outputs_json(tmp_path: Path):
    answer_path = tmp_path / "answer.md"
    answer_path.write_text(
        "이 렌즈로 보면 공시 근거 확인이 먼저 필요합니다. "
        "현금흐름, 리스크 요인, 자본배분을 확인한 뒤 투자 가격과 사업 품질을 분리해 점검해야 합니다.",
        encoding="utf-8",
    )
    payload_path = tmp_path / "research_payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "research_status": "needs_company_evidence",
                "answerability": {
                    "direct_source_match": False,
                    "source_match_strength": "related",
                    "recommended_answer_mode": "lens_with_company_bridge",
                },
                "requires_company_evidence": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "guru",
            "eval-answer",
            "--root",
            str(tmp_path / "guru"),
            "--question",
            "AAPL을 버핏 관점에서 봐줘.",
            "--answer-file",
            str(answer_path),
            "--research-payload",
            str(payload_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["format"] == "krw-guru-answer-eval-report/v1"
    assert report["passed"] is True
    assert report["score_ratio"] >= 0.82


def test_run_guru_answer_eval_batch_scores_answer_artifacts(tmp_path: Path):
    good_answer = tmp_path / "good_answer.md"
    good_answer.write_text(
        "이 렌즈로 보면 공시 근거 확인이 먼저 필요합니다. "
        "현금흐름, 리스크 요인, 자본배분을 확인한 뒤 사업 품질과 투자 가격을 분리해 점검해야 합니다.",
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "research_status": "needs_company_evidence",
                "answerability": {
                    "direct_source_match": False,
                    "source_match_strength": "related",
                    "recommended_answer_mode": "lens_with_company_bridge",
                },
                "requires_company_evidence": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases_path = tmp_path / "answer_cases.jsonl"
    cases = [
        {
            "id": "ok",
            "question": "AAPL을 버핏 관점에서 봐줘.",
            "answer_path": good_answer.name,
            "research_payload_path": payload.name,
        },
        {
            "id": "bad",
            "question": "AAPL을 버핏 관점에서 봐줘.",
            "answer": "버핏은 지금 AAPL을 사라고 말할 겁니다. 목표가 300입니다.",
            "research_payload": {
                "research_status": "needs_company_evidence",
                "requires_company_evidence": True,
            },
        },
    ]
    cases_path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )

    report = run_guru_answer_eval_batch(tmp_path / "guru", cases_path=cases_path)

    assert report["format"] == "krw-guru-answer-eval-batch-report/v1"
    assert report["cases"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["failures"][0]["id"] == "bad"
    assert Path(report["output_path"]).exists()


def test_guru_eval_answer_batch_cli_outputs_json(tmp_path: Path):
    cases_path = tmp_path / "answer_cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "id": "ok",
                "question": "AAPL을 버핏 관점에서 봐줘.",
                "answer": (
                    "이 렌즈로 보면 공시 근거 확인이 먼저 필요합니다. "
                    "현금흐름, 리스크 요인, 자본배분을 확인한 뒤 점검해야 합니다."
                ),
                "research_payload": {
                    "research_status": "needs_company_evidence",
                    "answerability": {
                        "direct_source_match": False,
                        "source_match_strength": "related",
                        "recommended_answer_mode": "lens_with_company_bridge",
                    },
                    "requires_company_evidence": True,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "guru",
            "eval-answer-batch",
            "--root",
            str(tmp_path / "guru"),
            "--cases-path",
            str(cases_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["format"] == "krw-guru-answer-eval-batch-report/v1"
    assert report["cases"] == 1
    assert report["passed"] == 1


def _write_quality_eval_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "guru"
    reviewed = root / "reviewed"
    reviewed.mkdir(parents=True)
    _write_jsonl(
        reviewed / "guru_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:decision:business-vs-investment:test",
                "candidate_id": "candidate:1",
                "author_key": "buffett",
                "object_type": "decision_criterion",
                "object_origin": "source_grounded",
                "label_ko": "좋은 사업과 좋은 투자의 구분",
                "summary_ko": "좋은 사업은 지속 가능한 경제성과 현금 창출력이고, 좋은 투자는 가격과 안전마진까지 함께 맞아야 한다.",
                "supporting_span_ids": ["buffett:test:span:0001"],
                "intent_family": "business_quality_check",
                "applicability": {
                    "strong_for": ["business_quality_check", "valuation_check"],
                    "possible_for": ["learn_guru_view"],
                    "weak_for": [],
                    "anti_triggers": [],
                    "requires_clarification_when": [],
                    "confidence": "high",
                },
                "specificity": {
                    "level": "general_principle",
                    "source_case_tags": [],
                    "generalization_confidence": "high",
                },
                "answer_role": {
                    "default": "core_lens",
                    "possible_roles": ["checklist"],
                    "confidence": "high",
                },
                "confidence": "high",
                "status": "reviewed",
            }
        ],
    )
    _write_jsonl(
        reviewed / "consultation_objects.jsonl",
        [
            {
                "reviewed_id": "guru:buffett:question_template:business-quality:test",
                "candidate_id": "candidate:2",
                "author_key": "buffett",
                "object_type": "question_template",
                "object_origin": "consultation_derived",
                "label_ko": "좋은 사업 점검 질문",
                "summary_ko": "좋은 사업과 좋은 투자를 구분하는 질문 목록이다.",
                "question_pattern_ko": "좋은 사업인가, 좋은 투자 가격인가?",
                "intent_family": "business_quality_check",
                "requires_company_data": False,
                "requires_portfolio_data": False,
                "requires_user_context": False,
                "supporting_span_ids": [],
                "applicability": {
                    "strong_for": ["business_quality_check"],
                    "possible_for": ["learn_guru_view"],
                    "weak_for": [],
                    "anti_triggers": [],
                    "requires_clarification_when": [],
                    "confidence": "high",
                },
                "specificity": {
                    "level": "general_principle",
                    "source_case_tags": [],
                    "generalization_confidence": "high",
                },
                "answer_role": {
                    "default": "checklist",
                    "possible_roles": ["supporting_lens"],
                    "confidence": "high",
                },
                "confidence": "high",
                "status": "reviewed",
            }
        ],
    )
    _write_jsonl(reviewed / "data_needs.jsonl", [])
    _write_jsonl(reviewed / "corpus_metadata.jsonl", [])
    _write_jsonl(reviewed / "relationships.jsonl", [])
    _write_jsonl(reviewed / "rejected_candidates.jsonl", [])
    (reviewed / "curation_report.json").write_text(
        json.dumps(
            {
                "schema_version": "krw-guru-ontology/v1",
                "generated_at": "2026-07-05T00:00:00+00:00",
                "root": str(root),
                "running_root": str(tmp_path / "running"),
                "warnings": [],
                "rejection_reasons": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
