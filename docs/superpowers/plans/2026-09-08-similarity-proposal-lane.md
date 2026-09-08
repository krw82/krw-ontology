# 유사도 제안 레인 (Phase 2a: 어휘 브리징 + 퓨전 형평) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 베이스라인(2026-09-08)에서 측정된 두 검색 격차 — vocabulary_mismatch mean_recall 0.125, multi_period 0.25 — 를 **결정론적 메커니즘만으로** 닫고, 동일 골든셋(736케이스)에서 베이스라인 대비 열화 없음(게이트 exit 0) 또는 개선을 입증한다.

**Architecture:** 유사도 제안 레인의 2a 단계. 임베딩 없이 (1) 메트릭 사전 **정적 별칭 확장**(YAML, 결정론), (2) 절에 `metrics`가 없을 때 별칭 토큰이 매핑되면 스토어가 **metric-lookup 채널을 추가 발동**(match_mode="alias_expanded" 라벨 + 진단 기록; 서버는 플랜을 재작성하지 않음 — 검색측 확장만), (3) 요청 기간별 metric 유닛이 퓨전 창에서 굶주리지 않는 **기간별 예약 할당**. 덴스(sqlite-vec) 레인은 2a 후 잔여 갭 측정해 조건부(Plan 2b).

**Tech Stack:** Python 3.12 (uv), pydantic, SQLite(기존), pytest. 기존 `eval_gold` 하니스가 게이트.

## Global Constraints

- Plan 1의 모든 제약 승계: `uv run pytest`, 소스는 Edit/Write 도구만, credential 리터럴 금지, `git add -A` 금지, pytest 요약 확인 후 커밋.
- 브랜치: `feat/similarity-proposal-lane` (feat/evidence-gold-harness HEAD에서 생성 — 스택형).
- **결정론 교리**: 별칭 테이블은 정적 YAML. 검색 경로에 LLM/임베딩/시간/난수 금지. 동일 골드+릴리스 재실행 → 동일 pass/fail.
- **플랜 불변**: 서버는 SearchPlan을 재작성/추론하지 않는다. 별칭 확장은 검색측 부가 채널 + 진단이며, 플랜 JSON은 바뀌지 않는다.
- 비교 기준: Task 0의 재베이스라인 v1.1 (CY/FY 아티팩트 제거 후) — 최종 비교는 같은 그레이더로 같은 골드에 대해.
- 절대경로 보호 동일 (prod 불가, 원본 리포 불가, release 20260830_193811 읽기 전용).
- 커밋 접두사 fix|feat|test|chore.

---

### Task 0: 그레이더 CY/FY 라벨 정규화 + 재베이스라인 v1.1

**Files:**
- Modify: `src/krw_ontology/eval_gold/grader.py` (period 비교 부분, ~:38)
- Modify: `tests/unit/test_eval_gold_grader.py` (정규화 테스트 추가)
- Create: `benchmarks/reports/evidence_gold_baseline_v1_1_20260908.{json,md}` (재측정 산출물)

**Interfaces:**
- Produces: `normalize_period_label(s: str) -> str` — `FY2024`≡`CY2024` 라벨 동등화(숫자만 비교, 접두사 무시; `2026Q1` 등은 기존 유지). `match_expected`의 period 비교가 이 함수를 경유.

**근거**: Task 6 발견 — 90건 실패 중 5건은 "앵커 객체는 반환됐으나 유닛 period가 `FY2024`로 노출돼 골드 `CY2024`와 불일치 처리"된 측정 아티팩트. 샤드는 파딩을 CY키로 관리하며 일부 유닛이 FY 라벨을 노출한다. 라벨 동등화는 "같은 파딩 키의 두 표기"를 같게 취급하는 것(달력 동등성 아님) — fiscal_offset 성층의 의미(CY키 파딩 기대)는 유지된다.

