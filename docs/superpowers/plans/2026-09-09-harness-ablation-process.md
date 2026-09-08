# 하니스 Ablation / Hill-Climbing 프로세스 (품질 프로그램 Plan 5)

> 상태: 프로세스 문서(그 자체가 산출물). 실행 계획 아님 — 분기 주기로 운영하는 절차 규정.

## 원칙 (외부 담론에서 채택, 2026-09-08 조사로 검증)

1. **모든 하니스 컴포넌트는 "모델이 못하는 것"에 대한 낡은 가정을 담는다** (Anthropic, 2025-12). 모델·프로바이더가 바뀔 때마다 컴포넌트는 다시 증명해야 한다.
2. **가장 가벼운 반증 메커니즘 먼저** (Datadog). ablation = 컴포넌트 하나 끄고 골드 게이트를 돌리는 것 — 새 도구가 아니라 기존 하니스의 `--label candidate-ablation-<comp> --baseline <regold>` 실행이다.
3. **측정이 판정한다**: "나아진 것 같다" 금지. evidence-gold 게이트(exit 2)가 유일한 판정.

## 재료 (이미 구축됨 — 신규 도구 불필요)

- 골든셋 736케이스 + 성층 + 게이트: `benchmarks/evidence_gold_v1.json`, `scripts/benchmark_evidence_gold.py`
- 성층별 베이스라인 체인: v1.0 → v1.1 → regold → 2a → 2a+ (`benchmarks/reports/`)
- 결함 입력 큐: 각 SDD 레저의 deferred-minors 목록 + 게이트 리포트의 성층 잔여 분류

## 절차 (분기 1회 + 모델/프로바이더 교체 시 즉시)

1. **인벤토리**: 검색 하니스 컴포넌트 열거 — 별칭 확장 채널, 기간 예약, 별칭 하한, 파딩 버킷 정렬, FTS 전략(strict/expanded/split/relaxed), 예산·리페어 상한(엔진 쪽은 krw-agnet 레저와 별도 사이클).
2. **개별 ablation**: 컴포넌트별로 끄는 브랜치에서 동일 골드 전체 게이트 실행, regold 베이스라인 대비.
3. **판정 규칙**: 컴포넌트 유지 조건 = (a) 표적 성층에서 ≥ 유의미 개선(당근 기준 1pp 이상) 유지, (b) 전 성층 무열화(exit 0). 둘 중 하나라도 실패 시 **제거**(코드 삭제 — 비활성화 아님; 구조 제거 원칙) 또는 조임.
4. **기록**: `benchmarks/reports/ablation_<date>_<comp>.{json,md}` + 본 문서 하단 로그 표에 한 줄.
5. **골드 신선도**: not_disclosed 케이스의 금지 단편은 릴리스 재바인딩 시 재검증(신규 공개 지표 위장 위험 — Plan 1 Task 5 우려 이관).

## 현재 결함 입력 큐 (2026-09-09 컴파일, 4개 SDD 레저에서)

**우선순위 상향 후보**: ① EvidenceUnit.period=파딩 기간 계약 수정(multi_period 0.25의 원인, 검색 아님 — contracts.py:1471 + 하니스 _UNIT_KEYS + 그레이더 필터의 3점 계약) ② buyback 계열 사전 canonical 부재(meta_share_buybacks 회복 조건) ③ msft value-identity dedupe(신규 파딩 트윈 우선 문제).
**하향(문서·테스트 위생)**: CLI traceback 정리 2건, exit-2 e2e 테스트 부재, matched_object_ids 비유일성, template_v1.json 중복, 디오프사이트 문서 오탈자들.
**측정 후보**: 별칭 채널 발동률 서빙 관측(bare canonical 발동 표면 — 진단 카운터 이미 존재).

## 로그

| 일자 | 컴포넌트 | 판정 | 근거 (게이트 리포트) |
|---|---|---|---|
| 2026-09-09 | (초기 — 첫 ablation은 첫 분기 또는 모델 교체 시) | — | — |
