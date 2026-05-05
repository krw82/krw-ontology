---
name: fix-test-qa
description: "수정된 코드에 대한 테스트 업데이트 및 실행, 최종 QA 경계면 교차 검증. metric validation 테스트, checkpoint 테스트, period 옵션 테스트, 계약 정렬 검증 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix Test & QA — Skill

모든 수정에 대한 테스트 업데이트, 전체 테스트 스위트 실행, QA 경계면 교차 검증을 수행한다.

## Task 1: 테스트 업데이트

### 1-1. Metric edge validation 테스트

**파일:** `tests/unit/test_validate_ontology.py` 또는 `tests/unit/test_reference_validator.py` (존재 확인)

추가할 케이스:
- `metric:revenue`가 `all_objects`에 있는 edge가 reference validation을 통과하는지 확인
- metric dictionary가 없는 경우에도 에러가 나지 않는지 확인
- 정상적인 non-metric edge도 여전히 통과하는지 확인 (regression)

테스트 작성 시 기존 fixture 패턴을 따를 것. metric_dictionary.yaml fixture가 필요하면 `tests/conftest.py`에 추가.

### 1-2. Checkpoint resume 테스트

**파일:** `tests/unit/test_checkpoint.py` (이미 존재)

추가할 케이스:
- `discover_source_document` 이후 `checkpoint_path`가 ctx에서 local 변수로 동기화되는지 확인
- 완료된 stage가 resume 시 skip되는지 확인
- checkpoint 파일에 모든 stage가 기록되는지 확인

### 1-3. Period 옵션 테스트

**파일:** `tests/unit/test_cli.py` 또는 새 `tests/unit/test_orchestrator_period.py`

추가할 케이스:
- `run_pipeline(period="FY2025")` 호출 시 ctx에 period가 저장되는지 확인
- report_date가 있어도 명시적 period가 우선하는지 확인
- period가 None일 때 report_date에서 자동 계산되는지 확인

### 1-4. Edge ID 계약 테스트

별도 테스트 불필요. 아래 QA 검증에서 dev_spec/init_workspace/schema 일치성 확인.

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

### 4-1. Edge ID 계약 일치성 (3-way)

다음 세 파일의 Edge id_pattern이 모두 동일한지 확인:
1. `ontology/schema/objects.yaml`
2. `dev_spec_v1.md`
3. `src/krw_ontology/cli/init_workspace.py`

모두 `edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}` 이어야 함.

### 4-2. Import 검증

```bash
cd ~/krw-ontology
uv run python -c "
from krw_ontology.pipeline.orchestrator import run_pipeline, PIPELINE_STAGES
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.cli.main import app
from krw_ontology.cli.init_workspace import init_workspace
print('All imports OK')
print(f'Pipeline stages: {PIPELINE_STAGES}')
"
```

### 4-3. CLI 동작 검증

```bash
cd ~/krw-ontology
uv run krw-ontology --help
```

4개 명령어가 모두 나타나는지 확인.

### 4-4. Checkpoint 동작 검증

`orchestrator.py`의 `run_pipeline` 루프에서:
- `checkpoint_path = ctx.get("checkpoint_path")` 동기화 라인이 `_execute_stage()` 직후에 있는지 확인
- `_is_code_stage()`가 모든 stage를 포함하는지 확인

## 완료 조건

- [ ] `uv run pytest tests/unit/ -v` 모든 테스트 통과
- [ ] `uv run ruff check .` 0 에러
- [ ] 모든 import 정상
- [ ] CLI --help에 4개 명령어 표시
- [ ] Edge ID 계약 3-way 일치
- [ ] QA 경계면 교차 검증 critical issue 0건