- [ ] **Step 1: 실패 테스트** — `test_period_label_fy_cy_equivalence`: item.period="CY2024", unit.period="FY2024" → match; item.period="CY2024", unit.period="FY2025" → no match; item.period="CY2026Q1" 등 분기 라벨은 그대로 엄격.
- [ ] **Step 2: 실패 확인 → Step 3: 구현** — `normalize_period_label`: `^(FY|CY)(\d{4})$` 두 형태를 `Y\4`... 대신 `f"CY{digits}"`로 정규화해 반환; 다른 형태는 원문 반환. `match_expected`의 `item.period`/`unit.period` 비교 양측에 적용.
- [ ] **Step 4: 그레이더·템플릿·하니스 전체 eval_gold 스위트 녹색 확인** (`uv run pytest tests/unit -q -k "eval_gold or benchmark_evidence"` — 41개 기대).
- [ ] **Step 5: 재베이스라인 실행** — `KRW_ONTOLOGY_RELEASE_ROOT=<release> uv run python scripts/benchmark_evidence_gold.py --gold benchmarks/evidence_gold_v1.json --label baseline-v1.1` 후 산출물을 위 안정 이름으로 복사. README의 베이스라인 표에 v1.1 행 추가(아티팩트 제거 명시). 기대: overall pass 0.8777 → ~0.884 (5/736 회복).
- [ ] **Step 6: 커밋** — `fix(eval-gold): FY/CY period-label equivalence in grader + re-baseline v1.1`

### Task 1: 메트릭 사전 별칭 확장

**Files:**
- Modify: `ontology/schema/metric_dictionary.yaml` (별칭 추가)
- Modify: `src/krw_ontology/agent_index/metric_dictionary.py` (별칭 조회 API)
- Test: `tests/unit/test_metric_dictionary_aliases.py` (기존 metric_dictionary 테스트 파일이 있으면 거기에)

**Interfaces:**
- Consumes: 기존 `MetricDictionary`/`canonicalize` (metric_dictionary.py — 실행 시 실제 클래스명 확인; fallback id 경로 :207 부근)
- Produces: `resolve_alias_terms(text: str) -> dict[str, str]` — 텍스트에서 별칭 토큰을 찾아 `{별칭표현: canonical_metric}` 반환. 대소문자 무시, 단어 경계 매칭, 복수형 허용. 다중 히트 가능.

- [ ] **Step 1: 실패 테스트** — "share buybacks and repurchase activity" → {"buyback(s)": canonical, ...} 등 큐레이티드 8개 어휘군 전부 커버: buyback/자사주, revenue/매출/sales(top line), operating income/영업이익, eps/earnings per share, R&D/research and development, total debt/debt load, repurchase program. + 별칭이 없는 텍스트 → {}. + 모호 다중 히트 → 둘 다 반환.
- [ ] **Step 2: 실패 확인 → Step 3: 구현** — YAML 각 metric 항목에 `aliases:` 리스트 추가(사전 편집; 매핑은 실제 사전의 canonical id에 맞춤 — 실행 시 `ontology/schema/metric_dictionary.yaml`의 실제 스키마 확인 후 반영). `resolve_alias_terms`는 로드 시 별칭→canonical 역인덱스 구축(정렬된 순회로 결정론).
- [ ] **Step 4: 통과 확인 + 기존 사전 테스트 회귀 없음 확인** — **Step 5: 커밋** `feat(metric-dictionary): static alias table + resolve_alias_terms`

### Task 2: 별칭 확장 검색 채널 (스토어)

**Files:**
- Modify: `src/krw_ontology/agent_index/store.py` — `query_planned_compact_with_diagnostics` (:1639 부근)의 절 처리 경로
- Test: `tests/unit/test_store_alias_expansion.py` (합성 미니 릴리스 레시피 재사용: tests/unit/test_shard_schema_v3.py:38-143)

**Interfaces:**
- Consumes: Task 1 `resolve_alias_terms`; 기존 `_query_metrics` (:4430)와 metric_scope 처리(:4504)
- Produces: 절에 `metrics`가 비어 있고 retrieval_query의 별칭이 ≥1 canonical로 매핑될 때 — 매핑된 canonical으로 `_query_metrics`를 절 티커/기간에 대해 추가 실행, 결과를 해당 절의 유닛으로 병합. 유닛 `match_mode="alias_expanded"`, `search_diagnostics`에 `alias_expansions: [{clause_id, alias, canonical_metric, added_units}]` 기록. `metrics`가 있는 절은 변화 없음(기존 채널이 이미 처리).

