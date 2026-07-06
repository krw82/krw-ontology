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
        "자, 먼저 공시 근거 없이 결론을 내리기는 어렵습니다. "
        "먼저 현금흐름, 자본배분, 리스크 요인, 밸류에이션 근거를 확인해야 합니다. "
        "이 관점에서는 그 다음 좋은 사업인지와 좋은 투자 가격인지를 분리해서 점검하는 방식이 맞습니다."
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


def test_evaluate_guru_answer_contract_rejects_generic_template_when_lenses_exist():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["terry_smith"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "terry_smith",
                    "label_ko": "현금전환 품질",
                    "summary_ko": "현금전환과 이익의 질을 보며 불필요한 매매를 피하는 렌즈다.",
                }
            ],
            "persona_profile": {
                "reasoning_style": [{"label_ko": "현금전환 품질"}],
            },
        },
    }
    answer = (
        "이 질문은 기업 식별이 없는 프레임 점검 질문입니다. "
        "현재는 렌즈 기반의 질문으로만 접근하고, 조건이 명확하면 "
        "체크리스트를 바로 실행 가능한 형태로 바꿔 드리는 방식이 안정적입니다. "
        "우선 확인할 것은 가설의 반박 증거와 의사결정 체계입니다."
    )

    result = evaluate_guru_answer_contract(
        question="테리 스미스 관점에서 좋은 회사를 오래 보는 질문을 만들어줘.",
        answer=answer,
        research_payload=payload,
    )

    failed = {check["name"] for check in result["failures"]}
    assert result["passed"] is False
    assert "selected_lens_usage" in failed
    assert "guru_voice_distinctiveness" in failed
    assert "generic_template_escape" in failed


def test_evaluate_guru_answer_contract_accepts_lens_grounded_voice():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["marks"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "marks",
                    "label_ko": "가격에 반영된 기대와 하방 리스크",
                    "summary_ko": "가격에 이미 들어간 기대와 사이클이 틀렸을 때의 손실을 함께 본다.",
                }
            ],
            "persona_profile": {
                "reasoning_style": [{"label_ko": "가격에 반영된 기대"}],
                "caution_patterns": [{"label_ko": "하방 리스크"}],
            },
        },
    }
    answer = (
        "자, 먼저 좋은 시나리오가 가격에 얼마나 들어갔는지부터 봐야 합니다. "
        "핵심은 '가격에 반영된 기대와 하방 리스크'입니다. "
        "내가 먼저 물을 질문은 좋은 이야기를 너무 쉽게 받아들이고 있는지입니다. "
        "그 다음에는 사이클이 불리하게 움직일 때 손실을 견딜 여지가 있는지 점검해야 합니다. "
        "결론보다 반대 증거와 안전마진을 먼저 확인하는 쪽이 이 질문에 더 맞습니다."
    )

    result = evaluate_guru_answer_contract(
        question="하워드 막스식으로 내가 놓치기 쉬운 리스크 질문을 만들어줘.",
        answer=answer,
        research_payload=payload,
    )

    assert result["passed"] is True
    assert not result["failures"]


def test_evaluate_guru_answer_contract_rejects_immersion_breaking_meta_voice():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["buffett"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "buffett",
                    "label_ko": "사업 소유자 관점",
                    "summary_ko": "주식을 사업 일부의 소유로 보고 경제성과 가격을 분리한다.",
                }
            ],
        },
    }
    answer = (
        "이 렌즈로 보면 먼저 주식이 아니라 사업 소유자 관점으로 봐야 합니다. "
        "사업의 경제성, 경영진, 현금흐름을 확인하고 가격표를 마지막에 봐야 합니다. "
        "좋은 가게도 너무 비싸게 사면 좋은 투자가 아닙니다."
    )

    result = evaluate_guru_answer_contract(
        question="버핏처럼 좋은 사업과 좋은 투자의 차이를 말해줘.",
        answer=answer,
        research_payload=payload,
    )

    failed = {check["name"] for check in result["failures"]}
    assert result["passed"] is False
    assert "immersive_voice" in failed


