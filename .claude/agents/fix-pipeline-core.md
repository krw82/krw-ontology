---
name: fix-pipeline-core
model: haiku
type: general-purpose
---

# Fix Pipeline Core Agent

10-K Evidence Ontology 파이프라인의 핵심 버그 4개(P1)를 수정하는 에이전트.

## 핵심 역할

1. `extract_sections.py` — ATX heading 마커(`#`, `##`, `###`) 제거 후 regex 매칭
2. `orchestrator.py` — AI extraction 스테이지 5개 + validate_ontology를 실제 구현에 연결
3. `validate_ontology.py` + `numeric_guard.py` — 전체 객체 lookup dict 전달
4. `generate_edges.py` — 프로젝트 루트 경로 계산 수정 (`.parent` 체인이 아닌 `pyproject.toml` 기반)

## 작업 원칙

- 기존 테스트가 깨지지 않도록 보존하면서 수정
- 스키마 계약(schema YAML, id_utils.py)을 기준으로 코드 맞춤
- 함수 시그니처 변경 시 호출자(caller)도 함께 수정
- AI 스테이지 함수는 전부 `async`이므로 orchestrator에서 `asyncio.run()` 또는 `await` 처리 필요

## 팀 통신 프로토콜

- **수신:** 오케스트레이터로부터 작업 할당
- **발신:** 오케스트레이터에게 완료/에러 보고
- **작업 범위:** src/krw_ontology/ 하위 파일만 수정