- [ ] **Step 1: 실패 테스트** — 미니 샤드에 metric 객체(revenue 계열)를 만들고, `metrics` 없이 retrieval_query="share buyback activity"…(샤드에 존재하는 canonical의 별칭)인 절로 쿼리 → 해당 metric 유닛이 반환됨 + match_mode 라벨 + 진단 항목 존재. 반대로 별칭 없는 절 → 기존 동작(진단 항목 없음). metrics 있는 절 → 진단 항목 없음(이중 발동 금지).
- [ ] **Step 2: 실패 확인 → Step 3: 구현** — 절 어댑터(tools.py `_execute_search_plan`→store 진입)에서 절 정의를 읽어 별칭 발동 조건 판정. 병합 순서: 기존 FTS 결과 우선, 별칭 유닛은 중복(object_id) 제거 후 추가. 상한: 절당 추가 유닛 ≤ 기존 limit과 동일 예산.
- [ ] **Step 4: eval_gold 전체 + store 관련 기존 테스트 회귀 확인** — **Step 5: 커밋** `feat(store): alias-expanded metric channel for metric-less clauses`

### Task 3: 퓨전 기간 형평 (요청 기간별 metric 예약)

**Files:**
- Modify: `src/krw_ontology/agent_index/store.py` — 절 결과 조립/윈도우 컷 부분(Task 6 진단에서 12-유닛 퓨전 창이라 지목된 경로; 실행 시 `query_planned_compact_with_diagnostics`의 결과 컷 로직 실측 위치 확인)
- Test: `tests/unit/test_store_period_equity.py`

**Interfaces:**
- Produces: 절이 `periods=[P1,P2]`를 명시했고 P1·P2 각각에 metric 관측 유닛이 존재함에도 한 기간이 상위 컷에서 밀려나는 경우 — **각 요청 기간별로 metric 유닛 최소 2슬롯을 예약**하는 결정론적 할당(예약 후 남은 슬롯을 기존 rank 순으로 채움). metric 유닛이 없는 기간은 예약 없음. 진단에 `period_reservations: [{period, reserved}]` 기록.

- [ ] **Step 1: 실패 테스트** — 미니 샤드에 두 기간 metric 객체 + 다수 정성 객체를 넣고 limit을 좁게 주어, 기존 코드가 두 번째 기간 metric을 밀어내는 상황을 재현 → 예약 후 두 기간 모두 metric 유닛 포함. 단일 기간 절 → 기존 동작 보존.
- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 회귀 확인** — **Step 5: 커밋** `feat(store): per-period metric reservation in planned query fusion`

### Task 4: 후보 게이트 실행 + 문서화

**Files:**
- Create: `benchmarks/reports/evidence_gold_candidate_2a_20260908.{json,md}` (산출물)
- Modify: `benchmarks/README.md` (후보 행 추가)

- [ ] **Step 1: 후보 실행** — `KRW_ONTOLOGY_RELEASE_ROOT=<release> uv run python scripts/benchmark_evidence_gold.py --gold benchmarks/evidence_gold_v1.json --label candidate-2a --baseline benchmarks/reports/evidence_gold_baseline_v1_1_20260908.json` → 게이트 exit 0 (열화 없음) 기대, 성층별 개선 기대: vocabulary_mismatch 0.125 → 상승, multi_period 0.25 → 상승.
- [ ] **Step 2: 잔여 갭 기록** — 개선 후에도 vocab < 0.7이면 Plan 2b(sqlite-vec 덴스 레인) 착수 근거로 수치 기록; ≥ 0.7이면 2b 보류 권고 기록.
- [ ] **Step 3: README 표 갱신 + 커밋** `test(eval-gold): candidate 2a gate run (alias expansion + period equity)`
- [ ] **Step 4: 전체 스모크** — eval_gold 41+신규 녹색, 기존 10개 선재 실패 외 무새 실패 확인.

---

## Task 5~8 (2a 확장 — Task 4 측정에 근거한 추가, 2026-09-08 결정)

