# krw-ontology-v2 품질 프로그램 마스터 로드맵 (2026-09-08)

> **For agentic workers:** 이 문서는 5개 워크스트림의 순서·의존성·완료 기준을 정의하는 마스터 로드맵이다. 개별 실행은 각 Plan 문서(TDD 태스크 단위)로 진행하고, Plan 문서 헤더의 REQUIRED SUB-SKILL 규칙을 따른다.

**목표:** 외부 담론+연구 조사(2026-09-08, 메모 `krw-related-work-sweep-2026-09-08`)에서 도출된 5개 보완점을 구현하고, 변경 전/후 품질을 골든세트 하니스로 정량 비교한다.

**원칙 (전 워크스트림 공통):**
- "유사도는 제안, 타입은 판정" — 유사도 결과는 별도 evidence tier(단서)로만, 수치 주장의 단독 근거 금지.
- 결정론 보존: 고정 모델 버전·버전 관리 인덱스·스키마 버전 명시적 범프.
- 오너 제약: 새 실패점 최소화, 메커니즘 재사용 우선 (비용 제약은 2026-09-08 해제됨).
- 절대 경로 보호: `~/krw-ontology-data/releases/prod`, 원본 리포 `~/krw-ontology` 불변. 빌드 출력은 `~/krw-ontology-data/releases/v2-dev`.

## 워크스트림 순서

| # | Plan | 리포 | 상태 | 산출물 |
|---|------|------|------|--------|
| 1 | 골든세트 평가 하니스 (evidence-gold) | krw-ontology | `2026-09-08-evidence-gold-harness.md` | 인용 레벨 골든셋 + 결정론 채점기 + **베이스라인 리포트 (= "이전 품질")** |
| 2 | 유사도 제안 레인 | krw-ontology | Plan 1 완료 후 작성 | 2a: 어휘 매핑(메트릭 사전 FTS/별칭) → 2b: sqlite-vec 덴스 폴백 도어(샤드 v4 범프) → 2c: 채굴→추출 투입 |
| 3 | 테이블 구간 추출 감사 | krw-ontology | Plan 1 완료 후 작성 | XBRL 교차검증 정밀도 지표 + 재무제표/세그먼트 구간 품질 리포트 |
| 4 | 잡큐 실엔진 | krw-backend (+krw-agent-service) | Plan 2~3과 병행 가능 | 이벤트 드리븐 루프 단계 (cron/webhook → 게이트웨이 런) |
| 5 | 하니스 ablation/hill-climbing | 전체 | Plan 1~4 완료 후 | 게이트/프롬프트/스킬 낡은 가정 제거 프로세스 + 도구 |

## 의존성

- Plan 2·3의 효과 측정은 Plan 1의 골든셋에 의존 → **Plan 1 선행 필수**.
- Plan 4는 독립 (krw-backend 잡큐 + 게이트웨이 runs API).
- 최종 비교 리포트: 베이스라인(Plan 1) vs Plan 2·3 적용 후 동일 골든셋 재측정.

## 완료 기준 (프로그램 전체)

1. `benchmarks/`에 evidence-gold v1 골든셋(템플릿 생성 + 큐레이티드 성층 포함)과 게이트 스크립트가 존재하고 단위·통합 테스트가 녹색.
2. 베이스라인 리포트가 커밋됨 (성층별 pass_rate / recall / zero_hit 비율 포함).
3. Plan 2 각 단계 적용 후 동일 골든셋 재측정 결과가 베이스라인 대비 열화 없음(게이트 exit 0) 또는 개선을 보임.
4. 이전/이후 비교 요약 문서 작성됨.
