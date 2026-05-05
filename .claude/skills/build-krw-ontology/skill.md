---
name: build-krw-ontology
description: "10-K Evidence Ontology Workspace 전체 빌드 오케스트레이터. 6개 에이전트 팀을 구성하여 Phase 1(스캐폴드) → Phase 2(병렬: 코드 스테이지 + AI 추출 + 검증기) → Phase 3(테스트) → Phase 4(QA) 순서로 파이프라인을 빌드한다. 프로젝트 빌드, 전체 개발, 파이프라인 구현, Phase 실행, 개발 시작 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build KRW Ontology — Orchestrator Skill

6개 에이전트 팀이 4단계 Phase로 10-K Evidence Ontology Workspace를 빌드하는 오케스트레이터.

## 실행 모드

**에이전트 팀 모드** — TeamCreate로 팀을 구성하고, 팀원 간 SendMessage + 파일 기반 산출물로 조율한다.

## 에이전트 팀 구성

| 에이전트 | 역할 | 사용 스킬 | Phase |
|---------|------|----------|-------|
| scaffold-engineer | 프로젝트 기반 구조 생성 | build-scaffold | 1 |
| code-stage-engineer | 코드 전용 파이프라인 스테이지 | build-pipeline-stages | 2 |
| ai-stage-engineer | AI 추출 스테이지 | build-ai-extraction | 2 |
| validator-engineer | 7개 결정론적 validator | build-validators | 2 |
| test-engineer | 테스트 작성 | build-tests | 3 |
| qa-engineer | QA 리뷰 | review-quality | 4 |

모든 에이전트 모델: `haiku`

## Phase 실행 계획

### Phase 1: Foundation (scaffold-engineer)

의존성: 없음 (최우선 실행)

scaffold-engineer가 `build-scaffold` 스킬에 따라 생성:

1. `pyproject.toml` — 프로젝트 메타데이터, dependencies, CLI entry point
2. `src/krw_ontology/__init__.py`
3. `src/krw_ontology/cli/main.py` — Typer app, 4개 명령어 skeleton
4. `src/krw_ontology/config/settings.py` — PipelineConfig
5. `src/krw_ontology/config/constants.py` — DOCUMENT_TYPE mapping
6. `src/krw_ontology/schema/id_utils.py` — ID 생성 함수들
7. `src/krw_ontology/schema/objects.py` — Pydantic 모델 (12개 객체 타입)
8. `src/krw_ontology/utils/io.py` — JSONL read/write, atomic write, SHA-256
9. `src/krw_ontology/utils/logging.py` — StructuredFormatter
10. `src/krw_ontology/errors.py` — 커스텀 예외 계층
11. `ontology/schema/` — 7개 YAML 파일
12. `src/krw_ontology/cli/init_workspace.py` — init-workspace 명령어

**완료 조건:**
- `pip install -e ".[dev]"` 성공
- `krw-ontology --help` 출력
- `python -c "from krw_ontology.schema.objects import SourceSpan"` 성공
- 모든 YAML 파일 존재

**산출물 위치:** 프로젝트 루트에 직접 파일 생성

Phase 1 완료 후 Phase 2를 시작한다. Phase 2의 3개 에이전트는 서로 독립적이므로 **병렬 실행**한다.

### Phase 2: Implementation (병렬)

3개 에이전트를 동시에 실행한다. 각 에이전트는 Phase 1에서 생성된 스키마/유틸리티에 의존한다.

#### 2A: code-stage-engineer (build-pipeline-stages 스킬)

생성 파일:
1. `src/krw_ontology/pipeline/checkpoint.py` — CheckpointManager
2. `src/krw_ontology/pipeline/orchestrator.py` — PIPELINE_STAGES 순서 정의
3. `src/krw_ontology/pipeline/stages/resolve_ticker.py`
4. `src/krw_ontology/pipeline/stages/discover_source.py`
5. `src/krw_ontology/pipeline/stages/download_source.py`
6. `src/krw_ontology/pipeline/stages/clean_to_markdown.py`
7. `src/krw_ontology/pipeline/stages/extract_sections.py`
8. `src/krw_ontology/pipeline/stages/build_spans.py`
9. `src/krw_ontology/pipeline/stages/extract_xbrl.py`
10. `src/krw_ontology/pipeline/stages/build_indexes.py`

**완료 조건:**
- 각 스테이지 모듈이 import 가능
- CheckpointManager 단위 테스트 통과

#### 2B: ai-stage-engineer (build-ai-extraction 스킬)

생성 파일:
1. `src/krw_ontology/extraction/worker.py` — ExtractionWorker
2. `src/krw_ontology/extraction/schemas.py` — AI 출력 Pydantic 모델
3. `src/krw_ontology/extraction/prompts/quote_extraction.py`
4. `src/krw_ontology/extraction/prompts/claim_extraction.py`
5. `src/krw_ontology/extraction/prompts/object_extraction.py`
6. `src/krw_ontology/extraction/prompts/edge_generation.py`
7. `src/krw_ontology/pipeline/stages/extract_evidence_quotes.py`
8. `src/krw_ontology/pipeline/stages/extract_research_claims.py`
9. `src/krw_ontology/pipeline/stages/extract_risks_drivers_headwinds.py`
10. `src/krw_ontology/pipeline/stages/extract_assumption_candidates.py`
11. `src/krw_ontology/pipeline/stages/generate_edges.py`

