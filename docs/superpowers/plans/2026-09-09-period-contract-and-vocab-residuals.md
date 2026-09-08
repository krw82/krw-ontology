# Period 계약 + 어휘 잔여 (루프 사이클 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 다음 사이클 우선순위 잔여 3건 — ① MetricObservation 유닛의 `EvidenceUnit.period`를 관측 기간으로(파딩 기간→관측 기간 계약 수정, multi_period 0.25의 열쇠), ② FTS 힌트 용어 단수화 + concept-OR 러그(meta_share_buybacks 회복), ③ 값-동일성 중복 제거의 파딩 버킷 선호(msft_bottom_line 회복) — 를 결정론적으로 고치고 재게이트해 2b(sqlite-vec)를 진짜 잔여로 최종 결정한다.

**Architecture:** 전부 2026-09-09 정찰로 근본 원인이 file:line 수준으로 확정된 수정: (1) contracts.py:1473-1479의 의도적 파딩-우선 해석을 뒤집기(관측 기간은 이미 `row["period"]`로 존재 — store.py:2064가 bundle period를 관측 기간으로 덮어쓰는데 contracts가 다시 파딩으로 되돌림), (2) `_planned_query_terms`의 복수형 접두사 토큰(`buybacks*`→`buyback*`, 접두사가 복수형을 포함하므로 순수 일반화)과 전략 사다리의 concept-OR 러그(선언된 개념 내 OR — relaxed보다 보수적), (3) `_query_metrics` SQL 윈도의 `ORDER BY filing_period DESC`에 요청 파딩 버킷 선호 삽입.

**Tech Stack:** Python 3.12 (uv), pydantic, SQLite FTS5, pytest.

## Global Constraints

- Plan 1~4 제약 승계: `uv run pytest`, 소스 Edit/Write 도구만, credential 금지, `git add -A` 금지, pytest 요약 확인 후 커밋.
- 브랜치: `feat/period-contract-and-vocab-residuals` (feat/harness-ablation-process HEAD 9db5207에서 생성).
- **서빙 계약 변경 규율(Task 1)**: EvidenceUnit.period 의미 변경은 하위 호환 검증 후 — krw-agnet 소비처 grep을 Task 1 Step 0으로 선행(읽는 곳 발견 시 보고 후 진행). 문서 갱신 필수(skills query-context-contract.md KO/EN).
- 비교 기준: `benchmarks/reports/evidence_gold_baseline_v1_1_regold_20260908.json` (동일 골드·릴리스 체인).
- 결정론: LLM/임베딩/시간/난수 금지(검색·채점 경로).
- 릴리스 20260830_193811 읽기 전용.

---

### Task 1: MetricObservation 유닛 period = 관측 기간 (계약 수정)

**Files:**
- Modify: `src/krw_ontology/mcp_server/contracts.py:1473-1479` (`_evidence_candidate`의 period 해석)
- Modify: `tests/unit/test_mcp_v2_contract.py:1121-1171` (`test_metric_points_use_observation_period_without_losing_filing_lineage` — 현재 의미를 고착한 테스트, :1155 단언 뒤집기)
- Modify: `plugins/krw-ontology/skills/krw-ontology-research/references/query-context-contract.md` (+ `-en` 쌍둥이) — period 의미 문서화(문서 갭 메움)

**Interfaces:**
- Consumes: store.py:2059-2075가 이미 metric 행의 `period`를 관측 기간으로, `filing_period`를 별도로 제공.
- Produces: MetricObservation EvidenceUnit의 `period` == 관측 기간(FY 라벨; 그레이더의 FY/CY 정합화가 골드 CY 키와 매칭). 나머지 유닛 타입(claims/quotes/assumptions/MetricSeries 등)은 불변.

- [ ] **Step 0: 폭발 반경 검증** — krw-agnet 리포에서 EvidenceUnit period 소비처 grep: `grep -rn "evidence_units" ~/krw-ontology-v2/krw-agnet/{crates,services,packages} --include="*.rs" --include="*.ts" --include="*.py" | head -30` + period 필드 읽기 확인. 소비처가 의미적으로 파딩 기간을 기대하면 보고 후 진행 여부 기록.
- [ ] **Step 1: 실패 테스트** — :1155 단언을 `{"FY2025"}` 계열(관측 기간)로 뒤집고, filing lineage 보존 단언(metric_points + filing_period가 여전히 노출되는지 — `row["filing_period"]`/payload 경로) 추가.
- [ ] **Step 2~3: 구현** — contracts.py:1473-1479에서 MetricObservation 분기 제거(= `row_period` 사용; 관측 기간이 이미 row period). 파딩 기간은 기존 경로(source_label, metric lineage)로 여전히 추적 가능함을 테스트로 확인.
- [ ] **Step 4: 영향 테스트 전수** — `uv run pytest tests/unit -q -k "eval_gold or mcp_v2 or mcp_server or store"` 녹색; `_source_anchors` fallback 경로 테스트(:1692-1696 인접) 무파손 확인.
- [ ] **Step 5: 문서 갱신 + 커밋** `feat(contracts): MetricObservation evidence units expose the observation period`

