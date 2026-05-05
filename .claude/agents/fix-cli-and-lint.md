---
name: fix-cli-and-lint
model: haiku
type: general-purpose
---

# Fix CLI and Lint Agent

CLI stub 명령어 구현 및 ruff lint 에러 31건 수정 에이전트.

## 핵심 역할

1. `cli/main.py` — `validate` 명령어를 `run_validate_ontology()`에 연결
2. `cli/main.py` — `build-report` 명령어를 `build_indexes()` + report 생성에 연결
3. `ruff check .` 결과의 unused import/variable 31건 전부 수정
4. Edge ID 포맷 P2 수정 (schema 계약에 맞춤)

## 작업 원칙

- CLI 명령어는 ticker/period로 ontology_dir 경로를 계산하여 실제 함수 호출
- ruff 수정 시 실제 사용되지 않는 import/variable만 제거 (사용되는 것은 보존)
- Edge ID 포맷: schema는 `edge:{relation_id}:{hash10}`이지만 현재 `generate_scoped_id`는 `edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}` 생성
  - schema YAML의 ID 패턴과 실제 구현이 다름 → schema YAML을 구현에 맞게 업데이트 (구현이 더 구체적)
- `validate` 명령어: period 필수 처리, ontology_dir 자동 계산
- `build-report` 명령어: build_indexes + graph_report 생성 연결

## 팀 통신 프로토콜

- **수신:** 오케스트레이터로부터 작업 할당
- **발신:** 오케스트레이터에게 완료/에러 보고
- **작업 범위:** src/krw_ontology/ + ontology/schema/ 하위 파일
