---
name: build-validators
description: "7개 결정론적 validator 구현 스킬. schema, exact_match, reference, support, metric, numeric_guard, relation validator를 구현하고 validate_ontology 파이프라인 스테이지를 연결한다. 검증, validator, 참조 무결성, numeric guard 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build Validators Skill

validator-engineer 에이전트가 따라야 할 구현 가이드.

## 1. validator 실행 순서

`dev_spec_v1.md` Section 8.1을 따른다. 이전 validator에서 실패한 객체는 다음 validator에서 테스트하지 않는다:

```
1. schema_validator     → Pydantic model validation
2. exact_match          → Quote text substring check
3. reference_validator  → ID referential integrity
4. support_validator    → Claim/quote support requirement
5. metric_validator     → Canonical metric mapping
6. numeric_guard        → Numeric claim XBRL cross-check
7. relation_validator   → Edge relation whitelist
```

## 2. 각 validator 상세

### schema_validator.py (Section 8.1 #1)
- Pydantic 모델로 JSON 검증
- required fields, type constraints, enum values 확인
- 실패 시: rejected + rejection_reason에 누락 필드 명시

### exact_match.py (Section 8.2)
- `quote_text`가 referenced `SourceSpan.text`의 substring인지 확인
- 우선 raw match → 실패 시 whitespace normalization 후 재시도
- `validate_exact_match(quote, all_spans) -> bool`
- `validate_exact_match_normalized(quote, all_spans) -> bool`
- EvidenceQuote 타입만 검증, 다른 타입은 통과

### reference_validator.py (Section 8.3)
- 모든 ID 참조가 존재하는 객체를 가리키는지 확인
- 검증 대상:
  - EvidenceQuote.source_span_id
  - ResearchClaim.supported_by_quotes
  - ResearchObject.supported_by_claims, supported_by_quotes
  - AssumptionCandidate.supported_by_claims, supported_by_quotes
  - LanguageSignal.source_quote_id
  - Edge.from_id, to_id
- Dangling reference → 즉시 rejected (needs_review 아님)
- metric 이름은 metric_validator 담당, 여기서 검증하지 않음

### support_validator.py (Section 8.4)
- RiskFactor, GrowthDriver, Headwind, AssumptionCandidate는
  supported_by_claims 또는 supported_by_quotes 중 최소 1개 필요
- 둘 다 비어있으면 rejected

### metric_validator.py (Section 8.5)
- `related_metrics`, `affects` 필드의 값이 metric_dictionary.yaml의 canonical_metrics에 있는지 확인
- unknown metric → unmapped_metrics로 이동, review_status = "needs_review"
- 새 metric 자동 생성 금지

### numeric_guard.py (Section 8.6)
- ResearchClaim, AssumptionCandidate, RiskFactor, GrowthDriver, Headwind의
  텍스트 필드에서 숫자 추출
- 각 숫자가 supporting quote_text 또는 XBRL fact에서 발견되어야 함
- XBRL이 unavailable하면 quote text만으로 검증
- unsupported numeric → rejected (needs_review 아님)
- AssumptionCandidate.value_hint: non-null numeric이면 동일 검증 적용

### relation_validator.py (Section 8.7)
- Edge의 from_id/to_id 객체 타입이 relations.yaml whitelist와 일치하는지
- relation_name, relation_id가 whitelist와 일치하는지
- from_type, to_type, relation_name, relation_id 4개 필드 모두 매치 필요

## 3. validate_ontology 스테이지

pipeline/stages/에 validate_ontology.py 생성:
1. 모든 JSONL 파일 로드
2. 7개 validator 순차 실행
3. accepted 객체는 JSONL에 유지
4. rejected 객체는 rejected_objects.jsonl에 추가
5. batch_failures.jsonl은 보존
6. audit_report.md 갱신
7. 결과: {accepted: dict[str, list], rejected: list}

## 4. 구현 원칙

- 모든 validator 함수는 순수 함수 (사이드 이펙트 없음)
- 입력: 검증할 객체 + 컨텍스트 (all_objects, metric_dictionary 등)
- 출력: (is_valid: bool, reason: str | None)
- rejected 객체는 원본 전체를 보존하여 rejected_objects.jsonl에 기록