**완료 조건:**
- extraction 모듈 import 가능
- 각 AI 스테이지가 ExtractionWorker를 올바르게 호출

#### 2C: validator-engineer (build-validators 스킬)

생성 파일:
1. `src/krw_ontology/validators/schema_validator.py`
2. `src/krw_ontology/validators/exact_match.py`
3. `src/krw_ontology/validators/reference_validator.py`
4. `src/krw_ontology/validators/support_validator.py`
5. `src/krw_ontology/validators/metric_validator.py`
6. `src/krw_ontology/validators/numeric_guard.py`
7. `src/krw_ontology/validators/relation_validator.py`
8. `src/krw_ontology/pipeline/stages/validate_ontology.py`

**완료 조건:**
- 7개 validator 모듈 import 가능
- 각 validator가 순수 함수로 구현됨

Phase 2 전원 완료 후 Phase 3을 시작한다.

### Phase 3: Tests (test-engineer)

의존성: Phase 1 + Phase 2 전원 완료

test-engineer가 `build-tests` 스킬에 따라 생성:

1. `tests/conftest.py` — 공통 fixture
2. `tests/fixtures/mini_10k.html`
3. `tests/fixtures/mini_10k_clean.md`
4. `tests/fixtures/mini_10k_spans.jsonl`
5. `tests/unit/test_id_utils.py`
6. `tests/unit/test_schema_validator.py`
7. `tests/unit/test_exact_match.py`
8. `tests/unit/test_reference_validator.py`
9. `tests/unit/test_support_validator.py`
10. `tests/unit/test_metric_validator.py`
11. `tests/unit/test_numeric_guard.py`
12. `tests/unit/test_relation_validator.py`
13. `tests/unit/test_checkpoint.py`
14. `tests/unit/test_io.py`
15. `tests/integration/test_pipeline_mini_10k.py`

**완료 조건:**
- `pytest tests/unit/ -v` 통과
- 통합 테스트 실행 (AI 스테이지는 mock)

### Phase 4: QA Review (qa-engineer)

의존성: Phase 3 완료

qa-engineer가 `review-quality` 스킬에 따라 검증:

1. 경계면 교차 검증 (Schema ↔ Pydantic, ID ↔ YAML, Validator ↔ Schema 등)
2. 임포트/의존성 검증
3. CLI 동작 검증
4. 테스트 실행 및 결과 분석
5. Phase 1 체크리스트 검증
6. QA 리포트 작성

**완료 조건:**
- Critical issues = 0
- 모든 Phase 1 체크리스트 항목 통과

## 데이터 전달 프로토콜

| 전략 | 용도 | 방식 |
|------|------|------|
| 태스크 기반 | 진행 상황 추적 | TaskCreate/TaskUpdate |
| 파일 기반 | 산출물 | 프로젝트 루트에 직접 파일 생성 |
| 메시지 기반 | 실시간 조율 | SendMessage |

## 에러 핸들링

- Phase 1 실패 → 전체 중단. 스키마/유틸리티가 없으면 이후 Phase 불가
- Phase 2 개별 에이전트 실패 → 실패한 에이전트만 재시도. 나머지는 계속 진행
- Phase 2 전원 실패 → Phase 3 불가, 원인 분석 후 재시도
- Phase 3 테스트 실패 → 실패한 테스트 원인 분석하여 해당 Phase 2 에이전트에 수정 요청
- Phase 4 QA critical issues → 해당 에이전트에 수정 요청 후 재검증

## 팀 실행 절차

```
1. TeamCreate(team_name="krw-ontology-build")
2. TaskCreate(Phase 1 작업) → scaffold-engineer 할당
3. scaffold-engineer 완료 대기
4. TaskCreate(Phase 2A, 2B, 2C 작업) → 3개 에이전트 병렬 할당
5. 3개 에이전트 완료 대기
6. TaskCreate(Phase 3 작업) → test-engineer 할당
7. test-engineer 완료 대기
8. TaskCreate(Phase 4 작업) → qa-engineer 할당
9. qa-engineer 완료 → 팀 정리
```

## 테스트 시나리오

### 정상 흐름
1. scaffold-engineer가 pyproject.toml부터 objects.py까지 모든 기반 파일 생성
2. 3개 에이전트가 병렬로 각자의 모듈 생성
3. test-engineer가 모든 테스트 작성 후 `pytest tests/unit/ -v` 통과
4. qa-engineer가 경계면 교차 검증 수행 후 critical issue 0건 확인

### 에러 흐름 (Phase 2 validator 실패)
1. validator-engineer가 reference_validator.py 작성 중 import 에러 발생
2. 오케스트레이터가 에러 로그 확인 → schema/objects.py에 누락된 필드 발견
3. scaffold-engineer에 수정 요청 SendMessage
4. scaffold-engineer가 objects.py 수정 후 완료 메시지
5. validator-engineer가 재시도하여 완료
6. 이후 Phase 3, 4 정상 진행
