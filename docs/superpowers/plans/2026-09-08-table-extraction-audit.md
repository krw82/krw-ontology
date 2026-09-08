# 테이블 구간 추출 감사 (XBRL 교차검증) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 샤드에 적재된 수치 추출물(재무제표·세그먼트 표 중심)의 **정밀도를 SEC XBRL 회사팩트와 교차검증**해 정량 감사 리포트를 만들고, 표 밀집 구간의 약점을 섹션 품질 통계로 식별한다. 감사(측정)가 목적 — 파서 교체는 측정 결과에 근거해 별도 계획.

**Architecture:** (1) 감사 스크립트가 샤드에서 (티커, 정식 지표, 기간, 값) 표본을 뽑고, SEC `companyfacts` API(캐시됨, UA 헤더 필수)에서 동일 지표·기간의 XBRL 팩트를 가져와 값·단위·기간 일치 여부를 결정론적으로 판정; (2) 전 샤드 `documents.section_quality` 통계로 표 밀집 섹션 약점 순위; (3) 통합 리포트 + 로드맵 반영.

**Tech Stack:** Python 3.12 (uv), stdlib sqlite3/json, httpx(이미 의존), SEC EDGAR companyfacts API(공개), 캐시 디스크(gitignored).

## Global Constraints

- Plan 1·2 제약 승계 (uv run pytest, Edit/Write 도구만, credential 리터럴 금지 — **SEC 요구 User-Agent는 식별 문자열이지 자격증명 아님: `KRW Audit <owner-email-or-placeholder>` 형식을 환경변수 `SEC_AUDIT_UA`에서 읽되 기본값은 비밀 아닌 식별자로**, `git add -A` 금지).
- 브랜치: `feat/table-extraction-audit` (feat/similarity-proposal-lane HEAD 8a29181에서 생성).
- 릴리스 20260830_193811 읽기 전용; XBRL 캐시는 `~/krw-ontology-data/cache/sec-companyfacts/` (릴리스 트리 밖, gitignored).
- 결정론: 값 비교는 순수 로직(단위 환산 규칙 명시, 허용오차 명시). 네트워크는 캐시 적중 시 0회.
- 감사 결과는 커밋된 리포트(JSON+MD)로. 골드/그레이더/검색 코드 불변.

---

### Task 1: XBRL 교차검증 감사 스크립트

**Files:**
- Create: `src/krw_ontology/eval_gold/table_audit.py`
- Create: `scripts/audit_table_extraction.py` (typer 래퍼)
- Test: `tests/unit/test_table_audit.py`

**Interfaces:**
- Produces: `fetch_companyfacts(ticker, *, cache_dir, ua) -> dict` (캐시 우선 httpx GET), `xbrl_lookup(facts, canonical_metric, fiscal_year) -> list[XbrlFact]` (us-gaap 태그 매핑 테이블 `XBRL_TAG_MAP` — 사전 canonical → us-gaap 태그 후보, 정적), `compare_value(shard_value, xbrl_value, unit) -> "match"|"tolerance"|"mismatch"|"missing"` (단위 환산: shares M/B, currency USD만 1차), `audit_shard_metrics(release_root, *, tickers, seed, per_ticker, cache_dir, ua) -> AuditReport`.

- [ ] Step 1: 실패 테스트 — 합성 facts dict로 `xbrl_lookup`/`compare_value` 단위 테스트(환산 1.5B↔1500M, tolerance ±1% mismatch 케이스, missing). `fetch_companyfacts`는 캐시 파일 히트 경로만 테스트(네트워크 모의 불필요 — 캐시 미스 시 스킵 표시).
- [ ] Step 2~5: 구현 → 녹색 → 커밋 `feat(table-audit): XBRL cross-validation core (lookup, unit conversion, compare)`

### Task 2: 섹션 품질 통계 + 통합 실행

**Files:**
- Modify: `src/krw_ontology/eval_gold/table_audit.py` (section_quality 스캔 추가)
- Test: `tests/unit/test_table_audit.py` 확장

- [ ] Step 1: `section_quality_stats(release_root) -> dict` — 전 샤드 `documents.section_quality_json` 집계: 섹션명×fail/warn 비율, 표 밀집 키워드(financial statements, segment, supplementary) 상위 약점 순위. 미니 릴리스 테스트.
- [ ] Step 2~5: 구현 → 커밋 `feat(table-audit): section-quality weak-spot statistics`

### Task 3: 실측 실행 + 리포트 + 로드맵 반영

- [ ] Step 1: 실행 — 표본 30티커×지표 (seed 고정): `uv run python scripts/audit_table_extraction.py --release-root <release> --tickers 30 --out benchmarks/reports/table_audit_20260908.{json,md}` (network ON, 캐시 디렉토리 환경변수). 매치율·허용오차 내·불일치·XBRL 없음 분포 + 섹션 약점 표 리포트.
- [ ] Step 2: 해석 기록 — 불일치 상위 유형(값 불일치 vs 기간 불일치 vs 단위 불일치 vs 태그 매핑 없음) 분류. T2-RAGBench의 "오류 73%=테이블" 명제와 우리 실측 대비.
- [ ] Step 3: 커밋 `test(table-audit): committed audit report (30-ticker XBRL cross-validation)` + README 섹션 추가.

## Self-Review

- 스펙 커버: 감사(값 교차검증+섹션 통계)·리포트·커밋 = 로드맵 Plan 3 전체. 파서 개선은 측정 후 별도.
- 플레이스홀더 없음: 인터페이스·판정 규칙 명시. XBRL_TAG_MAP 초기 범위 = 사전 6 지표(revenue/operating_income/net_income/eps/R&D/total_debt) — 확장은 측정 후.
