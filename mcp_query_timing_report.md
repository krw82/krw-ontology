# KRW Ontology MCP 쿼리 타이밍 리포트

- 생성일시(로컬): 2026-05-23
- 대상 인덱스: `~/krw-ontology-data`
- 반복 횟수: 5회(공통), 지표는 성공 샘플 기준 median / p95
- timeout: 20초
- 공통 환경: uv + python 환경에서 `krw_ontology` 툴 함수 직접 호출

## 테스트한 쿼리/툴

1. `catalog_tool`
2. `index_context_tool`
3. `company_context_tool`
4. `query_context_tool`
5. `query_tool`(focused compact, `topic=cloud`, `tickers=["AAPL"]`, `document_types=["10-K"]`)
6. `query_tool`(broad full, `topic=market risk margin growth`, `tickers=["AAPL","AMZN","GOOGL"]`, `document_types=["10-K","10-Q"]`, `object_types=[ResearchClaim, BusinessFactor, ExternalFactorExposure, MetricObservation]`, `include_rejected=True`)
7. `retrieve_tool`(full, `question= "AAPL AI and cloud related margin trend and risks"`, `tickers=["AAPL"]`, `document_types=["10-K"]`)
8. `quality_tool`
9. `compare_tool`
10. `plan_query_tool`
11. `trace_tool`
12. `chain_tool`

## seed 객체(Trace/Chain)

- seed query: `topic="risk"`, `ticker="AAPL"`, `document_types=["10-K"]`
- 사용 객체 ID: `quote:AAPL:CY2022:10K:item1a_00:0072:003`

## 결과 요약 (ms)

| MCP | median(ms) | p95(ms) | 성공횟수/시도 | 결과 바이트 수 |
| --- | ---: | ---: | ---: | ---: |
| catalog_tool | 0.401 | 0.468 | 5/5 | 4,557 |
| index_context_tool | 11.103 | 12.764 | 5/5 | 3,127 |
| company_context_tool | 0.367 | 0.446 | 5/5 | 270 |
| query_context_tool | 0.528 | 8.012 | 5/5 | 20,167 |
| query_tool.focused_compact | 2.653 | 3.288 | 5/5 | 17,263 |
| query_tool.broad_full | 1321.222 | 1599.940 | 5/5 | 773,055 |
| retrieve_tool.full | 2378.411 | 2406.277 | 5/5 | 171,031 |
| quality_tool | 2.053 | 3.152 | 5/5 | 24,151 |
| compare_tool | 7.506 | 36.870 | 5/5 | 13,515 |
| plan_query_tool | 0.432 | 0.541 | 5/5 | 486 |
| trace_tool | 37.432 | 38.480 | 5/5 | 6,849 |
| chain_tool | 97.992 | 103.595 | 5/5 | 72,013 |

## 긴 쿼리(병목 후보)

1. `query_tool.broad_full` (p95 기준 1,599.94ms)
2. `retrieve_tool.full` (p95 기준 2,406.277ms)

## 판단/선별 결과

1. 장기 반복 모니터링에서 먼저 추적해야 할 후보는 `query_tool.broad_full`, `retrieve_tool.full`입니다.
2. `trace_tool`, `chain_tool`은 단일 객체 탐색 기준으로 수십 ms~백 ms 수준이며, 현재 테스트 범위에서 가장 큰 병목은 아님.
3. `index_context_tool`, `query_context_tool`, `compare_tool`, `quality_tool`은 안정적으로 1ms~10ms 구간에서 동작합니다.
4. `query_context_tool`은 첫 반복에서 8ms가 나올 정도의 초기 스파이크가 있었으므로 캐시/웜업 영향이 있으나 반복 성능은 1ms 내외로 수렴합니다.

## 추천 다음 액션

1. 동일 쿼리셋을 주기적으로 반복 실행해 분산(표준편차/변동성)까지 같이 추적하세요.
2. `query_tool.broad_full`은 `object_types`, `tickers`, `period` 조합을 쪼개서 실행했을 때 개선되는지 분해 실험하세요.
3. `retrieve_tool.full`은 먼저 `query_tool`로 좁혀서 ID를 정한 뒤 `trace_tool`/`chain_tool` 보완이 있는지 체크하면 응답시간 감소가 나는지 확인하세요.

## 무한반복 실행 예시(중단할 때까지)

```bash
while true; do
  echo "$(date '+%F %T') start" >> ~/krw-ontology/mcp_query_timing_report.md
  uv run python - <<'PY'
# 실행 스니펫은 위 설정을 동일하게 적용한 버전으로 교체해서 계속 append할 수 있습니다.
PY
  sleep 60
done
```

`Ctrl+C`로 중단 시점까지 계속 누적해서 운영할 수 있습니다.
