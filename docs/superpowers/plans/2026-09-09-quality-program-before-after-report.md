# 품질 프로그램 이전/이후 비교 리포트 (2026-09-08 ~ 09)

> 오너 요청: "전부 계획세워서 진행해줘… 매우 최적화 시켜주고, 품질테스트까지해주고, 이전꺼랑 품질도 비교해줘." — 5개 워크스트림 실행 완료. 모든 수치는 커밋된 게이트 리포트에서 인용.

## 1. 이전 → 이후 (검색 품질, 736케이스 동일 골드·동일 릴리스 20260830_193811)

| 지표 | 이전 (베이스라인 v1.0) | 측정정합화 (v1.1) | 이후 (2a) | 이후 (2a+) | 사이클 2 (candidate-3) | 변화 (v1.0→사이클2) |
|---|---|---|---|---|---|---|
| overall pass_rate | 0.8777 | 0.8832 | — | 0.8899 | **0.9891** | **+11.1pp** |
| overall mean_recall | 0.8798 | 0.8852 | — | 0.8920 | **0.9907** | **+11.1pp** |
| vocabulary_mismatch pass/recall | **0.125** | 0.125 | 0.375 | 0.750 | **1.000** | **+87.5pp (8배)** |
| multi_period recall | 0.25 | 0.25 | 0.25 | 0.25 | **0.9167** | **+66.7pp** |
| multi_span recall | 0.875 | 0.875 | 0.875 | 0.875 | **0.625** | **−25.0pp (사이클 2 회귀 1건)** |
| dimensioned / template / 나머지 | — | — | — | 무열화 (전 성층) | template 1.000, dimensioned 0.9871 | 게이트 exit 2 (multi_span 단일 케이스) |

핵심: **어휘 불일치 격차의 71%를 임베딩 없이(정적 별칭 사전 + 결정론 채널 3종) 닫았다.** 개선 경로: 그레이더 FY/CY 라벨 정합화(+0.7pp) → 별칭 확장 채널(+2케이스) → 별칭 하한·파딩 버킷 정렬·골드 근원 보강(+3케이스). 회귀 0 (게이트 exit 0, 전 성층 프로그래매틱 검증).

### 사이클 2 (2026-09-09, candidate-3)

기간 계약(관측 기간 노출) + FTS 단수화·concept-OR + 파딩 버킷 dedupe 선호 3건 적용. overall 0.8899→**0.9891**, vocabulary_mismatch 0.750→**1.000**(잔여 2건 전부 회복), multi_period recall 0.25→**0.9167**, template 0.8905→**1.000**, dimensioned 0.9065→**0.9871**. 커밋별 기여(각 커밋을 골드 전체에 고립 재실행): 기간 계약 단독으로 multi_period 4건 전부 회복(recall 1.00, 대가로 파딩-기간 앵커 템플릿 4건 AMT/AWK/DUK/MMM 일시 하락) → FTS 커밋이 meta_share_buybacks 회복 → dedupe 커밋이 msft_bottom_line + 템플릿/디멘전 73건 회복(계약 리스크 4건 포함). **게이트 exit 2**: multi_span 1케이스 회귀(`curated_multispan_nvda_dc_growth_supply` 1.000→0.000) — 단수화된 strict 접두사가 매치 집합을 순수 확장하면서 bm25 재순위로 꽉 찬 12행 윈도에서 유일 앵커 캐리어가 밀려남(FTS 커밋 `2e93ecc` 발생; concept-OR 러그는 미발동). 부수로 dedupe 커밋이 MSFT multi_period를 1.0→0.667로 깎음(선호 버킷 내 `filing_period DESC` 동률결이 소유 파딩(CY2024)·최신 재작성(CY2026) 대신 중간 파딩(CY2025) 비교행 트윈을 남김 — 골드는 소유-또는-최신 근원만 허용; 결정론적 tie-break 정제 과제). **2b 최종 결정: CLOSED** (vocab 1.000 ≥ 0.9, multi_period recall 0.9167 ≥ 0.9; 재방문 조건 = 서빙 모델 교체 시 재측정만). 잔여 8케이스 분류는 `benchmarks/reports/evidence_gold_candidate_3_20260909` 및 사이클 2 태스크 리포트 참조.