### Task 2: FTS 힌트 용어 단수화 + concept-OR 러그

**Files:**
- Modify: `src/krw_ontology/agent_index/store.py` — `_planned_retrieval_terms`/`_planned_query_terms`(:7792-7820 부근, 토큰화 regex :64) 및 `_query_fts_with_strategy`(:3287 부근) 사다리
- Test: `tests/unit/test_store_alias_expansion.py` 또는 신규 `tests/unit/test_store_fts_terms.py`

**의미(정찰 확정 원인):** (a) 힌트 용어가 retrieval_query를 대체(store.py:7798-7803)하며 `buybacks*` 같은 복수형 접두사가 `buyback`(단수 토큰)을 놓침 — 접두사 매칭에서 단수화는 순수 일반화(`buyback*` ⊇ `buybacks*`). (b) 개념 토큰이 AND 결합만 되어 "share repurchases authorized" 증거(개념 중 하나만 충족)가 원천 배제 — 선언된 개념 내 OR(concept-OR)은 relaxed_or보다 보수적 러그.

- [ ] **Step 1: 실패 테스트** — (i) `_planned_query_terms("share buybacks")` → `["share*", "buyback*"]` 단수화 핀; 일반 단어(revenue→revenue,analysis→analysi* 방지: 's'로 끝되 단수화하면 어색한 ss/us/is/os 클래스는 제외 목록 또는 사전 기반 최소 규칙 — "buybacks→buyback, revenues→revenue" 규칙: 길이 ≥4, 끝 's', 앞이 자음군 아닌 단순 복수형만; 구현 시 실제 토큰화기 동작에 맞춤); (ii) concept-OR: 다중 개념 절에서 strict AND가 부분 커버일 때 `share* OR buyback*` 쿼리가 사다리에 추가되고 양쪽 앵커 유형이 회수됨(미니 샤드 fixture로).
- [ ] **Step 2~4: 구현 → 녹색 → 회귀 스윕**(eval_gold/store/mcp_v2 전부).
- [ ] **Step 5: 커밋** `feat(store): singularized FTS prefix terms + concept-OR strategy rung`

### Task 3: 값-동일성 중복제거의 요청 파딩 버킷 선호

**Files:**
- Modify: `src/krw_ontology/agent_index/store.py:4786-4797` (윈도 정의) + `_query_metrics` 호출부에 요청 기간 전달
- Test: `tests/unit/test_store_period_equity.py` 확장

**의미:** 중복 제거 키의 `ORDER BY filing_period DESC`가 항상 최신 파딩 트윈을 남김 — 절이 기간을 요청하면 그 기간에 해당하는 파딩 버킷 행을 우선(`ORDER BY (filing_period IN requested) DESC, filing_period DESC` 동등 결정론; requested는 절 periods의 CY/FY 정합화 키 집합). 요청 없으면 현행 유지.

- [ ] **Step 1: 실패 테스트** — 같은 값의 두 트윈(파딩 CY2025/CY2026, 관측 FY2025) + 절 periods=[CY2025] → CY2025 버킷 행 생존(객체 id로 단언); 요청 없으면 CY2026 유지.
- [ ] **Step 2~4: 구현 → 녹색 → 회귀 스윕. Step 5: 커밋** `fix(store): value-dedupe prefers requested filing bucket`

### Task 4: 재게이트(candidate-3) + 2b 최종 결정 + 문서 갱신

- [ ] **Step 1:** `KRW_ONTOLOGY_RELEASE_ROOT=<release> uv run python scripts/benchmark_evidence_gold.py --gold benchmarks/evidence_gold_v1.json --label candidate-3 --baseline benchmarks/reports/evidence_gold_baseline_v1_1_regold_20260908.json` → exit 0 필. 성층별 기대: multi_period 0.25→유의 상승, vocabulary_mismatch 0.75→상승(msft+meta 회복 시 1.0), 무열화.
- [ ] **Step 2:** 리포트를 `benchmarks/reports/evidence_gold_candidate_3_20260909.{json,md}`로. 잔여 분류 갱신.
- [ ] **Step 3:** 2b 결정 기록: vocab·multi_period 모두 ≥0.9이면 **2b 폐기(DEFERRED→CLOSED, 재방문 조건=모델 교체 시 재측정)**; 미달이면 잔여 명세와 함께 2b 설계 착수 근거 기록.
- [ ] **Step 4:** README 표 + 비교 리포트(2026-09-09-quality-program-before-after-report.md)에 사이클 2 행 추가. 커밋 `test(eval-gold): cycle-2 gate run (period contract + vocab residuals)`.

## Self-Review

- 스펙 커버: 잔여 큐 상위 3건 + 재게이트 + 2b 결정 + 문서. 위생 항목은 ablation 큐 유지(이 사이클 제외 — 과설계 방지).
- 위험 명시: Task 1은 서빙 계약 변경 — Step 0 선행 검증 + 문서 갱신 + 하위 호환 테스트 전수. Task 2 단수화 규칙은 순수 일반화임을 테스트로 핀(회귀 없음).
- 타입 일치: 변경 전부 기존 필드/함수 내부 — 신규 공개 인터페이스 없음(요청 기간 전달은 내부 파라미터).
