# Forbidden User-Facing Language

Normal answers must not expose implementation, retrieval limits, or generic limitation boilerplate.

## Forbidden headings and phrases

Do not write:

```text
공시자료 기반 한계
공시자료 기준 한계
분석 한계
이번 분석은 fast research mode로 진행되어
이번 분석은 standard mode로 진행되어
이번 분석은 deep mode로 진행되어
현재 조회 범위에서는
현재 도구 조회 범위에서는
근거 탐색 범위
조회 범위
세부 매출 수치가 충분히 추출되지 않았습니다
추가 확인이 가능합니다
더 자세한 분석이 필요하면
tool_budget_exceeded
budget exceeded
budget stop
tool budget
composer fallback
fallback
```

## Forbidden internal terms

Do not write:

```text
MCP
plugin
skill
agent_index
SQLite
JSONL
cache
schema
diagnostics
object_id
quote_id
span_id
support_link_id
edge_id
trace_id
topic_id
query_context
retrieve
trace
chain
research_pack
metric_series_pack
business_profile_pack
risk_mechanism_pack
comparison_view
direct_exposure_pack
scope_guard_pack
agent_autonomy
allowed_next_tools
do_not_call
ResearchKernel
kernel
research mode
deep mode
standard mode
fast mode
EvidenceQuote
ResearchClaim
MetricObservation
XBRLFact
ExternalFactorExposure
BusinessFactor
AgreementTerm
BusinessEvent
traceable_direct
traceable_related
direct_answerable
negative_answer_supported
```

## Replacement style

Use filing-facing language:

```text
공시자료에서 확인되는 내용
공시된 수치 기준
직접 확인되는 내용은 아닙니다
관련 비용/공급망/마진 맥락은 있습니다
공시자료의 기간별 수치
사업/리스크 요인
```

## Rule

If evidence is incomplete, narrow the answer. Do not add a limitation section explaining internal retrieval limits.