Task 4 결과(overall 0.8832→0.8859, vocab 0.125→0.375, multi_period 불변 0.25)와 잔여 분석에 따라, 기계적 규칙("vocab<0.7 → 2b 착수") 대신 **잔여의 실체가 결정론 수정 가능/골드 의미론 문제**이므로 이를 먼저 닫는다: (i) 단일 기간 metric-less 절의 별칭 굶주림 3케이스(wmt/amzn/aapl), (ii) 별칭 메트릭의 파딩 버킷 불일치 1케이스(msft), (iii) multi_period 잔여 = 앵커 근원 아티팩트(검색은 최신 파딩의 비교 행을 반환 — 유효 근거 — 골드는 연도별 파딩 객체 id만 인정). 2b(sqlite-vec)는 이 후의 진짜 잔여에 대해 결정.

### Task 5: 단일 기간 절의 별칭 메트릭 하한 보장
**Files:** Modify `src/krw_ontology/agent_index/store.py` (예약 트리거 일반화), Test `tests/unit/test_store_period_equity.py` 확장 또는 `test_store_alias_expansion.py`
**의미:** metric-less 절에서 별칭 채널이 발동했고 strict FTS 창이 가득 차면, 최하위 랭크 행을 밀어내 최대 2개의 별칭 메트릭 유닛을 보장 삽입(≥2 기간 트리거의 별칭 경로 일반화). 진단 `alias_floor_applied: true` 기록. 단일 기간·무별칭·metrics 있는 절은 기존과 동일(회귀 가드).
**커밋:** `feat(store): alias metric floor for full-window metric-less clauses`

### Task 6: 별칭/메트릭 채널의 파딩 버킷 정렬
**Files:** Modify `src/krw_ontology/agent_index/store.py` (별칭/메트릭 행 정렬), Test `tests/unit/test_store_alias_expansion.py` 확장
**의미:** 절이 periods를 명시하고 메트릭 행이 여러 파딩 버킷에 존재하면, 요청(관측) 기간과 파딩 기간이 일치하는 행을 우선(동일 기간 내 기존 랭크 유지), 비교 행은 후순. 결정론 정렬 규칙만으로 msft_bottom_line 유형 해결.
**커밋:** `feat(store): filing-bucket alignment for metric-channel rows`

### Task 7: multi_period 골드 근원 보강 (큐레이션 결정)
**Files:** Modify `benchmarks/evidence_gold_curated_v1.json` (+ `benchmarks/evidence_gold_v1.json` 재병합), Test `tests/unit/test_eval_gold_curated.py`
**결정 내용(근거 기록):** 각 역사 연도 expected 항목의 `object_ids`에 **최신 파딩의 비교 행 객체 id를 대안 앵커로 추가**(any-of). 비교 행은 해당 연도 값의 정당한(재작성 반영) 근거이며, 1차 앵커(연도 소속 파딩)는 그대로 유지. notes에 근거 명시. 골드 형식/그레이더 불변 — 측정 체계는 그대로 두고 앵커 집합만 근거 실태에 맞게 보강하는 것(테스트에 맞추기가 아니라, 애초에 둘 다 정답인 근거를 하나만 인정하던 편향 수정).
**커밋:** `test(eval-gold): multi-period anchors accept comparative-row provenance`

### Task 8: 재게이트 + 2b 최종 결정
후보 라벨 `candidate-2a-plus`, baseline v1.1. 성층별 개선 기록 후 **진짜 잔여**로 2b 결정: vocab+multi_period 모두 ≥0.7이면 2b 보류 권고, 미달이면 sqlite-vec 레인 설계 착수(별도 Plan 2b 문서). README 표 갱신 + 커밋 `test(eval-gold): candidate 2a+ gate run`.

## Self-Review

- 스펙 커버: 베이스라인 격차 2개(vocab·multi_period) + 측정 아티팩트(Task 0) + 게이트 입증(Task 4) = 2a 전체. 2b/2c는 로드맵에 조건부로 명시.
- 플레이스홀더: Task 2·3의 store.py 정확 삽입 위치는 실행 시 실측 확인 지시(Plan 1에서 검증된 패턴). 인터페이스·테스트 코드 의도는 구체 명시.
- 타입 일치: `resolve_alias_terms(text)->dict[str,str]`, `match_mode="alias_expanded"`, `period_reservations` 진단 키 전 태스크 일관.