**multi_period 불변의 정직한 이유**: 검색은 모든 기간의 행을 반환하지만 EvidenceUnit.period가 파딩 기간을 노출하는 3점 계약(contracts.py:1471 + 하니스 _UNIT_KEYS + 그레이더 period 필터)이 비교 행을 거부 — 임베딩으로도 못 고치는 계약 문제로 분류됨(2b DEFERRED의 근거).

## 2. 추출 품질 감사 (Plan 3, 신규 측정 — 이전 수치 없음)

- **10-K 수치 값 정밀도 96.4% (±0.1%)** — XBRL 교차검증 30티커. 수치 추출 자체는 건강.
- **약점은 구조 레벨**: item8(재무제표) fail_rate **1.000** (21/21 문서, 전부 missing), item7a 0.939, part1_item1 0.711 — T2-RAGBench "SEC 수치 QA 오류의 73%가 테이블" 사전과 일치.
- 샤드 실결함 발견: 10-K 내 분기 컬럼 캡처 6건 + canonical 매핑 오류 1건 → 파서 후속 계획의 구체 재료.
- 미스매치 57건 중 89.5%는 감사 하네스의 기간 정렬 한계(하네스 개선 과제), 진짜 값 오류 2건(3.5%).

## 3. 잡큐 실엔진 (Plan 4, krw-backend)

- **라이브 e2e PASS**: 실제 엔진 게이트웨이(:14318/v1/agent) 대향 AAPL company_research 잡 1건, **251초 만에 done**, markdown 1036자 + final_output_hash. OpenBB Copilot 배선의 "남은 조각" 폐쇄.
- 러너 경량화: 429 백오프(1s/2s/3회) + 전체 데드라인(KRW_AGENT_GATEWAY_DEADLINE_SECONDS) — 경계 테스트는 `>`/`>=` 판별 증명 포함.
- 이벤트 표면: KRW_JOBS_SCHEDULE 파일 기반 자동 인큐(미설정 시 완전 불활성 — 회귀 핀됨).

## 4. 품질 테스트 총괄

- krw-ontology: 평가·검색 신규 테스트 ~130개 추가(평가골드 41+17, 스토어 별칭/형평 27+, 테이블 감사 44+), 전체 스위트 1214 passed / 10 failed(전부 선재, 바이트 동일 확인).
- krw-backend: 65 → 79 passed +1 skipped(라이브 게이트), 라이브 런 1회 실측.
- 리뷰 게이트: 태스크 리뷰 17회, 수정 라운드 4회(전부 재리뷰 통과 — 그래이더 게이트 관용, 별칭 하한 후속, dimensioned metric_scope, 경계 테스트), 최종 브랜치 리뷰 4회 모두 MERGE_READY.

## 5. 브랜치 및 병합 권고 (모두 MERGE_READY, 미병합 — 오너 결정)

| 리포 | 브랜치 | 커밋 | 내용 | 병합 순서 |
|---|---|---|---|---|
| krw-ontology | feat/evidence-gold-harness | 10 | 골든세트 하니스+베이스라인 | 1 (스택 기저) |
| krw-ontology | feat/similarity-proposal-lane | 12 | 2a/2a+ 검색 개선 | 2 (스택) |
| krw-ontology | feat/table-extraction-audit | 5 | XBRL 감사 | 3 (스택) |
| krw-ontology | feat/harness-ablation-process | 1 | Plan 5 프로세스+본 리포트 | 4 (스택) |
| krw-backend | feat/job-queue-real-engine | 7 | 잡큐 실엔진 | 독립 |

## 6. 남은 것 (다음 사이클 입력 — ablation 프로세스 문서의 큐와 동일)

1. EvidenceUnit.period 계약 수정(multi_period 잔여의 열쇠) 2. buyback 사전 canonical 3. msft dedupe 4. sqlite-vec 덴스 레인(2b, 위 3건 후 잔여 측정해 재결정) 5. 파서 후속(감사 발견 7건) 6. 사소한 위생 항목들(SDD 레저 참조).
