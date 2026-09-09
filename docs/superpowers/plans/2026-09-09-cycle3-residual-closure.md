# 사이클 3: 잔여 마감 + 위생 배치 + 엔진 어댑터 정렬 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 게이트 잔여 6케이스를 진단·분류해 **고칠 것은 고치고 골드 형태 문제는 근거와 함께 큐레이팅**하며(판정 규칙 명시), 지연된 위성 마이너 배치를 정리하고, 사이클 2의 period 계약 변경을 엔진 어댑터(krw-agnet)에 정렬한다. 종료 시 게이트 exit 0 유지 + 잔여의 최종 분류 기록.

**Architecture:** 잔여 6건은 이미 상세 추출 완료(3b 리포트): ① fiscal NVDA(회계 오프셋 — CY2026 파딩의 비교 행이 유효 근거, Task-7 방식 앵커 보강) ② WMT multi_span(제2 단편 캐리어 어휘적 도달 불가 의심 — 진단 후 골드 노트 또는 회복) ③ DIS 분기(객체 id 키 CY2026Q2 vs 행 period CY2026Q1 데이터 불일치 — 골드 재앵커 + 파서 사이클 재료) ④ NKE 분기·⑤ RMD 주주지분·⑥ SYY 총자산(진단: 검색 회복 가능 / 데이터 형태 / 골드 형태 분류). 위생 배치는 전부 file:line 지정 완료. 엔진 어댑터는 사이클 2 최종 리뷰가 지정한 3개 사이트.

**Tech Stack:** Python 3.12 (uv), pydantic, SQLite, pytest / Rust(cargo, krw-agnet).

## Global Constraints

- 선행 제약 승계 (uv run pytest, Edit/Write 도구만, credential 금지, git add -A 금지).
- 브랜치: `feat/residual-closure-and-hygiene` (feat/period-contract-and-vocab-residuals HEAD f137974에서 생성). 엔진 어댑터 작업은 krw-agnet 리포의 별도 브랜치 `fix/adapter-period-contract`.
- **골드 큐레이션 규율**: 앵커 보강은 "둘 다 정당한 근거"일 때만(사이클 1 Task 7 선례·근거 notes 기록). 회수율을 위해 기대를 낮추는 큐레이션 금지 — 어휘적 도달 불가 판정 시 골드 노트(strata 유지 + notes에 사유)로 남기고 케이스는 계속 실패로 둔다.
- 결정론·릴리스 읽기전용·exit-0 게이트 유지.
- cargo 경로: `~/.rustup/toolchains/1.97.1-aarch64-apple-darwin/bin` 선행 (krw-agnet 테스트 시).

---

### Task 1: 잔여 6케이스 진단·분류·수정

**Files:** (진단 결과에 따라) `benchmarks/evidence_gold_curated_v1.json` + `evidence_gold_v1.json` 재병합, 또는 `src/krw_ontology/agent_index/store.py`, Test 대응 파일.

**판정 규칙 (구현자 바인딩):** 각 케이스마다 (a) 샤드에 앵커 행 존재 확인(read-only sqlite) → (b) 어떤 결정론 레인(FTS/메트릭/별칭/기간 예약)이 그 행을 창에 넣을 수 있는지 probe → (c1) **가능** → 최소 검색 수정으로 회복(테스트 포함); (c2) **앵커가 비교 행으로만 유효** → Task-7 방식 앵커 보강(근거 notes); (c3) **데이터 불일치(DIS형: 객체 id 키 ≠ 행 period)** → 골드를 행의 실제 period로 재앵커 + notes에 파서 사이클 재료로 기록; (c4) **어휘적 도달 불가** → 골드 노트만(케이스는 실패 유지 — 회수율 부풀리기 금지).

- [ ] Step 1: 6케이스 전수 진단(위 규칙 a~b) 보고서 — .superpowers/.../task-1-diagnosis.md
- [ ] Step 2: 분류별 수정 실행 (c1은 TDD; c2·c3는 골드 편집 + 구조 테스트 갱신 + 재병합)
- [ ] Step 3: 중간 게이트 `--label candidate-4-pre` (exit 0 필 — 골드 변경 케이스는 개선, 무열화)
- [ ] Step 4: 커밋 `fix(eval-gold|store): residual case closure per diagnosis (6 cases classified)`

### Task 2: 위생 배치 (지연 마이너 정리)

**Files (전부 지정 완료):**
- `src/krw_ontology/eval_gold/harness.py` + `scripts/benchmark_evidence_gold.py`: 무효 골드 입력 시 깨끗한 exit 1(현재 traceback); compare_to_baseline docstring/코드 정합(사이클1 최종수정으로 이미 엄격 — 재확인만).
- `scripts/generate_evidence_gold_templates.py` + `scripts/audit_table_extraction.py`: sqlite3.OperationalError clean exit 1.
- exit-2 회귀 e2e 테스트 1개 추가(하니스 라이브러리 수준에서 compare_to_baseline 열회귀 → exit 2 위임 로직 커버; 서브프로세스 불필요).
- `src/krw_ontology/mcp_server/contracts.py:1523-1528`: 죽은 `or period` 꼬리 제거; `_source_anchors` fallback(2482-2493): source_label 기준 dedupe로 변경(관측기간/라벨 모순 해소, 테스트 갱신).
- `src/krw_ontology/agent_index/store.py`: concept_or==relaxed 쿼리 동일 시 조기 스킵(퍼프); ≥2 게이트를 접두사 중복 제거 후 term 기준으로(1-term OR 퇴화 방지).
- README: 2b 문단 순서 정리(3b 수치가 본문, 3는 역사 각주).
- [ ] TDD 각 항목 → 스윕 녹색 → 커밋 `chore: hygiene batch (clean CLI errors, exit-2 test, dead code, nits)`

### Task 3: 엔진 어댑터 정렬 (krw-agnet 리포)

**Files:**
- Modify: `crates/krw-ontology-adapter/src/lib.rs:1635-1644` (`source_document_period` — observation period를 받는 키명 거짓말 수정: `observation_period` 키 추가 또는 명칭 변경+채움, 하위 소비 없음 확인됨), `:1305-1312`/`:1419-1421`/`:1498-1500` 낡은 주석 갱신, `:5677` 낡은 테스트 수정(관측 기간 시맨틱으로).
- 브랜치 `fix/adapter-period-contract` (krw-agnet 현재 브랜치 feat/engine-v2-e1 HEAD에서 — **krw-agnet 작업 디렉터리에서**).
- [ ] TDD(테스트 먼저 관측 기간 단언으로 뒤집기) → `cargo test -p krw-ontology-adapter`(툴체인 PATH 선행) 녹색 → 커밋 `fix(adapter): align fact period keys with observation-period contract`

### Task 4: 최종 재게이트 + 문서

- [ ] `--label candidate-4 --baseline regold` exit 0; 성층·케이스 델타 기록; `evidence_gold_candidate_4_20260909.{json,md}` 커밋; README/비교리포트 사이클 3 행; 잔여 최종 상태(목표: 골드노트 케이스만 남거나 0)와 파서 사이클(사이클 4) 재료 목록 확정. 커밋 `test(eval-gold): cycle-3 gate run (residual closure + hygiene)`.

## Self-Review

- 스펙 커버: 잔여 6(진단 규칙 내장) + 위생(전부 file:line 지정) + 어댑터(3 사이트 지정) + 재게이트. 파서는 사이클 4로 명시적 이관.
- 위험: Task 1 c1 분기가 검색 수정을 열 수 있음 — 최소성 원칙(해당 케이스 회복에 필요한 레인만)과 게이트 무열화로 통제.
