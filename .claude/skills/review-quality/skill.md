---
name: review-quality
description: "QA 리뷰 스킬. 모듈 간 경계면 교차 검증, 임포트/의존성 검증, 스키마 일관성 검증, CLI 동작 검증, 테스트 실행, Phase 1 체크리스트 검증을 수행한다. 코드 리뷰, 품질 검증, 경계면 검사, 누락 확인 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Review Quality Skill

qa-engineer 에이전트가 따라야 할 검증 가이드.

## 1. 경계면 교차 검증

"존재 확인"이 아니라 **"경계면 교차 비교"** 를 수행한다:

### 1.1 Schema ↔ Pydantic 일관성
- `ontology/schema/objects.yaml`의 각 타입 필드가
  `src/krw_ontology/schema/objects.py`의 Pydantic 모델과 정확히 일치하는지
- required/optional 일치
- enum 값 일치
- enum_ref (quote_types, language_signals, claim_types) 참조 일치

### 1.2 ID 생성 ↔ YAML 패턴 일관성
- `objects.yaml`의 id_pattern이 `id_utils.py`의 생성 함수와 일치하는지
- 예: `source:{ticker}:{period}:{doc_type_key}` ↔ `generate_source_document_id()`

### 1.3 Validator ↔ Schema 일관성
- `exact_match.py`가 `EvidenceQuote`의 `source_span_id` + `quote_text` 필드를 사용하는지
- `reference_validator.py`가 모든 ID 참조 필드를 검사하는지
- `metric_validator.py`가 `metric_dictionary.yaml`을 로드하는지

### 1.4 Pipeline Stage ↔ Dev Spec 계약
- 각 스테이지의 Input/Output이 `dev_spec_v1.md` Section 7과 일치하는지
- Orchestrator의 PIPELINE_STAGES 순서가 명세와 일치하는지

### 1.5 ExtractionWorker ↔ Pipeline Stage
- AI 스테이지가 ExtractionWorker의 extract() 메서드를 올바르게 호출하는지
- 배치 크기가 명세(quote=20, claim=15)와 일치하는지

## 2. 임포트/의존성 검증

```bash
# 모든 Python 파일이 정상적으로 import되는지 확인
python -c "import krw_ontology"
python -c "from krw_ontology.cli.main import app"
python -c "from krw_ontology.config.settings import PipelineConfig"
# ... 각 모듈별로 확인
```

- pyproject.toml의 dependencies가 모든 import를 커버하는지
- 순환 import가 없는지

## 3. CLI 동작 검증

```bash
krw-ontology --help
krw-ontology init-workspace
krw-ontology build-evidence-ontology --help
krw-ontology validate --help
krw-ontology build-report --help
```

- 4개 명령어가 모두 등록되어 있는지
- --help 출력에 올바른 파라미터가 표시되는지
- document_type validation이 작동하는지 ("10-Q" 입력 시 에러)

## 4. 테스트 실행

```bash
cd ~/krw-ontology
pip install -e ".[dev]"
pytest tests/unit/ -v
```

실패한 테스트가 있으면 원인 분석:
- import 에러 → 누락된 모듈
- assertion 에러 → 구현 버그
- fixture 에러 → 테스트 데이터 불일치

## 5. Phase 1 체크리스트 검증

`dev_spec_v1.md` Section 20의 체크리스트를 항목별로 확인:

### Foundation
- [ ] pyproject.toml 존재 + 올바른 dependencies
- [ ] CLI skeleton 4개 명령어
- [ ] config/settings.py PipelineConfig
- [ ] config/constants.py DOCUMENT_TYPE mapping
- [ ] schema/id_utils.py ID 생성 함수들
- [ ] 7개 YAML 파일 존재 + 내용 정확
- [ ] utils/io.py JSONL read/write, atomic write
- [ ] utils/logging.py structured logging
- [ ] pipeline/checkpoint.py CheckpointManager
- [ ] 9개 pipeline stages (resolve_ticker ~ build_indexes)
- [ ] pipeline/orchestrator.py

### Validation
- [ ] 7개 validator 파일 존재
- [ ] validate_ontology 스테이지

### Tests
- [ ] 3개 fixture 파일
- [ ] 10개 unit test 파일
- [ ] 1개 integration test
- [ ] 테스트 통과

## 6. QA 리포트 형식

```
## QA Report

### Summary
- Total checks: N
- Passed: X
- Failed: Y
- Warnings: Z

### Critical Issues (must fix)
1. [file:line] description

### Non-critical Issues (should fix)
1. [file:line] description

### Checklist Status
- Foundation: X/20 items
- Validation: X/7 items
- Tests: X/14 items
```