def test_evaluate_guru_answer_contract_rejects_footer_disclaimer():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["buffett"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "buffett",
                    "label_ko": "사업 소유자 관점",
                    "summary_ko": "주식을 사업 일부의 소유로 보고 경제성과 가격을 분리한다.",
                }
            ],
            "persona_profile": {
                "reasoning_style": [{"label_ko": "사업 소유자 관점"}],
            },
        },
    }
    answer = (
        "자, 내가 먼저 묻고 싶은 건 하나입니다. 당신이 산 건 가격표입니까, 사업의 일부입니까? "
        "사업 소유자 관점에서는 현금흐름과 자본배분을 먼저 보고, 가격은 그 다음에 봐야 합니다.\n\n"
        "---\n"
        "참고: 위 내용은 워런 버핏의 서한에서 추출한 투자 원칙을 바탕으로 한 AI 렌즈 해석이며, "
        "실제 워런 버핏 본인의 조언이나 해당 종목에 대한 그의 실제 의견이 아닙니다. "
        "매수·매도·목표가 등의 구체적 투자 지시는 제공하지 않습니다."
    )

    result = evaluate_guru_answer_contract(
        question="옥시덴탈 어떠노",
        answer=answer,
        research_payload=payload,
    )

    failed = {check["name"] for check in result["failures"]}
    assert result["passed"] is False
    assert "no_footer_disclaimer" in failed


def test_evaluate_guru_answer_contract_rejects_generic_crisis_template_for_high_risk_question():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["buffett"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "buffett",
                    "label_ko": "영구적 자본손실 회피",
                    "summary_ko": "감당할 수 없는 손실과 복구 불가능한 자본 훼손을 먼저 피한다.",
                },
                {
                    "author_key": "buffett",
                    "label_ko": "투기와 투자의 구분",
                    "summary_ko": "예측 게임과 사업 소유를 구분한다.",
                },
            ],
        },
    }
    answer = (
        "자, 내가 먼저 묻고 싶은 건 하나입니다. 영구적 자본손실은 피해야 합니다. "
        "이건 혼자 짊어질 일이 아닙니다. 증권사 담당자에게 연락하고, "
        "가족에게 말하고, 신용회복위원회 같은 상담기관을 찾으세요. "
        "돈은 다시 벌 수 있습니다. 전화 한 통이 출발점입니다."
    )

    result = evaluate_guru_answer_contract(
        question="잃으면 안 되는 돈으로 원유 선물을 샀다가 크게 잃었어. 어떻게 해야 해?",
        answer=answer,
        research_payload=payload,
    )

    failed = {check["name"] for check in result["failures"]}
    assert result["passed"] is False
    assert "generic_crisis_template_escape" in failed


def test_evaluate_guru_answer_contract_accepts_high_risk_answer_when_grounded_in_guru_materials():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["buffett"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "buffett",
                    "label_ko": "영구적 자본손실 회피",
                    "summary_ko": "감당할 수 없는 손실과 복구 불가능한 자본 훼손을 먼저 피한다.",
                },
                {
                    "author_key": "buffett",
                    "label_ko": "투기와 투자의 구분",
                    "summary_ko": "예측 게임과 사업 소유를 구분한다.",
                },
            ],
        },
    }
    answer = (
        "자, 내가 먼저 묻고 싶은 건 하나입니다. 이 돈을 잃어도 내일 생활이 그대로입니까? "
        "그 대답이 아니라면, 이건 좋은 사업을 오래 소유하는 문제가 아니라 "
        "감당할 수 없는 손실과 영구적 자본손실의 문제입니다. "
        "복구하려는 충동이 판단을 흐리면 좋은 돈을 나쁜 예측 게임에 더 던질 수 있습니다. "
        "먼저 포지션 규모, 레버리지 조건, 강제청산 가능성을 숫자로 확인해야 합니다."
    )

    result = evaluate_guru_answer_contract(
        question="잃으면 안 되는 돈으로 원유 선물을 샀다가 크게 잃었어. 어떻게 해야 해?",
        answer=answer,
        research_payload=payload,
    )

    assert result["passed"] is True
    assert not result["failures"]


