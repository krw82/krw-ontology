---
name: build-tests
description: "테스트 작성 스킬. fixture, unit test, integration test를 작성하여 모든 모듈의 정확성을 검증한다. 테스트, pytest, fixture, coverage, validator 테스트 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build Tests Skill

test-engineer 에이전트가 따라야 할 구현 가이드.

## 1. conftest.py

공통 fixture:
- `project_root` — 프로젝트 루트 경로
- `mini_10k_html` — tests/fixtures/mini_10k.html 경로
- `mini_10k_clean_md` — tests/fixtures/mini_10k_clean.md 경로
- `mini_10k_spans` — tests/fixtures/mini_10k_spans.jsonl 파싱 결과
- `sample_source_span` — 합성 SourceSpan dict
- `sample_evidence_quote` — 합성 EvidenceQuote dict
- `sample_research_claim` — 합성 ResearchClaim dict
- `metric_dictionary` — metric_dictionary.yaml 파싱 결과
- `relations_whitelist` — relations.yaml 파싱 결과
- `tmp_workspace` — 임시 작업 디렉토리 (tmp_path 사용)

## 2. Fixture 파일

### mini_10k.html (~2KB)
최소 합성 10-K HTML. 포함:
- Cover page (Company Name, CIK)
- Item 1 (Business) — 2~3 문단
- Item 1A (Risk Factors) — 3~4 risk factor 문단
- Item 7 (MD&A) — 2~3 문단, 숫자 포함
- 간단한 XBRL inline 태그 (선택)
- HTML 구조: <html><body><div> with SEC 표준 구조

### mini_10k_clean.md
mini_10k.html → clean_to_markdown 변환의 예상 출력.

### mini_10k_spans.jsonl
mini_10k_clean.md의 섹션을 분할한 예상 span JSONL.

## 3. Unit Tests

`dev_spec_v1.md` Section 17.1의 10개 테스트 파일:

### test_id_utils.py
- normalize_doc_type("10-K") == "10K"
- denormalize_doc_type("10K") == "10-K"
- generate_source_document_id("AAPL", "FY2025", "10K") == "source:AAPL:FY2025:10K"
- generate_scoped_id("risk", "AAPL", "FY2025", "10K", "supply-chain") 형식 검증
- generate_metric_id("gross_margin") == "metric:gross_margin"
- XBRLFact hash 생성 검증
- Edge hash 생성 검증

### test_schema_validator.py
- 유효한 SourceSpan 객체 통과
- 필수 필드 누락 시 rejection
- enum 값 불일치 시 rejection
- type 판별 검증 (RiskFactor vs GrowthDriver vs Headwind)

### test_exact_match.py
- 정확한 substring → 통과
- Whitespace normalization 후 통과
- 존재하지 않는 텍스트 → 실패
- 존재하지 않는 span_id → 실패

### test_reference_validator.py
- 유효한 참조 → 통과
- Dangling quote ID → rejected
- Dangling claim ID → rejected
- Dangling from_id/to_id in Edge → rejected
- 여러 dangling ref → 모두 보고

### test_support_validator.py
- supported_by_claims 있는 RiskFactor → 통과
- supported_by_quotes만 있는 GrowthDriver → 통과
- 둘 다 없는 Headwind → rejected
- EvidenceQuote는 support 검증에서 제외

### test_metric_validator.py
- Canonical metric → 통과
- Unknown metric → unmapped_metrics로 이동 + needs_review
- 여러 unknown metrics → 모두 unmapped

### test_numeric_guard.py
- 숫자가 quote에 있음 → 통과
- 숫자가 XBRL에 있음 → 통과
- 숫자가 어디에도 없음 → rejected
- 숫자가 없는 claim → 통과 (nothing to validate)
- XBRL unavailable + 숫자가 quote에 있음 → 통과

### test_relation_validator.py
- Whitelist에 있는 relation → 통과
- Whitelist에 없는 relation → 실패
- from/to type 불일치 → 실패
- relation_name 불일치 → 실패

### test_checkpoint.py
- save/load 완전성
- is_stage_complete 판별
- mark_complete 후 상태 변경
- 빈 체크포인트 기본값

### test_io.py
- JSONL write + read 완전성
- Atomic write (파일 존재 확인)
- SHA-256 계산 정확성

## 4. Integration Test

### test_pipeline_mini_10k.py
- mini_10k.html로 전체 파이프라인 실행
- AI 스테이지는 mock으로 대체
- 결과:
  - 모든 JSONL 파일 존재 확인
  - artifact_index.json 존재 + 올바른 경로
  - validate_ontology 통과
  - graph_report.md 존재
  - audit_report.md 존재

## 5. 테스트 실행 명령

```bash
pytest tests/unit/ -v                    # unit tests only
pytest tests/ -v -m "not slow"          # slow 제외
pytest tests/ -v --run-slow              # 전체
pytest tests/ --cov=src/krw_ontology     # coverage
```
