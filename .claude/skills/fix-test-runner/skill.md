---
name: fix-test-runner
description: "수정된 코드에 대한 테스트 업데이트 및 실행, 최종 QA 경계면 교차 검증. 테스트 수정, QA 검증, 파이프라인 검증 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix Test Runner — Skill

수정된 코드에 대한 테스트를 업데이트하고, 전체 테스트 스위트를 실행하며, QA 경계면 교차 검증을 수행한다.

## Task 1: 기존 테스트 확인 및 업데이트

### 1-1. extract_sections 테스트

**파일:** `tests/unit/test_extract_sections.py` (존재 확인 필요)

ATX heading 마커가 포함된 입력으로도 섹션이 정상 감지되는지 확인:
- `### Item 1. Business` → `item1` 감지
- `## Item 1A. Risk Factors` → `item1a` 감지
- `# Item 7. Management's Discussion` → `item7` 감지

기존 테스트가 plain text 기반이었다면 ATX heading 버전도 추가.

### 1-2. numeric_guard 테스트

**파일:** `tests/unit/test_numeric_guard.py`

claim→quote 지원 경로를 테스트하는 케이스가 있는지 확인. 없으면 추가:
- RiskFactor가 `supported_by_claims`를 가지고
- 해당 claim이 `supported_by_quotes`를 가지고
- quote 텍스트에 숫자가 포함된 경우가 정상 pass하는지 확인

### 1-3. orchestrator 테스트 (있으면)

`orchestrator._execute_stage()`가 AI 스테이지를 mock ExtractionWorker로 호출하는지 확인.

### 1-4. io 유틸리티 테스트

**파일:** `tests/unit/test_io.py`

`find_project_root()` 함수에 대한 테스트 추가.

## Task 2: 전체 테스트 실행

```bash
cd ~/krw-ontology
uv run pytest tests/unit/ -v
```

모든 테스트가 pass해야 함. 실패하면 원인 분석 후 수정.

## Task 3: ruff lint 확인

```bash
cd ~/krw-ontology
uv run ruff check .
```

0 에러 확인.

## Task 4: QA 경계면 교차 검증

### 4-1. Schema ↔ Pydantic 일치성

`ontology/schema/objects.yaml`의 Edge ID description이 `id_utils.py`의 `generate_scoped_id("edge", ...)` 출력과 일치하는지 확인.

### 4-2. ID ↔ YAML 일치성

모든 객체 타입의 ID 생성 함수가 schema YAML의 description과 일치하는지 확인.

### 4-3. Validator ↔ Schema 일치성

`validate_ontology.py`가 `_ACCEPTED_FILES` 매핑에서 모든 JSONL 파일을 올바르게 처리하는지 확인.

### 4-4. CLI ↔ Pipeline 일치성

- `validate` 명령어가 `run_validate_ontology()`를 올바른 경로로 호출하는지 확인
- `build-report` 명령어가 `build_indexes()`를 올바른 경로로 호출하는지 확인

### 4-5. Import/의존성 검증

```bash
cd ~/krw-ontology
uv run python -c "
from krw_ontology.pipeline.orchestrator import run_pipeline, PIPELINE_STAGES
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.pipeline.stages.extract_evidence_quotes import extract_evidence_quotes
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.pipeline.stages.extract_risks_drivers_headwinds import extract_risks_drivers_headwinds
from krw_ontology.pipeline.stages.extract_assumption_candidates import extract_assumption_candidates
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.cli.main import app
print('All imports OK')
print(f'Pipeline stages: {PIPELINE_STAGES}')
"
```

### 4-6. CLI 동작 검증

```bash
cd ~/krw-ontology
uv run krw-ontology --help
```

4개 명령어가 모두 나타나는지 확인.

## 완료 조건

- [ ] `uv run pytest tests/unit/ -v` 모든 테스트 통과
- [ ] `uv run ruff check .` 0 에러
- [ ] 모든 import 정상
- [ ] CLI --help에 4개 명령어 표시
- [ ] QA 경계면 교차 검증 critical issue 0건