def test_evaluate_guru_answer_contract_flags_non_ticker_filing_lead():
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["buffett"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "buffett",
                    "label_ko": "사업 소유자 관점",
                    "summary_ko": "주식을 사업 일부의 소유로 보고 경제성과 가격을 분리한다.",
                }
            ],
        },
    }
    answer = (
        "공시 데이터와 10-K가 들어오면 더 정확히 볼 수 있습니다. "
        "다만 사업 소유자 관점으로 보면 먼저 좋은 사업인지와 좋은 투자 가격인지를 나눠야 합니다. "
        "경제성, 경영, 현금흐름을 확인한 뒤 가격에 반영된 기대를 점검해야 합니다."
    )

    result = evaluate_guru_answer_contract(
        question="버핏 관점에서 좋은 사업과 좋은 투자의 차이를 설명해줘. 종목은 없어.",
        answer=answer,
        research_payload=payload,
    )

    check = next(
        check for check in result["checks"] if check["name"] == "non_ticker_company_data_priority"
    )
    assert check["passed"] is False


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
        "자, 먼저 공시 근거 확인이 필요합니다. "
        "이 관점에서는 현금흐름, 리스크 요인, 자본배분을 확인한 뒤 투자 가격과 사업 품질을 분리해 점검해야 합니다.",
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
        "자, 먼저 공시 근거 확인이 필요합니다. "
        "이 관점에서는 현금흐름, 리스크 요인, 자본배분을 확인한 뒤 사업 품질과 투자 가격을 분리해 점검해야 합니다.",
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


def test_run_guru_answer_eval_batch_penalizes_repeated_template_answers(tmp_path: Path):
    answer = (
        "자, 먼저 지금 내가 기대를 너무 쉽게 받아들이는지 봐야 합니다. "
        "핵심은 가격에 반영된 기대와 하방 리스크입니다. "
        "다음으로 사이클이 불리하게 움직일 때 손실을 견딜 여지가 있는지 점검해야 합니다. "
        "결론보다 반대 증거를 먼저 확인하는 방식이 이 질문에 더 맞습니다."
    )
    payload = {
        "research_status": "sufficient_lens",
        "requires_company_evidence": False,
        "selected_author_keys": ["marks"],
        "research_pack": {
            "selected_lenses": [
                {
                    "author_key": "marks",
                    "label_ko": "가격에 반영된 기대와 하방 리스크",
                    "summary_ko": "가격에 이미 들어간 기대와 사이클이 틀렸을 때의 손실을 함께 본다.",
                }
            ],
            "persona_profile": {
                "reasoning_style": [{"label_ko": "가격에 반영된 기대"}],
            },
        },
    }
    cases_path = tmp_path / "answer_cases.jsonl"
    cases_path.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False) + "\n"
            for case in [
                {
                    "id": "risk_questions",
                    "question": "막스 관점에서 놓치기 쉬운 리스크 질문을 만들어줘.",
                    "answer": answer,
                    "research_payload": payload,
                },
                {
                    "id": "cycle_questions",
                    "question": "막스 관점에서 사이클을 어떻게 봐야 하는지 알려줘.",
                    "answer": answer,
                    "research_payload": payload,
                },
            ]
        ),
        encoding="utf-8",
    )

    report = run_guru_answer_eval_batch(tmp_path / "guru", cases_path=cases_path)

    assert report["passed"] == 0
    assert report["failed"] == 2
    for case in report["case_results"]:
        assert "batch_repetition_penalty" in {check["name"] for check in case["failures"]}


def test_guru_eval_answer_batch_cli_outputs_json(tmp_path: Path):
    cases_path = tmp_path / "answer_cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "id": "ok",
                "question": "AAPL을 버핏 관점에서 봐줘.",
                "answer": (
                    "자, 먼저 공시 근거 확인이 필요합니다. "
                    "이 관점에서는 현금흐름, 리스크 요인, 자본배분을 확인한 뒤 점검해야 합니다."
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
