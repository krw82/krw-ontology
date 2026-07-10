# KRW Ontology v3 최종 production 개발 기준서

기준일: 2026-06-12

이 문서는 KRW Ontology의 index build, immutable release, quality, MCP runtime,
production publish를 v3 최종형으로 전환하기 위한 개발 기준서다.

파일별 구현 매뉴얼은
[`v3-final-production-developer-manual.md`](v3-final-production-developer-manual.md)를
같이 본다. 상세 실행 개발 문서는
[`v3-final-production-implementation-runbook.md`](v3-final-production-implementation-runbook.md)를
같이 본다. 이 문서는 최종 결정과 완료 기준의 source of truth이고, 개발자 매뉴얼은
파일별 구현 지침, 구현 순서, 테스트와 review 기준을 담당한다. 상세 실행 문서는
운영 runbook, CLI 흐름, 실패 대응을 담당한다.

이 문서가 답해야 하는 질문은 다음과 같다.

1. 최종 production 구조는 무엇인가?
2. 왜 monolith를 production 기본에서 제거하는가?
3. 전체 ontology chain은 어떻게 유지하는가?
4. 새 ticker, ticker 수정, ontology schema 변경은 어떤 범위를 rebuild하는가?
5. `force`, cache, partial build, delta upload는 정확히 무엇을 의미하는가?
6. quality check와 repair plan은 어떤 release를 읽고 무엇을 수정하는가?
7. MCP는 왜 빨리 시작할 수 있고, 어떤 검증은 startup에서 하면 안 되는가?
8. dev release를 prod로 어떻게 안전하게 전환하고 rollback하는가?
9. 현재 branch에서 무엇이 구현되었고 무엇이 아직 남아 있는가?
10. 무엇을 통과해야 v3 전환 완료라고 부를 수 있는가?

이 문서는 임시 전환안, 1차 구현안, v1/v2 호환 계획을 설명하지 않는다.
최종 목표는 하나다.

```text
production serving = v3 global spine + company shards
```

## 0. 문서 권위와 사용법

### 0.1 문서 우선순위

문서가 서로 충돌하면 아래 순서로 판단한다.

1. 이 문서의 확정 결정과 완료 기준
2. [`v3-final-production-developer-manual.md`](v3-final-production-developer-manual.md)의 파일별 구현 지침과 review 기준
3. [`v3-final-production-implementation-runbook.md`](v3-final-production-implementation-runbook.md)의 개발 실행 순서와 운영 runbook
4. 실제 코드와 자동화 테스트
5. [`global-spine-company-shards-v3-development-guide.md`](global-spine-company-shards-v3-development-guide.md)의 schema 상세
6. [`v3-final-production-development-spec.md`](v3-final-production-development-spec.md)의 구현 명세
7. [`v3-production-implementation-handoff.md`](v3-production-implementation-handoff.md)의 파일별 handoff
8. [`v3-final-production-conversion-development-plan.md`](v3-final-production-conversion-development-plan.md)의 작업 순서
9. 기존 v1/v2 문서

문서별 책임은 다음과 같다.

| 문서 | 책임 | 사용 시점 |
| --- | --- | --- |
| 이 문서 | 최종 결정, 현재 상태, 완료 기준, 운영 계약 | 구현/리뷰/운영 전반 |
| `v3-final-production-developer-manual.md` | 파일별 개발 지침, 구현 순서, 테스트와 review 기준 | 실제 코드 수정과 PR 리뷰 |
| `v3-final-production-implementation-runbook.md` | 개발자가 바로 따라갈 작업 순서, CLI 흐름, 검증 matrix, review checklist | 실제 구현과 운영 실행 |
| `global-spine-company-shards-v3-development-guide.md` | SQLite table, manifest, build DAG, router의 상세 계약 | schema와 builder 구현 |
| `v3-final-production-development-spec.md` | component별 입력/출력과 알고리즘 | 코드 작성과 API 리뷰 |
| `v3-production-implementation-handoff.md` | 파일별 소유권과 이어서 할 작업 | 개발 인수인계 |
| `v3-final-production-conversion-development-plan.md` | 전환 작업 순서와 검증 matrix | 일정과 작업 분할 |
| 기존 v1/v2 문서 | 설계 이력과 폐기 이유 | 회귀 원인 조사만 |

기존 v1/v2 문서는 production normal path의 구현 근거로 사용하지 않는다.

실제 코드가 이 문서와 다르면 두 가지 중 하나다.

```text
문서가 잘못됨
  -> 문서를 고치고 이유를 기록한다.

코드가 최종 계약을 아직 만족하지 못함
  -> 코드를 미완료로 분류하고 완료처럼 문서화하지 않는다.
```

### 0.2 production의 의미

이 문서에서 production은 단순히 `prod` 환경만 의미하지 않는다.

```text
dev release
staging release
prod release
```

모두 같은 v3 manifest, index layout, verification, immutable release 계약을
사용해야 한다. 환경별 차이는 배포 대상과 운영 정책이지, 데이터 구조의 세대가
아니다.

### 0.3 상태 표기

| 상태 | 의미 |
| --- | --- |
| implemented | 코드와 targeted test가 존재함 |
| verified | 현재 branch에서 관련 테스트가 통과함 |
| partial | 핵심 경로는 있으나 final contract를 깨는 잔여 경로가 있음 |
| blocked | production 전환 전에 반드시 해결해야 함 |
| planned | 구현 범위와 완료 기준은 정해졌으나 코드가 없음 |

`implemented`는 production 완료를 뜻하지 않는다. 전체 definition of done을
통과해야 production complete다.

### 0.4 이 문서를 읽는 방법

개발자가 바로 이어서 작업할 때는 아래 순서로 읽는다.

1. `1. 최종 결정`에서 production target을 확인한다.
2. `3. 절대 불변식`에서 절대 깨면 안 되는 규칙을 확인한다.
3. `14. CLI 최종 계약`, `15. Quality 계약`, `16. MCP runtime 계약`에서
   사용자-facing surface를 확인한다.
4. `24. 현재 branch 구현 상태`에서 이미 끝난 것과 남은 것을 구분한다.
5. `25. 구현 작업 패키지`와 `30. 개발자 상세 부록`을 보면서 코드를 수정한다.
6. `28. Definition of done`으로 완료 여부를 판단한다.

운영자가 명령만 확인할 때는 `27. 운영 runbook`과 `29. 아주 쉬운 설명`만 봐도
된다. 다만 실제 prod 전환 전에는 반드시 `28. Definition of done`을 같이 확인한다.

## 1. 최종 결정

### 1.1 최종 데이터 흐름

```text
running-root
  -> source_manifest.json
  -> v3 build DAG
  -> immutable dev release candidate
  -> deep verification
  -> dev/current atomic promote
  -> optional quality check
  -> prod delta publish
  -> prod/current atomic activate
  -> MCP lightweight startup
  -> global spine routing + lazy company shard reads
```

### 1.2 production 기본 산출물

```text
manifest.json
source_manifest.json
indexes/global_spine.sqlite
indexes/shard_manifest.json
indexes/build_plan.json
indexes/build_summary.json
indexes/build_progress.jsonl
indexes/fragments/spine/<TICKER>.sqlite
indexes/companies/<TICKER>.sqlite
verify/*.json
logs/*.log
```

### 1.3 production 기본에서 제거할 것

```text
indexes/agent_index.sqlite
monolith-and-shards production layout
v1/v2 release serving compatibility
MCP startup deep verification
MCP startup PRAGMA integrity_check
MCP startup full file sha256
MCP runtime monolith fallback
quality scanner monolith dependency
prod publish v1/v2 acceptance
missing shard를 숨기는 filesystem fallback
```

### 1.4 운영자가 일상적으로 사용할 명령

```bash
uv run krw-ontology release plan
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology quality check
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

긴 경로 option은 복구, CI, debug에서만 필요해야 한다.

## 2. 왜 이 구조가 장기 최선인가

### 2.1 기존 monolith-and-shards의 문제

기존 구조는 전체 index와 회사별 shard를 동시에 보관한다.

```text
indexes/agent_index.sqlite
  전체 object, edge, evidence, quality, search data

indexes/companies/MSFT.sqlite
indexes/companies/NVDA.sqlite
...
  회사별 데이터
```

같은 데이터가 monolith와 shard에 중복된다.

주요 비용:

1. 저장공간이 중복된다.
2. 전체 build 때 거대한 monolith를 다시 써야 한다.
3. full integrity check와 full sha256이 대용량 파일 전체를 읽는다.
4. upload와 rollback bundle이 커진다.
5. monolith fallback이 남아 있으면 shard router correctness를 끝까지 증명하기 어렵다.
6. ticker 하나 수정에도 monolith 비용이 남는다.

### 2.2 v3의 역할 분리

```text
global_spine.sqlite
  전체 연결성
  global routing
  no-ticker discovery
  chain planning
  object/document locator
  compact search

indexes/companies/<TICKER>.sqlite
  full evidence
  quote
  payload
  trace detail
  company-local search
  quality events
```

전체 ontology chain을 없애는 것이 아니다.

```text
global spine = 전체를 연결하는 지도
company shard = 실제 근거를 보관하는 상세 저장소
```

질문은 global spine에서 경로와 대상 회사를 찾고, 필요한 shard에서만 세부 근거를
읽는다.

### 2.3 chain 강점 보존

사용자의 핵심 요구는 여러 회사, factor, topic, metric, entity가 한꺼번에 연결되는
chain이다. v3는 이를 아래 구조로 보존한다.

```text
global_object_locator
  object_id -> owning ticker/shard

global_edge_spine
  전역에서 필요한 관계 edge

global_factor_spine
global_topic_spine
global_metric_spine
global_entity_spine
global_counterparty_spine
  회사 간 공유 key와 route 후보

global_chain_index
  from ticker/object -> shared key -> to ticker/object
```

full evidence는 shard에 남지만, chain planning에 필요한 compact relation은
global spine에 남는다.

### 2.4 성능 개선의 성격

v3가 개선하는 비용은 다음과 같다.

| 작업 | v2 비용 | v3 목표 비용 |
| --- | --- | --- |
| ticker 하나 수정 | shard + full monolith | 해당 shard + fragment + compact merge |
| 새 ticker 추가 | full monolith 영향 | 새 shard + fragment + compact merge |
| warm no-change force | 큰 index 재생성 가능 | cache reuse + release materialization |
| MCP startup | 대형 DB scan 가능 | manifest + path + cheap SQLite metadata |
| prod publish | 큰 bundle | changed files 중심 delta |
| ticker query | monolith 또는 shard | 해당 shard direct |
| no-ticker query | broad monolith search | global spine 후보 선정 + limited fanout |

정확한 개선 비율은 실제 162 ticker 데이터 baseline으로 측정해야 한다. 문서에서
측정하지 않은 배수나 퍼센트를 약속하지 않는다.

## 3. 절대 불변식

아래는 개발 선호가 아니라 production 계약이다.

1. `current`는 immutable release를 가리키는 pointer다.
2. `current`가 가리키는 release 내부 파일은 수정하지 않는다.
3. build, verify, publish, health check 실패 시 기존 `current`를 유지하거나 되돌린다.
4. production release format은 `krw-ontology-release/v3`만 허용한다.
5. production index layout은 `global-spine-and-company-shards`만 허용한다.
6. `monolith_required`는 반드시 `false`다.
7. `force`는 새 release 생성을 강제한다.
8. cache read 우회는 `--no-cache`만 의미한다.
9. MCP startup은 lightweight verification만 한다.
10. deep verification은 release build 또는 publish preflight에서 한다.
11. quality check는 자동 prod gate가 아니다.
12. quality check는 immutable release를 read-only로 읽는다.
13. quality repair는 running-root/source만 수정한다.
14. repair 결과를 release에 반영하려면 새 release를 만든다.
15. required shard 누락은 즉시 실패한다.
16. manifest의 public path는 release root 내부 상대 경로다.
17. global spine에 full evidence text를 중복 저장하지 않는다.
18. prod publish는 runtime restart 전에 target release를 검증한다.
19. health가 target `release_id`와 다르면 성공으로 인정하지 않는다.
20. v1/v2를 정상 target으로 받아들이는 user-facing path를 남기지 않는다.

## 4. 디렉터리와 소유권

### 4.1 running-root

예:

```text
~/krw-ontology-data-running
```

역할:

```text
source artifact가 수정되는 mutable 작업 공간
pipeline과 quality repair가 수정하는 공간
release build의 입력
```

금지:

```text
runtime serving root로 직접 사용
prod/current 대신 사용
검증 없이 production에 노출
```

### 4.2 releases-root

예:

```text
~/krw-ontology-data/releases
```

구조:

```text
releases/
  dev/
    current -> 20260612_120000
    20260612_120000/
    failed/
    events/
    .index_fragment_cache/
  prod/
    current -> 20260612_130000
    20260612_130000/
    failed/
    events/
```

### 4.3 release root

release root는 immutable artifact다.

```text
<releases-root>/<env>/<release-id>/
```

release build 완료 후 허용되는 변경:

```text
원칙적으로 없음
```

운영 event log가 별도 env-owned path에 기록될 수 있지만, active release의
index와 manifest를 고치면 안 된다.

## 5. v3 release layout

```text
<release-root>/
  manifest.json
  source_manifest.json

  companies/
    <TICKER>/
      source artifacts copied or materialized for the release

  indexes/
    global_spine.sqlite
    shard_manifest.json
    build_plan.json
    build_summary.json
    build_progress.jsonl

    fragments/
      spine/
        <TICKER>.sqlite

    companies/
      <TICKER>.sqlite

  verify/
    release_verify.json
    consistency_report.json
    smoke_queries.json
    chain_smoke.json
    routing_report.json
    quality_report.json

  logs/
    release-build.log

```

`debug/monolith.sqlite` 같은 parity 조사 산출물은 정상 production bundle에 포함하지
않는다. 필요하면 release normal output 바깥에서 명시 debug 작업으로만 만든다.
정상 `manifest.json`의 `indexes`에도 `debug_monolith` entry를 넣지 않는다.

## 6. Public contract

### 6.1 manifest v3

최소 구조:

```json
{
  "format": "krw-ontology-release/v3",
  "release_id": "20260612_120000",
  "env": "dev",
  "status": "ready",
  "index_layout": "global-spine-and-company-shards",
  "monolith_required": false,
  "indexes": {
    "global_spine": {
      "path": "indexes/global_spine.sqlite",
      "required": true,
      "sha256": "<sha256>",
      "schema_version": "krw-ontology-global-spine/v2",
      "counts": {}
    },
    "company_shards": {
      "dir": "indexes/companies",
      "required": true,
      "count": 162,
      "tickers": {}
    },
    "shard_manifest": {
      "path": "indexes/shard_manifest.json",
      "required": true,
      "sha256": "<sha256>"
    }
  }
}
```

반드시 거절할 조건:

| 조건 | 이유 |
| --- | --- |
| format이 v3가 아님 | v1/v2 serving 차단 |
| status가 ready가 아님 | 미완성 release 차단 |
| index layout이 v3가 아님 | monolith layout 차단 |
| monolith_required가 false가 아님 | runtime fallback 차단 |
| release_id 없음 | activation 식별 불가 |
| required output 없음 | serving 불가 |
| absolute public path | portable release 위반 |
| path가 root 밖으로 escape | 보안 및 correctness 위반 |
| shard count/ticker set 불일치 | topology 불일치 |
| `indexes.debug_monolith.required == true` | production contract 위반 |

### 6.2 source manifest

`source_manifest.json`은 build input의 source of truth다.

역할:

1. build 대상 ticker와 artifact를 결정한다.
2. 각 artifact content hash를 기록한다.
3. 회사별 source hash를 계산한다.
4. cache key 입력을 제공한다.
5. rebuild/skip 이유를 설명한다.

금지:

```text
hidden cache directory를 source로 간주
failed/current release를 source로 섞기
filesystem 전체 scan 결과만으로 target을 암묵 결정
source hash 없이 cache hit 인정
```

### 6.3 shard manifest

`indexes/shard_manifest.json`은 ticker별 evidence store topology다.

각 entry가 가져야 할 정보:

```text
ticker
relative shard path
sha256
schema version
document count
object count
edge count
quality event count
```

manifest ticker set과 shard manifest ticker set은 같아야 한다.

### 6.4 build plan

`indexes/build_plan.json`은 실행 전 DAG 설명이다.

각 node가 가져야 할 정보:

```json
{
  "id": "company_shard:MSFT",
  "stage": "company_shard",
  "depends_on": ["source_manifest"],
  "status": "cached",
  "cache_hit": true,
  "cache_key": "<hash>",
  "rebuild_reason": null,
  "output": "indexes/companies/MSFT.sqlite"
}
```

### 6.5 build summary

`indexes/build_summary.json`은 실행 결과다.

최소 정보:

```text
source count
company count
company shard cache hits/misses
spine fragment cache hits/misses
global spine counts
worker count
duration
failure reason
```

### 6.6 verification reports

deep verification 결과는 `verify/` 아래에 남긴다.

이 보고서는 다음 단계의 trust input이다.

```text
local promote
prod publish preflight
rollback candidate verification
incident investigation
```

## 7. Global spine 상세

### 7.1 역할

global spine은 compact global index다.

저장해야 하는 것:

```text
object/document locator
global relation edge
topic/factor/metric/entity/counterparty routing key
cross-company chain 후보
key frequency와 generic penalty
compact FTS search text
```

저장하면 안 되는 것:

```text
full evidence payload
긴 quote 전체
company shard의 모든 JSON
company-local search index 전체 복제
```

### 7.2 필수 table

| table | 책임 |
| --- | --- |
| `metadata` | schema, builder, layout, created_at |
| `global_object_locator` | object_id를 shard와 local key로 연결 |
| `global_document_catalog` | document를 ticker, period, shard로 연결 |
| `global_edge_spine` | 전역 chain에 필요한 relation |
| `global_factor_spine` | factor key 기준 route |
| `global_topic_spine` | topic key 기준 route |
| `global_metric_spine` | normalized metric 기준 compare route |
| `global_entity_spine` | entity 기준 route |
| `global_counterparty_spine` | counterparty 관계 route |
| `global_chain_index` | cross-company chain 후보 |
| `global_key_stats` | key 빈도, idf, generic 판정 |
| `global_search_fts` | compact global discovery |

현재 schema constant:

```text
GLOBAL_SPINE_SCHEMA_VERSION = krw-ontology-global-spine/v2
GLOBAL_SPINE_BUILDER_VERSION = global-spine-builder/v2
GLOBAL_SPINE_LAYOUT = global-spine-and-company-shards
```

### 7.3 cross-company link 품질

공유 key가 있다고 무조건 강한 chain으로 취급하면 안 된다.

weight 계산에 고려할 것:

```text
shared key type
key rarity
confidence
evidence grade
materiality
recency
generic penalty
directionality
```

너무 일반적인 key는 `global_key_stats.generic`과 penalty로 약화해야 한다.

## 8. Company shard 상세

company shard는 해당 ticker의 full evidence store다.

주요 table:

```text
metadata
documents
objects
edges
quality_events
object_fts
object_search_text
object_text
object_traceability
metric_lookup
metric_dimension_lookup
company_dimension_catalog
exposure_lookup
agreement_lookup
event_lookup
factor_lookup
company_topic_index
company_topic_source_objects
company_topic_fts
```

현재 company shard schema는 기존 agent index schema primitive를 재사용한다.
최종 production 관점에서는 이 schema가 "monolith schema"가 아니라
"company shard evidence schema"로 소유권이 명확해져야 한다.

현재 관련 version:

```text
COMPANY_SHARD_SCHEMA_VERSION = krw-ontology-company-shard/v2
AGENT_INDEX_SCHEMA_VERSION = 1.0.0-alpha.3
```

최종 cleanup에서 user-facing naming과 내부 constant naming이 company shard 역할과
일치하는지 검토해야 한다.

## 9. Build DAG

### 9.1 node

| node | 입력 | 출력 | 병렬화 | cache |
| --- | --- | --- | --- | --- |
| SourceManifest | running-root artifacts | source_manifest | no | no |
| BuildPlan | source manifest, versions, cache | build_plan | no | no |
| CompanyShard:TICKER | ticker source | company shard | yes | yes |
| SpineFragment:TICKER | company shard | spine fragment | yes | yes |
| GlobalSpineMerge | all fragments | global spine | no | future conditional |
| CrossCompanyLinks | merged spine | chain rows | no | future conditional |
| ShardManifest | shard outputs | shard manifest | no | no |
| ReleaseManifest | verified paths/counts | manifest | no | no |
| DeepVerify | release outputs | verify reports | partially | no |
| PromoteCurrent | verified release | current symlink | no | no |

### 9.2 algorithm

```text
resolve configured roots and env
acquire env build lock
create immutable candidate directory
materialize source
write source manifest
calculate company source hashes
plan dirty/cached company shards
build dirty company shards in parallel
reuse valid cached company shards
build or reuse spine fragments
merge all fragments deterministically
generate cross-company links
write shard manifest and build summary
write v3 manifest
run deep verification
write verification reports
atomically promote current
release lock
```

### 9.3 parallelism

병렬화 가능한 부분:

```text
different company shard builds
different spine fragment projections
some shard-local verification
```

병렬화하면 안 되는 부분:

```text
current pointer swap
same env release promotion
same output SQLite writer
global deterministic merge finalization
```

병렬화는 CPU와 IO를 동시에 사용하므로 무조건 빨라지지 않는다. 대용량 SQLite build는
disk write bandwidth와 page cache가 병목일 수 있다. worker 수는 실측으로 정한다.

## 10. Cache와 partial rebuild

### 10.1 cache 종류

| cache | key 입력 | 산출물 |
| --- | --- | --- |
| company shard cache | company source hash, schema version, builder version | company shard |
| spine fragment cache | company shard cache key, projection version, builder version | spine fragment |

현재 cache format:

```text
krw-ontology-company-shard-cache/v3
krw-ontology-spine-fragment-cache/v3
```

### 10.2 cache hit 조건

파일 존재만으로 cache hit를 인정하지 않는다.

필수 조건:

```text
cache key 일치
파일 존재
SQLite open 가능
metadata 일치
schema version 일치
ticker 일치
필수 table 존재
```

cache가 깨졌다면 source에서 rebuild하고, 기존 valid cache를 성공 전에 삭제하지 않는다.

### 10.3 force와 no-cache

```text
release force
  새 immutable release 생성을 강제한다.
  valid cache는 사용할 수 있다.

release force --no-cache
  새 release를 만들고 모든 company shard와 fragment cache read를 우회한다.
```

일반 quality 수정 후에는 `release force`가 맞다.

`--no-cache`는 아래 경우에만 사용한다.

```text
cache 손상 의심
cache key 버그 조사
schema/version invalidation 검증
완전 cold build baseline 측정
```

## 11. 변경 유형별 rebuild 범위

### 11.1 source 변경 없음

```text
새 release id 생성
company shard cache hit
spine fragment cache hit
global spine deterministic merge
deep verification
promote
```

최종 최적화에서는 fragment set hash가 같을 때 global spine reuse도 가능하지만,
correctness와 deterministic metadata 계약을 먼저 정의해야 한다.

### 11.2 ticker 하나 수정

예: MSFT source 변경

```text
MSFT source hash 변경
MSFT company shard rebuild
MSFT spine fragment rebuild
다른 ticker shard/fragment cache hit
global spine merge
cross-company links refresh
deep verify
promote
```

### 11.3 새 ticker 추가

```text
source manifest에 새 ticker 등장
new_company dirty reason
새 company shard build
새 spine fragment build
global spine merge
새 ticker가 포함된 cross-company link 생성
verify
promote
```

사람이 기존 모든 ticker를 다시 맞출 필요는 없다.

### 11.4 ticker 제거

```text
source manifest에서 ticker 제거
새 release shard manifest에서 제외
global merge input에서 fragment 제외
removed ticker locator/edge/chain reference 검증
promote
```

이전 immutable release에는 해당 ticker가 남아 있어 rollback할 수 있다.

### 11.5 ontology source schema 변경

source artifact parser와 projection이 바뀌는 경우다.

가능한 범위:

```text
source schema가 모든 회사 artifact에 영향을 줌
  -> migration 또는 parser backward rejection 정책 결정
  -> 회사 source hash/version invalidation
  -> 전체 affected company shard rebuild

source schema가 optional field 추가이고 old source도 유효
  -> affected artifact만 rebuild 가능
```

v1/v2 serving compatibility를 제거한다는 결정과 source artifact migration은 다른
문제다. runtime release는 v3만 허용하되, source artifact migration이 필요하면
명시적인 migration job으로 처리한다.

### 11.6 company shard schema 변경

```text
COMPANY_SHARD_SCHEMA_VERSION bump
-> 모든 company shard cache invalid
-> 모든 company shard rebuild
-> 모든 spine fragment 재평가
-> global spine merge
```

### 11.7 spine projection 변경

```text
SPINE_PROJECTION_VERSION bump
-> company shard cache는 유지 가능
-> 모든 spine fragment rebuild
-> global spine rebuild
-> cross-company links rebuild
```

### 11.8 chain algorithm 변경

```text
chain index version bump
-> company shard 유지 가능
-> spine fragment 유지 가능할 수 있음
-> global_chain_index와 관련 stats 재생성
```

## 12. Immutable release lifecycle

### 12.1 candidate 상태

```text
creating
building
verifying
ready
failed/quarantined
current
```

### 12.2 성공

```text
candidate build
-> deep verify pass
-> verification report write
-> current.next symlink create
-> atomic replace current
-> activation event write
```

### 12.3 실패

```text
candidate build/verify failure
-> current unchanged
-> candidate failed/ quarantine
-> error and worker status recorded
```

### 12.4 rollback

rollback도 새 검증 없이 symlink만 과거로 돌리면 안 된다.

```text
target release 존재
target manifest v3
target startup preflight
target verification artifacts valid
atomic current switch
optional runtime reload
health target release_id 확인
```

## 13. Verification 분리

### 13.1 startup verification

목표:

```text
MCP가 빠르게 시작할 수 있는 최소 serveability 확인
```

허용:

```text
current symlink 확인
manifest read
v3 format/status/env/layout 확인
relative path/root containment 확인
global spine/shard manifest/company shard directory 존재 확인
global spine SQLite open
metadata schema/layout 확인
```

금지:

```text
PRAGMA integrity_check
전체 file sha256
모든 shard open
전체 count scan
smoke query
quality scan
ranking evaluation
```

### 13.2 deep verification

목표:

```text
release를 promote/publish하기 전에 전체 correctness를 증명
```

현재 release deep verifier가 반드시 수행하는 것:

```text
manifest format/status/env/layout
manifest path containment
global spine digest
shard manifest digest
global spine schema
shard manifest topology
required shard existence
각 company shard digest
각 shard_manifest quality_summary format
각 shard_manifest quality_summary 재계산 일치
object locator consistency
document catalog consistency
edge/chain endpoint consistency
release filesystem temp/broken symlink 검사
```

별도 integration/운영 검증으로 수행해야 하는 것:

```text
router query smoke
cross-company chain smoke
MCP tool parity smoke
quality check
performance baseline
prod delta reconstruction/activation/rollback rehearsal
```

release deep verification은 artifact integrity와 topology correctness의 trust root다.
router/quality/performance 검증을 release verifier 한 함수에 다시 합치지 않는다. 서로
실패 의미와 실행 비용이 다르기 때문이다.

deep verification은 느려도 된다. 단, runtime startup을 막아서는 안 된다.

### 13.3 standalone index verification

`krw-ontology index verify`는 mutable running-root 또는 build 작업 디렉터리의 v3
index 산출물을 검사한다. 이 위치에는 아직 canonical release `manifest.json`이 없을
수 있다.

따라서 계약을 분리한다.

| 검증 모드 | release manifest | digest 책임 |
| --- | --- | --- |
| `release verify` | 필수 | release manifest의 global spine, shard manifest, company shard digest를 모두 강제 |
| `index verify` | 선택 | shard manifest 내부 digest와 schema/topology를 검사하되, 존재하지 않는 release manifest digest는 요구하지 않음 |
| `release startup-check` | 필수 | digest 전체 계산 금지, serveability만 확인 |

이 분리는 검증 강도를 낮추기 위한 것이 아니다. 아직 release가 아닌 build output에
release 계약을 잘못 적용하지 않고, immutable release가 된 순간에는 digest를 반드시
강제하기 위한 것이다.

손상 SQLite는 verifier 밖으로 예외를 던져 CLI를 종료시키면 안 된다. verifier는
`ok=false`와 구조화된 error를 반환해야 하며, promote/publish는 그 결과를 보고
실패해야 한다.

## 14. CLI 최종 계약

### 14.1 최초 설정

```bash
cd ~/krw-ontology

uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

값 결정 우선순위:

```text
explicit CLI option
-> saved config
-> environment variable
-> safe default or clear failure
```

### 14.2 release plan

```bash
uv run krw-ontology release plan
```

하는 일:

```text
source를 읽음
cache hit/miss 계산
dirty ticker 계산
DAG 출력
release output은 쓰지 않음
```

확인할 것:

```text
dirty_companies
cached_companies
dirty_spine_fragments
worker count
cache root
no_op 여부
```

### 14.3 release force

```bash
uv run krw-ontology release force
```

기본은 background worker다.

```text
명령은 worker를 시작하고 반환
실제 build는 계속 실행
```

foreground debug:

```bash
uv run krw-ontology release force --foreground
```

cold rebuild:

```bash
uv run krw-ontology release force --no-cache
```

### 14.4 status와 watch

```bash
uv run krw-ontology release status
uv run krw-ontology release watch
```

`status`는 현재 pointer와 worker 상태를 요약해야 한다.

`status`는 latest candidate의 `indexes/build_progress.jsonl` 마지막 event도 함께
요약해야 한다.

`watch`는 worker log와 `build_progress.jsonl`을 같이 follow해야 한다. worker stdout
log가 조용한 긴 구간에서도 progress stream이 갱신되면 현재 stage, node, ticker,
count, cache hit, duration을 볼 수 있어야 한다.

### 14.5 verify

startup-safe:

```bash
uv run krw-ontology release startup-check --env dev
```

명시 root:

```bash
uv run krw-ontology release verify \
  --startup-check \
  --root ~/krw-ontology-data/releases/dev/current \
  --env dev \
  --require-current-symlink
```

deep verification은 candidate promote/publish 경로에서 실행한다.

### 14.6 prod publish

가장 단순한 dev to prod 명령:

```bash
uv run krw-ontology prod publish-dev
```

현재 이 명령은 configured `dev/current`를 선택하고 delta upload를 기본으로 사용한다.

generic publish:

```bash
uv run krw-ontology prod publish --from-env dev --delta
```

최종 CLI에서는 사용자 혼란을 줄이기 위해 normal path를 하나로 수렴해야 한다.
권장 normal path는 `prod publish-dev` 또는 더 짧은 단일 alias 중 하나다.

## 15. Quality 계약

### 15.1 quality check

```bash
uv run krw-ontology quality check
```

기본 대상:

```text
<publish-root>/<env>/current
```

읽는 것:

```text
manifest v3
global spine
shard manifest
company shards
quality events
```

하지 않는 것:

```text
source 수정
release 수정
index build
current promote
prod publish
자동 gate
```

### 15.2 quality consistency

검사 범위:

```text
manifest v3/layout/monolith flag
global spine 존재
shard manifest 존재
shard entry 존재
shard count 일치
object -> locator 일치
locator -> shard object 일치
document -> global catalog 일치
global edge endpoint locator 존재
global chain endpoint locator 존재
shard-local quality event 집계
```

### 15.3 quality gate

`quality gate` 명령은 operator가 명시적으로 실행할 수 있는 threshold 검사다.

```bash
uv run krw-ontology quality gate
```

중요:

```text
prod publish가 자동으로 quality gate를 실행하지 않는다.
사용자가 원할 때만 실행한다.
```

### 15.4 repair plan

```bash
uv run krw-ontology quality repair plan
```

plan은 특정 release의 quality 결과 snapshot이다.

fingerprint:

```text
release root
release id
release format
manifest sha256
source manifest sha256
global spine sha256
```

plan 실행 전 fingerprint가 달라지면 stale plan으로 거절한다.

명시 override:

```bash
uv run krw-ontology quality repair run --allow-stale-plan
```

이 option은 사고 조사나 의도된 override에서만 사용한다.

### 15.5 repair 후 release

```text
quality check에서 문제 발견
-> repair plan 생성/실행 또는 source 직접 수정
-> running-root 변경
-> current release는 그대로
-> release force
-> 새 current
-> quality check 재실행
```

다시 `force`가 필요한 이유는 current release가 immutable이기 때문이다.

## 16. MCP runtime 계약

### 16.1 startup sequence

```text
resolve release root
-> verify_release_startup_v3
-> export runtime v3 paths
-> configure HTTP server
-> open health/MCP port
-> open router/store lazily on request
```

runtime env:

```text
KRW_ONTOLOGY_ENV
KRW_ONTOLOGY_RELEASE_ROOT
KRW_ONTOLOGY_ROOT
KRW_ONTOLOGY_MANIFEST_PATH
KRW_ONTOLOGY_GLOBAL_SPINE_PATH
KRW_ONTOLOGY_SHARD_MANIFEST_PATH
KRW_ONTOLOGY_INDEX_LAYOUT
KRW_MCP_STORE_MODE
```

production runtime env에서 제거할 것:

```text
KRW_ONTOLOGY_INDEX_PATH
```

### 16.2 health contract

필수 핵심 필드:

```json
{
  "ok": true,
  "release_id": "20260612_120000",
  "env": "prod",
  "manifest_valid": true,
  "index_layout": "global-spine-and-company-shards",
  "global_spine_present": true,
  "company_shard_count": 162
}
```

runtime deploy는 HTTP 200만 확인하면 안 된다.

성공 조건:

```text
ok == true
release_id == target release id
env == expected env
manifest_valid == true
index_layout == v3 layout
global_spine_present == true
```

### 16.3 router

```text
ticker query
  -> direct company shard

no-ticker query
  -> global spine candidate ticker selection
  -> bounded shard fanout

trace
  -> global object locator
  -> owning shard

chain
  -> local shard chain
  -> global spine neighbors

compare
  -> normalized metric/topic/factor key
  -> selected shard fanout
```

required shard가 없으면 실패해야 한다. monolith fallback이나 broad filesystem scan으로
숨기면 안 된다.

## 17. krw-ontology-front runtime 계약

대상 repo:

```text
~/krw-ontology-front
```

### 17.1 production restart

안전한 순서:

```text
target prod/current v3 preflight
-> target release_id 추출
-> 기존 MCP stop/restart
-> health poll
-> target release_id/layout 확인
-> 실패 시 배포 실패 처리
```

invalid target이면 기존 healthy MCP를 먼저 내리면 안 된다.

### 17.2 dev runtime

dev runtime도 같은 v3 contract를 사용한다.

```text
dev/current v3 startup check
global spine/shard manifest env
no --index-path
health layout/release id 확인
```

### 17.3 docker worker

worker container health는 다음을 확인해야 한다.

```text
global_spine.sqlite 존재
shard_manifest.json 존재
plugin config 존재
worker process health
```

web container는 full SQLite data를 소유하지 않고 web catalog boundary를 유지한다.

## 18. Prod publish와 delta upload

### 18.1 publish preflight

local source가 다음을 만족해야 한다.

```text
v3 manifest
status ready
v3 layout
monolith_required false
global spine 존재
shard manifest 존재
company shard topology 일치
verification report 존재
```

### 18.2 delta 계산

```text
candidate file map 생성
remote current file map 조회
sha256가 다른 path = changed
remote에만 있는 path = removed
delta manifest 생성
changed file만 bundle
```

### 18.3 remote reconstruction

```text
remote current를 candidate tmp에 clone/copy
changed files 적용
removed files 삭제
candidate manifest v3 검증
changed file digest 검증
global spine cheap schema/metadata 검증
shard manifest digest 검증
각 company shard digest 검증
각 shard_manifest quality_summary 재계산 검증
global spine/shard topology 검증
atomic activate
runtime reload
health target release_id 확인
```

### 18.4 rollback 조건

다음 중 하나면 이전 prod/current로 복구한다.

```text
remote preflight 실패
reload 실패
health 요청 실패
health ok != true
health release_id mismatch
health index_layout mismatch
```

## 19. 관측성

### 19.1 release status

운영자가 알아야 하는 값:

```text
env
current release id
candidate release id
worker pid/state
phase
dirty/cached company count
dirty/cached fragment count
global spine output
latest error
log path
progress path
```

### 19.2 build progress event

최종 event 형식:

```json
{
  "format": "krw-ontology-v3-build-progress/v1",
  "release_id": "20260612_130000",
  "event": "node_status",
  "node_id": "company_shard:MSFT",
  "stage": "company_shard",
  "status": "rebuilt",
  "timestamp": "2026-06-12T04:00:00+00:00",
  "ticker": "MSFT",
  "cache_hit": false,
  "output": "indexes/companies/MSFT.sqlite",
  "duration_ms": 1234,
  "details": {
    "completed": 42,
    "total": 162,
    "artifact_count": 7,
    "cache_key": "<hash>"
  }
}
```

필수 node:

```text
build
source_manifest
build_plan
company_shards
company_shard:<TICKER>
spine_fragments
spine_fragment:<TICKER>
global_spine_merge
shard_manifest
build_summary
release_manifest
verification
promote_current
```

status 값:

```text
started
complete
cached
rebuilt
failed
```

상위 v3 DAG progress 파일은 `indexes/build_progress.jsonl`이다. 하위
source-artifact SQLite phase log는 이 파일을 지우거나 섞으면 안 된다. 하위 로그는
별도 ticker별 파일에 기록한다.

```text
indexes/progress/source_artifact_sqlite/<TICKER>.jsonl
```

CLI 계약:

```text
release status
  progress path
  latest progress_status
  progress_count
  progress_ticker
  progress_output
  progress_error

release watch
  release-build.log 또는 publish-dev.log tail
  build_progress.jsonl latest status
  follow 중 append되는 progress event를 사람이 읽는 한 줄로 출력
```

### 19.3 MCP metrics

최소 metrics:

```text
health status
release id
startup verification status
store pool hits/misses
store rotations
active/retired stores
open shard count
tool latency
slow path count
```

## 20. 장애 시나리오와 대응

### 20.1 release force가 오래 걸림

확인:

```bash
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology release plan
```

원인 후보:

```text
cold cache
schema version bump
worker count 부족 또는 과다
disk IO saturation
global merge
deep verification
stuck worker
```

### 20.2 watch가 잘못된 cache directory를 선택

정상 동작은 release worker state의 release id를 우선해야 한다.

대응:

```bash
uv run krw-ontology release status
uv run krw-ontology release watch <release-id>
```

hidden cache directory를 candidate release로 선택하면 CLI bug다.

### 20.3 MCP가 health port를 열지 않음

먼저:

```bash
uv run krw-ontology release verify \
  --startup-check \
  --root ~/krw-ontology-data/releases/prod/current \
  --env prod \
  --require-current-symlink
```

확인:

```text
manifest v3 여부
current symlink 여부
global spine 존재/open 가능 여부
shard manifest 존재 여부
startup path에 deep verification이 남아 있는지
target release id
```

timeout만 늘리는 것은 근본 해결이 아니다.

### 20.4 quality repair plan stale

원인:

```text
plan 생성 후 current release 변경
manifest/source manifest/global spine 변경
```

정상 대응:

```bash
uv run krw-ontology quality repair plan
```

### 20.5 prod publish 후 health mismatch

정상 동작:

```text
activation 실패
previous prod/current 복구
failure event 기록
target release는 조사 가능하게 보존
```

## 21. Security와 integrity

### 21.1 path containment

manifest와 shard manifest의 path는 release root 밖으로 나갈 수 없다.

거절:

```text
absolute path
../ escape
symlink로 root 밖 escape
```

### 21.2 immutable current guard

writer command는 active current 또는 그 하위 path를 output으로 받으면 거절해야 한다.

### 21.3 SQLite

startup:

```text
cheap open + metadata
```

offline deep verify:

```text
schema
digest
topology
consistency
smoke
```

### 21.4 operator override

다음 override는 normal workflow가 아니다.

```text
--no-cache
--allow-stale-plan
--foreground
explicit release root/path options
```

사용 시 로그와 이유를 남겨야 한다.

## 22. Test strategy

### 22.1 unit

```bash
uv run pytest -q \
  tests/unit/test_spine_schema.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_release_v3.py \
  tests/unit/test_agent_index.py \
  tests/unit/test_mcp_server.py \
  tests/unit/test_quality.py
```

### 22.2 CLI

```bash
uv run pytest -q tests/unit/test_cli.py
```

### 22.3 static

```bash
uv run python -m py_compile \
  src/krw_ontology/agent_index/spine_schema.py \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/agent_index/spine_router.py \
  src/krw_ontology/release.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/mcp_server/http_server.py \
  src/krw_ontology/mcp_server/server.py \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/cli/main.py

git diff --check
```

### 22.4 front runtime

```bash
cd ~/krw-ontology-front

sh -n \
  scripts/start-mcp-server-dev.sh \
  scripts/verify-mcp-server-dev.sh \
  scripts/install-mcp-server-dev-launchd.sh \
  scripts/start-mcp-server-production.sh \
  scripts/verify-mcp-server-production.sh \
  scripts/install-mcp-server-launchd.sh \
  scripts/mac-worker-preflight.sh

npm run typecheck
npx vitest run src/lib/deploy-scripts.test.ts
```

### 22.5 integration scenarios

필수:

1. two-ticker cold v3 build
2. warm no-change force
3. single ticker change
4. new ticker add
5. ticker removal
6. company shard schema version bump
7. missing shard rejection
8. corrupt cache rebuild
9. quality check and repair stale guard
10. MCP startup without deep scan
11. MCP current hot swap
12. prod delta publish
13. health release id mismatch rollback
14. v1/v2 hard reject
15. debug monolith exclusion

## 23. Performance baseline

완료 전에 실제 데이터로 기록해야 한다.

| metric | cold | warm | 목표/판단 |
| --- | --- | --- | --- |
| full v3 build time | 측정 필요 | 해당 없음 | baseline |
| no-change force | 해당 없음 | 측정 필요 | cache 효과 |
| single ticker change | 측정 필요 | 측정 필요 | partial rebuild 효과 |
| new ticker add | 측정 필요 | 측정 필요 | scale-out 비용 |
| global spine bytes | 측정 필요 | 동일 | monolith 대비 compact성 |
| total shard bytes | 측정 필요 | 동일 | evidence storage |
| release bundle bytes | 측정 필요 | 측정 필요 | publish 비용 |
| delta upload bytes | 해당 없음 | 측정 필요 | 네트워크 절감 |
| MCP startup seconds | 측정 필요 | 측정 필요 | startup contract |
| ticker query latency | 측정 필요 | 측정 필요 | direct shard |
| no-ticker latency | 측정 필요 | 측정 필요 | bounded fanout |
| trace latency | 측정 필요 | 측정 필요 | locator route |
| chain latency | 측정 필요 | 측정 필요 | global chain |
| quality check time | 측정 필요 | 측정 필요 | shard scan 비용 |

기록 예:

```json
{
  "date": "2026-06-12",
  "release_id": "20260612_120000",
  "ticker_count": 162,
  "source_bytes": 0,
  "global_spine_bytes": 0,
  "company_shards_bytes": 0,
  "cold_build_seconds": 0,
  "warm_force_seconds": 0,
  "single_ticker_change_seconds": 0,
  "new_ticker_seconds": 0,
  "mcp_startup_seconds": 0,
  "quality_check_seconds": 0
}
```

## 24. 현재 branch 구현 상태

### 24.1 2026-06-12 검증 결과

| 검증 | 결과 |
| --- | --- |
| MCP tool schema/runtime suite | `73 passed` |
| expanded MCP/release v3/spine suite | `81 passed` |
| full CLI unit suite | `136 passed` |
| release v3 + spine builder + CLI suite | `146 passed` |
| standalone agent index suite | `38 passed, 1 skipped` |
| combined MCP/CLI runtime suite | `202 passed` |
| current targeted v3 matrix | `302 passed, 1 skipped` |
| front TypeScript typecheck | passed |
| front selected runtime tests | `48 passed` across 7 files |
| Python compile for selected v3/MCP files | passed |
| shell syntax for selected front runtime scripts | passed |
| both repo diff whitespace check | passed |
| key CLI help contract check | passed |
| local Markdown link target check | passed |

검증 명령의 정확한 범위:

```bash
uv run pytest -q \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_mcp_server.py \
  --maxfail=10

uv run pytest -q \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_mcp_server.py \
  tests/unit/test_release_v3.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_spine_schema.py \
  --maxfail=10

uv run pytest -q tests/unit/test_cli.py --maxfail=10

uv run pytest -q \
  tests/unit/test_agent_index.py \
  --maxfail=10

uv run pytest -q \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_cli.py \
  --maxfail=10

uv run pytest -q \
  tests/unit/test_agent_index.py \
  tests/unit/test_cli.py \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_quality.py \
  tests/unit/test_release_v3.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_spine_schema.py \
  tests/unit/test_ontology_v02.py \
  tests/unit/test_observability_assets.py \
  tests/unit/test_observability_config.py \
  --maxfail=10
```

front 검증은 deploy/runtime 관련 선택 테스트 7개, TypeScript typecheck, 관련 shell
script syntax 검사를 포함한다. 이 결과는 실제 162 ticker build, 실제 MCP restart,
실제 원격 prod activation을 대신하지 않는다.

### 24.2 구현됨

| 영역 | 상태 |
| --- | --- |
| global spine schema | implemented, verified |
| company shard direct build | implemented, verified |
| spine fragment projection | implemented, verified |
| deterministic global merge | implemented, verified |
| exact cross-company links | implemented, verified |
| v3 build plan/summary/shard manifest | implemented, verified |
| company shard/fragment cache | implemented, verified |
| v3 manifest writer | implemented, verified |
| v3 startup verifier | implemented, verified |
| v3 deep verifier | implemented, verified |
| public release writer/verifier v3-only 단일화 | implemented, verified |
| non-v3 manifest SQLite-open 전 hard reject | implemented, verified |
| global spine/shard manifest/company shard digest 강제 | implemented, verified |
| shard manifest quality_summary rollup 생성 | implemented, verified |
| deep verifier의 shard quality_summary 재계산 검증 | implemented, verified |
| standalone index verify와 release digest 계약 분리 | implemented, verified |
| 손상 global spine의 구조화된 verification failure | implemented, verified |
| legacy release writer/startup verifier/path resolver 제거 | implemented, verified |
| legacy monolith ranking calibration command/logic 제거 | implemented, verified |
| release force background worker | implemented, verified |
| release plan/status/watch/cancel | implemented, verified |
| v3 DAG build progress JSONL | implemented, verified |
| release manifest/verification/promote progress events | implemented, verified |
| status/watch progress summary and follow output | implemented, verified |
| v3 quality release scanner | implemented, verified |
| quality check bounded mode의 manifest quality_summary rollup 사용 | implemented, verified |
| quality repair fingerprint/stale guard | implemented, verified |
| prod v3 preflight | implemented, verified by unit coverage |
| prod delta bundle/reconstruction | implemented, unit coverage exists |
| prod remote activation shard digest/quality_summary preflight | implemented, verified by unit coverage |
| prod rollback target shard digest/quality_summary preflight | implemented, verified by unit coverage |
| remote health release id rollback | implemented, unit coverage exists |
| MCP external tool schema의 `root`/`index_path` 제거 | implemented, verified |
| MCP tool internal helper의 runtime path override 제거 | implemented, verified |
| MCP tool runtime forbidden args hard reject | implemented, verified |
| MCP health/metrics/diagnostics v3-only surface | implemented, verified |
| MCP health/runtime payload `global_spine_path` 수렴 | implemented, verified |
| MCP v3 startup preflight | implemented, verified |
| CLI release transaction result `global_spine_path` 수렴 | implemented, verified |
| default ontology store routing의 v3 spine-only 수렴 | implemented, verified |
| legacy `KRW_ONTOLOGY_INDEX_PATH` resolver 제거 | implemented, verified |
| quality CLI/report naming의 `global_spine_path` 수렴 | implemented, verified |
| `agent_index` package public surface에서 legacy `build_agent_index` 제거 | implemented, verified |
| `agent_index` package public surface에서 legacy fragment/shard helper 제거 | implemented, verified |
| `agent_index` package public surface에서 low-level `OntologyStore` 제거 | implemented, verified |
| source artifact SQLite primitive를 `source_artifact_sqlite.py`로 명확히 분리 | implemented, verified |
| MCP observability metric/alert naming을 `global_spine`/`global_topic_spine`으로 수렴 | implemented, verified |
| MCP `retrieve_tool` normal payload에 v3 routing/missing shard diagnostics 노출 | implemented, verified |
| MCP cross-company chain이 global spine neighbor를 반환하는 smoke | implemented, verified |
| MCP no-ticker retrieve가 global spine fanout route를 반환하는 smoke | implemented, verified |
| front prod launchd pre-restart preflight | implemented |
| front dev MCP v3 startup path | implemented |
| front MCP health reader/scripts `global_spine_path` 수렴 | implemented, verified |
| docker worker global spine/shard manifest path | implemented |
| front legacy data deploy alias/script 제거 | implemented, verified |
| front queue legacy `--no-rebuild-agent-index` 제거 | implemented, verified |
| front v3 startup-safe deploy tests | implemented, verified |

### 24.3 partial 또는 blocked

| 영역 | 상태 | 필요한 작업 |
| --- | --- | --- |
| company shard internal naming | partial | 기존 agent-index primitive를 shard 역할에 맞게 명확히 분리 |
| router API parity | partial | retrieve/trace/chain/compare/topic/quality end-to-end smoke와 실제 데이터 smoke 확대 |
| quality scale | partial | 대용량 runtime budget와 전체 topology 검사 비용 측정 |
| release integration smoke | partial | router/quality/performance 검증을 release trust path와 분리한 integration suite 완성 |
| real production baseline | blocked | 실제 162 ticker cold/warm/single/new ticker 측정 |
| end-to-end cutover | blocked | 실제 dev v3 release, prod publish, MCP health, rollback 검증 |

### 24.4 현재 코드에서 반드시 제거할 legacy 흔적

`krw-ontology`:

```text
old agent_index naming이 company shard 역할과 혼동되는 surface
builder.py 내부의 legacy monolith primitive naming
```

이미 제거된 release legacy:

```text
RELEASE_FORMAT_V2
build_release_manifest/write_release_manifest
verify_release_startup
resolve_manifest_index_path
non-v3를 legacy verifier로 보내는 verify_release_root branch
calibrate-ranking-thresholds와 monolith-vs-global-topic ranking calibration
```

v1/v2 문자열은 정상 성공 경로가 아니라 다음 negative test에만 남길 수 있다.

```text
SQLite open 전 hard reject
prod upload/restart 전 hard reject
MCP startup 전 hard reject
production monolith 미생성 assertion
```

`krw-ontology-front`:

```text
normal deploy/runtime path의 legacy data script와 option은 제거 완료
deploy-scripts.test.ts에 legacy 문자열이 다시 들어오지 않는 negative assertion만 유지
향후 변경 시 global spine/shard env가 빠지지 않는지 계속 검사
```

최종 결정은 호환 유지가 아니라 제거 또는 hard reject다.

### 24.5 release trust path 구현 기록

release trust path는 다음 세 계층으로 고정한다.

```text
manifest writer
  build_release_manifest_v3
  write_release_manifest_v3

startup verifier
  verify_release_startup_v3

deep verifier
  verify_release_root
    -> _verify_release_root_v3
    -> verify_spine_shard_release
```

각 계층의 책임:

| 계층 | 읽는 범위 | 금지되는 동작 | 실패 결과 |
| --- | --- | --- | --- |
| writer | immutable candidate output | active current 수정, v1/v2 manifest 생성 | exception, candidate 미활성 |
| startup verifier | manifest, path, global spine metadata | full digest, 모든 shard open, count scan | `ok=false`, MCP 미시작 |
| deep verifier | manifest, 모든 required digest, schema/topology | current 전환, runtime serving | `ok=false`, promote/publish 거절 |

`verify_release_root`의 non-v3 처리 순서:

```text
manifest load
-> format가 v3인지 확인
-> 아니면 manifest_format_unsupported
-> SQLite open 없음
-> legacy verifier 호출 없음
```

이 순서는 중요하다. v1/v2 release가 90GB monolith를 가리키더라도, deep verifier나
startup verifier가 그 파일을 읽기 시작해서는 안 된다.

deep digest 검증 순서:

```text
release manifest의 global spine sha256
release manifest의 shard manifest sha256
release manifest 또는 shard manifest의 company shard sha256
schema/topology/endpoint consistency
```

digest mismatch는 손상, 잘못된 delta reconstruction, build 이후 mutation을 모두
잡는다. digest가 아예 없으면 release deep verification은 실패해야 한다.

standalone index output의 처리:

```text
index build
-> 아직 release manifest가 없을 수 있음
-> index verify는 schema/topology와 shard manifest 정보를 검사
-> release manifest digest missing을 오류로 만들지 않음

release writer 실행
-> canonical digest가 manifest에 기록됨
-> release verify부터 digest가 필수
```

손상 SQLite 처리:

```text
verify_global_spine_schema가 sqlite_error 반환
-> verify_spine_shard_release가 error를 수집
-> 손상 DB를 다시 열어 count/attach를 시도하지 않음
-> CLI는 FAIL 항목을 출력
-> uncaught sqlite3.DatabaseError 없음
```

주요 error contract:

| error | 의미 | 조치 |
| --- | --- | --- |
| `manifest_format_unsupported` | v3가 아닌 release | v3 release 재생성 |
| `global_spine_sha256_missing` | release manifest digest 누락 | writer/build contract 수정 후 재생성 |
| `global_spine_sha256_mismatch` | spine이 manifest 이후 변경 또는 손상 | candidate 폐기 후 재생성 |
| `shard_manifest_sha256_mismatch` | shard topology manifest 손상 | candidate 폐기 후 재생성 |
| `shard:<TICKER>:sha256_missing` | ticker shard digest 누락 | manifest writer 수정 후 재생성 |
| `shard:<TICKER>:sha256_mismatch` | ticker shard 손상/잘못된 delta | 해당 candidate 폐기 후 재전송/재생성 |
| `global_spine:sqlite_error:*` | global spine이 SQLite로 열리지 않음 | candidate 폐기 |
| `shard:<TICKER>:shard_missing` | required shard 누락 | candidate 폐기 또는 delta 재구성 수정 |
| `manifest_indexes_*_path_outside_root` | path containment 위반 | manifest 생성 로직 수정 |

### 24.6 검증 모드별 테스트 책임

| 테스트 범주 | 반드시 증명할 것 |
| --- | --- |
| release v3 unit | writer가 v3 digest/relative path를 기록하고 deep verify가 통과 |
| non-v3 rejection | SQLite open 전에 v1/v2/unknown format 거절 |
| digest corruption | global spine, shard manifest, company shard 변경 감지 |
| corrupt SQLite | exception 누출 없이 구조화된 failure |
| standalone index | release manifest 없이 `index build -> index verify` 성공 |
| promote/rollback | target deep/startup verify 실패 시 current 불변 |
| prod publish | remote upload/restart 전에 v3 source preflight |
| MCP startup | lightweight verifier만 실행하고 health port를 빠르게 열 수 있음 |

## 25. 구현 작업 패키지

### WP-1 MCP v3-only hard cleanup - 구현 및 targeted 검증 완료

목표:

```text
MCP health, metrics, diagnostics, tools가 v3만 이해한다.
```

작업:

1. health/metrics/diagnostics의 non-v3 branch 제거
2. missing spine error를 `global_spine_not_found`로 통일
3. old agent index/catalog/topic health field 제거
4. external MCP tool schema에서 `root`와 `index_path` 제거
5. unused persistent monolith open helper 제거
6. v1/v2 manifest가 SQLite open 전에 거절되는 테스트 유지

완료 기준:

```text
rg로 production MCP path에 legacy index env/path 없음
test_mcp_server.py 통과
health contract가 v3-only
외부 MCP tool 12개 schema에 root/index_path 없음
internal MCP tools가 configured runtime release/global spine만 사용
root/index_path/global_spine_path/release_root override 인자가 들어오면 hard reject
```

추가 완료된 cleanup:

```text
unit fixture 전용 root/index_path override 제거
KRW_ONTOLOGY_INDEX_PATH 기반 resolver 제거
open_ontology_store default를 v3 spine-only로 수렴
public open_ontology_store의 routing="monolith"/"auto"/"shards"는 거절
source artifact SQLite primitive는 내부 guard를 통해서만 사용
```

### WP-2 release v3-only hard cleanup - 구현 및 targeted 검증 완료

목표:

```text
normal release command가 v3만 생성, 검증, promote한다.
```

완료된 작업:

1. legacy verifier와 writer 삭제
2. normal CLI가 legacy verifier를 호출하지 않는 테스트
3. v1/v2 manifest hard reject
4. old index path option 제거
5. release verifier/report/web catalog caller를 `global_spine_path` 중심으로 전환
6. global spine/shard manifest/company shard digest 검증
7. 손상 SQLite를 구조화된 verification failure로 반환
8. legacy ranking calibration command와 report 제거

완료 기준:

```text
normal help와 command path에 v1/v2 acceptance 없음
release unit/integration 통과
```

남은 범위는 release trust path의 v1/v2 호환 제거가 아니라, CLI/MCP 내부 generic
`index_path` naming 정리와 실제 데이터 integration이다.

### WP-3 front legacy runtime 제거 - 구현 및 targeted 검증 완료

목표:

```text
front의 normal deploy/runtime path가 v3만 사용한다.
```

작업:

1. legacy data GCP script 삭제
2. package legacy aliases 제거
3. queue wrapper legacy option 제거
4. stale deploy test 갱신
5. fixture naming 정리
6. docs/env example v3-only 확인

완료 기준:

```text
front broad rg에서 normal runtime agent_index.sqlite 없음
selected vitest 전부 통과
typecheck 통과
shell syntax 통과
```

### WP-4 router parity

목표:

```text
모든 user-facing retrieval 기능이 monolith 없이 동작한다.
```

필수 smoke:

```text
ticker retrieve
no-ticker retrieve
trace
local chain
cross-company chain
compare
topic map
company context
quality
missing shard failure
current hot swap
```

### WP-5 quality finalization

목표:

```text
quality가 v3 topology와 shard evidence를 정확히 검사하고 운영 가능한 시간 안에 끝난다.
```

작업:

```text
duplicate object id
locator/object 양방향
document/catalog 양방향
edge/chain endpoint
shard count/hash
repair plan stale guard
large release timing
bounded sampling과 full mode 분리 여부 결정
```

### WP-6 integration과 performance

목표:

```text
실제 데이터에서 correctness와 성능을 숫자로 증명한다.
```

순서:

1. dev cold build
2. warm no-change force
3. single ticker edit
4. new ticker add
5. quality check
6. MCP startup/query smoke
7. prod dry-run delta
8. prod activate
9. forced health mismatch rollback test
10. 결과 문서화

### WP-7 최종 legacy 제거와 문서 수렴

목표:

```text
코드, 테스트, CLI help, 운영 문서가 모두 v3 하나만 정상 계약으로 설명한다.
```

작업:

1. release v3-only cleanup 완료 상태를 모든 문서에 반영
2. 남은 v1/v2 fixture가 negative rejection/assertion 용도인지 broad audit
3. MCP tool 내부 `root/index_path` fixture override를 test-only seam으로 격리 또는 제거
4. company shard 내부에서 재사용하는 agent-index primitive와 production monolith
   contract를 분리
5. 이전 v1/v2 문서 상단에 historical-only 경고 유지
6. CLI help와 운영 문서의 normal path를 `release force`, `release startup-check`,
   `quality check`, `prod publish-dev`로 수렴
7. broad `rg` audit 결과를 검증 기록에 남김

완료 기준:

```text
normal release verifier가 v3가 아니면 SQLite를 열기 전에 hard reject
normal command가 legacy manifest를 생성하지 않음
old 문서가 source of truth라고 주장하지 않음
legacy 문자열은 migration history, negative assertion, 명시적 rejection test에만 존재
```

### WP-8 함수 단위 구현 ledger

이 표는 현재 branch에서 남은 작업을 실제 코드 단위로 연결한다. 작업 완료 시
함수명, 테스트명, 상태를 함께 갱신한다.

| 파일/영역 | 현재 상태 | 최종 변경 | 완료 증명 |
| --- | --- | --- | --- |
| `release.py::verify_release_root` | v3-only, non-v3 immediate hard reject | 완료 | non-v3 fixture에 SQLite open이 호출되지 않는 test |
| `release.py::verify_release_startup_v3` | 유일한 startup verifier | 완료, lightweight 계약 유지 | startup tests와 MCP preflight |
| `release.py::build_release_manifest_v3` / `write_release_manifest_v3` | 유일한 normal release writer | 완료 | normal command가 v3만 생성 |
| `release.py::_verify_release_root_v3` | `global_spine_path` 중심 결과 계약 | 완료 | CLI/web catalog caller tests |
| `agent_index/spine_verify.py::verify_spine_shard_release` | release manifest가 있으면 digest 강제, standalone index output도 검사 | release/index 모드 책임 분리 완료 | digest mismatch, no-manifest index verify, corrupt SQLite tests |
| `cli/main.py::release_verify_cmd` | v3-only verifier 결과 사용 | 완료 | CLI non-v3 hard-reject test |
| `cli/main.py` release transaction result dict | `global_spine_path` 중심 | 완료 | full CLI suite와 broad `rg` |
| `mcp_server/server.py` health/diagnostics payload | `global_spine_path`, `company_shards_present`, renamed store hot-swap fields | 완료 | MCP server suite와 front typecheck/vitest |
| `web_catalog.py` | v3-only verifier 결과만 허용 | 완료 | export web catalog v3 test |
| `mcp_server/tools.py` internal helpers | 외부 schema와 internal runtime 모두 v3-only | 완료, configured current/global spine만 사용 | MCP external schema audit, forbidden override tests, runtime fixture tests |
| `quality/scanner.py` | normal path는 `QualityReleaseScanner`, shard-local helper는 `QualityShardScanner` | 출력/모델 명칭을 global spine/shard terminology로 정리 | quality CLI와 scanner tests |
| `agent_index/builder.py` | company shard가 기존 schema primitive를 재사용, legacy builder/fragment/shard helpers는 package public surface에서 제거됨 | production v3 builder와 reusable shard primitive의 내부 소유권을 계속 명확화 | shard build tests, no-monolith assertion, public export negative test |
| `tests/unit/test_cli.py` | release v1/v2는 hard-reject/negative fixture로만 유지 | 남은 monolith primitive fixture를 production contract와 분리 | full suite와 legacy-success audit |
| `tests/unit/test_agent_index.py` | reusable legacy primitive 역사 테스트 포함 | reusable shard primitive test와 production normal path를 명확히 분리 | production v3 contract와 충돌 0건 |
| `krw-ontology-front` deploy/runtime | normal path cleanup 완료 | negative assertion과 v3 env contract 유지 | selected vitest, typecheck, shell syntax |

### WP-9 남은 구현의 정확한 실행 순서

아래 순서는 의존성과 회귀 범위를 기준으로 고정한다.

#### 1. Release verifier를 v3-only로 단일화 - 완료

```text
verify_release_root의 legacy branch 제거
-> non-v3 immediate hard reject
-> verify_release_startup 삭제
-> resolve_manifest_index_path 삭제
-> CLI/web catalog caller payload 전환
-> targeted release/CLI tests
```

이 단계가 먼저인 이유:

```text
release verifier는 promote, prod publish, rollback, web catalog의 trust root다.
여기에 legacy acceptance가 남아 있으면 다른 surface가 v3-only여도 최종 계약이 아니다.
```

#### 2. Legacy manifest constructor와 성공 fixture 제거 - release trust path 완료

```text
build_release_manifest/write_release_manifest 삭제
-> v3 writer만 유지
-> v1/v2 성공 fixture를 v3 fixture로 교체
-> non-v3는 rejection test로만 유지
-> full CLI/release suite
```

v1/v2 test data를 모두 삭제한다는 뜻은 아니다. 다음 용도만 허용한다.

```text
hard reject가 SQLite open 전에 일어나는지 확인
prod publish가 upload 전에 거절하는지 확인
MCP startup이 port open 전에 명확히 실패하는지 확인
```

#### 3. MCP/CLI 내부 naming과 runtime override 정리 - health/result 완료

```text
release transaction result의 index_path alias 제거 - 완료
MCP health/runtime의 generic index_path를 global_spine_path로 수렴 - 완료
front MCP health reader/scripts의 payload.index_path 의존 제거 - 완료
MCP tool internal root/index override 제거 - 완료
external MCP schema에 root/index_path가 다시 노출되지 않는 negative test 유지
```

#### 4. Router와 MCP 기능 parity 완성

```text
ticker retrieve
no-ticker bounded retrieve
trace
local chain
cross-company chain
compare
topic map
quality
missing shard
current hot swap
```

각 smoke는 production monolith 없이 실행해야 한다. 테스트가 통과하더라도
`agent_index.sqlite`를 fallback으로 열었다면 실패로 간주한다.

#### 5. Quality topology 검사와 비용 확정

```text
locator <-> shard object
catalog <-> shard document
edge/chain endpoint
duplicate object id
shard digest/count
repair plan stale guard
large release runtime
```

quality는 자동 prod gate가 아니다. 그러나 operator가 실행했을 때는 v3 topology
결함을 놓치지 않아야 한다.

#### 6. 실제 데이터 integration과 performance baseline

```text
162 ticker cold build
warm no-change force
single ticker change
new ticker add
ticker removal
MCP startup/query
quality check
prod delta dry-run
prod activate
rollback rehearsal
```

실제 prod activation과 launchd restart는 명시적인 운영 승인 이후에만 수행한다.
그 전까지는 unit/integration, local release, dry-run으로 검증한다.

### WP-10 구현 변경 단위와 review 규칙

권장 변경 단위:

| 변경 단위 | 포함 | 포함하면 안 되는 것 |
| --- | --- | --- |
| release v3-only cutover | verifier, writer, caller, 해당 tests | router 기능 확장 |
| MCP/router parity | router/tool behavior, parity tests | release manifest 변경 |
| quality finalization | topology checks, repair fingerprint, timing | prod remote shell 변경 |
| prod integration | delta reconstruction, activation, rollback tests | schema 변경 |
| performance | baseline harness, measurement report | correctness contract 완화 |

review에서 반드시 확인할 질문:

1. 이 변경이 `current` 내부를 직접 수정하는가?
2. non-v3를 정상 입력으로 받아들이는 경로가 생기는가?
3. missing shard를 fallback으로 숨기는가?
4. startup path에 hash, integrity check, broad scan이 들어가는가?
5. cache hit가 source hash와 version contract를 검증하는가?
6. 실패 전에 `current` 또는 remote active pointer를 바꾸는가?
7. quality repair가 release를 수정하는가?
8. 새 schema/version 변경이 정확한 cache invalidation 범위를 갖는가?
9. 테스트가 production monolith 없이 통과하는가?
10. 운영자가 normal path에서 긴 경로나 legacy option을 알아야 하는가?

## 26. 파일 소유 경계

### krw-ontology

| 영역 | 파일 |
| --- | --- |
| spine schema | `src/krw_ontology/agent_index/spine_schema.py` |
| v3 builder/cache/DAG | `src/krw_ontology/agent_index/spine_builder.py` |
| cross-company links | `src/krw_ontology/agent_index/cross_company_links.py` |
| v3 verifier | `src/krw_ontology/agent_index/spine_verify.py` |
| v3 router | `src/krw_ontology/agent_index/spine_router.py` |
| router facade | `src/krw_ontology/agent_index/router.py` |
| release contract | `src/krw_ontology/release.py` |
| CLI | `src/krw_ontology/cli/main.py` |
| quality | `src/krw_ontology/quality/` |
| MCP startup | `src/krw_ontology/mcp_server/http_server.py` |
| MCP health/tools | `src/krw_ontology/mcp_server/server.py`, `tools.py` |
| path config | `src/krw_ontology/config/paths.py` |

### krw-ontology-front

| 영역 | 파일 |
| --- | --- |
| prod MCP start/verify/install | `scripts/start-mcp-server-production.sh`, `verify-mcp-server-production.sh`, `install-mcp-server-launchd.sh` |
| dev MCP start/verify/install | `scripts/start-mcp-server-dev.sh`, `verify-mcp-server-dev.sh`, `install-mcp-server-dev-launchd.sh` |
| Mac worker preflight/start | `scripts/mac-worker-preflight.sh`, `start-mac-worker-production.sh` |
| compose | `docker-compose.yml`, `scripts/deploy-vm-compose.sh` |
| app MCP client | `src/lib/agent/mcp-client.ts` |
| worker health | `src/lib/agent/worker-heartbeat.ts` |
| deploy contract tests | `src/lib/deploy-scripts.test.ts` |

## 27. 운영 runbook

### 27.1 dev build 전

```bash
cd ~/krw-ontology
export KRW_ONTOLOGY_ENV=dev

uv run krw-ontology release plan
```

확인:

```text
source root가 맞음
env가 dev
dirty ticker가 예상과 일치
schema/version invalidation이 예상과 일치
```

### 27.2 dev release 생성

```bash
uv run krw-ontology release force
uv run krw-ontology release watch
uv run krw-ontology release status
```

### 27.3 startup contract 확인

```bash
uv run krw-ontology release verify \
  --startup-check \
  --root ~/krw-ontology-data/releases/dev/current \
  --env dev \
  --require-current-symlink
```

### 27.4 quality 확인

```bash
uv run krw-ontology quality check
uv run krw-ontology quality tickers --severity high
```

문제 수정 후:

```bash
uv run krw-ontology release force
uv run krw-ontology release watch
uv run krw-ontology quality check
```

### 27.5 prod 준비

최초 한 번:

```bash
uv run krw-ontology prod configure \
  --host <user@host> \
  --remote-root <remote-root> \
  --reload-command '<reload command>' \
  --health-url <health-url>
```

매 배포:

```bash
uv run krw-ontology prod doctor
uv run krw-ontology prod publish-dev --dry-run
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

### 27.6 prod rollback

```bash
uv run krw-ontology prod rollback
```

특정 release:

```bash
uv run krw-ontology prod rollback <release-id>
```

### 27.7 front MCP runtime

prod preflight/install dry-run:

```bash
cd ~/krw-ontology-front
scripts/install-mcp-server-launchd.sh --dry-run
```

실제 restart는 target prod/current가 유효한 v3임을 확인한 뒤에만 수행한다.

## 28. Definition of done

아래가 모두 참일 때만 v3 final production complete라고 부른다.

1. `release force`가 v3 release만 만든다.
2. production release에 `indexes/agent_index.sqlite`가 없다.
3. dev/current와 prod/current가 v3만 가리킨다.
4. v1/v2 release는 normal path에서 hard reject된다.
5. MCP startup이 deep scan 없이 health를 연다.
6. MCP health와 tools가 v3-only다.
7. ticker query가 direct shard로 동작한다.
8. no-ticker query가 global spine과 bounded fanout으로 동작한다.
9. trace가 global locator와 shard로 동작한다.
10. local/cross-company chain smoke가 통과한다.
11. compare/topic/company context/quality smoke가 통과한다.
12. missing shard가 fallback 없이 실패한다.
13. quality check가 immutable v3 release를 read-only로 검사한다.
14. quality repair가 running-root만 수정한다.
15. repair plan stale guard가 동작한다.
16. prod publish가 v1/v2와 monolith-required release를 거절한다.
17. delta publish가 changed files만 전송하고 candidate를 재구성한다.
18. remote preflight가 activation 전에 shard digest와 `quality_summary` 재계산까지 통과한다.
19. health target release id mismatch가 rollback된다.
20. front dev/prod/docker runtime이 v3 env만 사용한다.
21. legacy data deploy와 option이 제거 또는 hard reject된다.
22. full targeted/unit/integration tests가 통과한다.
23. real data cold/warm/single/new ticker baseline이 기록된다.
24. real MCP startup과 query baseline이 기록된다.
25. 실제 prod cutover와 rollback rehearsal가 완료된다.

## 29. 아주 쉬운 설명

기존 구조는 모든 내용을 넣은 아주 큰 책과 회사별 작은 책을 둘 다 만들었다.
그래서 같은 내용이 중복되고, 큰 책을 다시 만들고 검사하는 데 오래 걸렸다.

v3는 두 역할을 나눈다.

```text
global_spine.sqlite
  어느 회사와 어떤 내용이 연결되는지 알려주는 지도

company shard
  실제 문장, 숫자, 근거가 들어 있는 회사별 책
```

질문이 들어오면 먼저 지도에서 필요한 회사를 찾는다. 그다음 필요한 회사 책만 연다.
그래서 전체 연결 chain은 유지하면서 큰 monolith를 production에서 없앨 수 있다.

평소 흐름은 다음과 같다.

```text
source 수정
-> release force
-> release watch
-> quality check
-> prod publish-dev
```

`force`는 새 release를 만든다. 모든 것을 무조건 다시 계산한다는 뜻은 아니다.
변경되지 않은 회사는 cache를 사용할 수 있다.

quality 문제를 고친 뒤 다시 `force`를 실행하는 이유는, 현재 release를 직접 고치지
않고 항상 새 immutable release로 바꾸기 때문이다.

## 30. 개발자 상세 부록

이 장은 실제 개발자가 코드를 수정할 때 보는 상세 작업 문서다. 앞 장들이
architecture와 운영 계약을 설명한다면, 이 장은 "어느 파일을 왜 고치고, 무엇을
깨면 안 되고, 어떤 테스트로 증명할 것인가"에 집중한다.

### 30.1 최종형의 한 문장 정의

```text
running-root의 canonical source artifact를 읽어 immutable v3 release를 만들고,
MCP와 quality는 current release의 global_spine.sqlite와 company shards만 읽는다.
```

따라서 개발자가 어떤 코드를 보더라도 먼저 아래 질문을 해야 한다.

```text
이 코드가 active current release를 직접 수정하는가?
이 코드가 v1/v2 release를 성공 경로로 받아들이는가?
이 코드가 production serving에서 agent_index.sqlite를 요구하는가?
이 코드가 MCP startup에서 대용량 scan을 수행하는가?
이 코드가 shard 누락을 monolith fallback으로 숨기는가?
```

하나라도 "예"이면 최종형 위반이다.

### 30.2 파일별 책임 지도

| 영역 | 파일 | 책임 |
| --- | --- | --- |
| release transaction | `src/krw_ontology/release.py` | v3 release 생성, manifest 작성, startup/deep verifier, current promote |
| CLI | `src/krw_ontology/cli/main.py` | normal command surface, config 기본값, background worker, status/watch |
| path config | `src/krw_ontology/config/paths.py` | v3 release root/publish root/running root 해석 |
| global spine schema | `src/krw_ontology/agent_index/spine_schema.py` | global spine table/index 생성 |
| v3 builder | `src/krw_ontology/agent_index/spine_builder.py` | company shard, spine fragment, global spine merge |
| v3 verifier | `src/krw_ontology/agent_index/spine_verify.py` | release topology, digest, schema, lightweight startup check |
| runtime router | `src/krw_ontology/agent_index/spine_router.py` | global spine 기반 route, shard lazy open, fanout/merge |
| store factory | `src/krw_ontology/agent_index/router.py` | production default routing을 spine으로 고정 |
| legacy primitive | `src/krw_ontology/agent_index/builder.py`, `store.py` | shard 내부 schema primitive와 저수준 테스트용 monolith primitive |
| MCP startup | `src/krw_ontology/mcp_server/http_server.py` | 포트 open 전 lightweight release startup check |
| MCP tools | `src/krw_ontology/mcp_server/tools.py` | configured runtime release/global spine만 사용 |
| MCP health | `src/krw_ontology/mcp_server/server.py` | runtime 상태와 release id, global spine path 노출 |
| quality scanner | `src/krw_ontology/quality/scanner.py` | v3 release quality scan, shard-local quality events, topology consistency |
| quality models | `src/krw_ontology/quality/models.py` | repair plan/job snapshot과 stale guard fingerprint |
| quality runner | `src/krw_ontology/quality/runner.py` | running-root/source repair 실행 |
| deployment wrapper | `scripts/deploy-with-local-release.sh` | local running-root에서 v3 release force 후 deploy command 실행 |

소유권 판단:

```text
release.py가 release trust root다.
spine_builder.py가 build artifact trust root다.
spine_verify.py가 index topology trust root다.
spine_router.py가 runtime correctness trust root다.
quality/scanner.py가 operator quality trust root다.
mcp_server/http_server.py가 startup latency trust root다.
```

### 30.3 정상 데이터 흐름

정상 build 흐름:

```text
1. running-root 확인
2. source_manifest.json 생성 또는 검증
3. source manifest hash 계산
4. build plan 생성
5. ticker별 input hash 계산
6. 변경 없는 company shard cache hit
7. 변경된 company shard build
8. company shard에서 spine fragment 생성
9. fragment cache hit/miss 결정
10. global_spine.sqlite deterministic merge
11. shard_manifest.json 작성
12. build_summary.json 작성
13. manifest.json v3 작성
14. deep verify
15. current atomic promote
```

정상 runtime 흐름:

```text
1. KRW_ONTOLOGY_ENV 또는 --env로 env 결정
2. publish-root/env/current resolve
3. manifest.json v3 확인
4. indexes/global_spine.sqlite 존재 확인
5. indexes/shard_manifest.json 존재 확인
6. SQLite lightweight open
7. MCP health port open
8. query마다 global spine route
9. 필요한 company shard만 open
10. answer result에 evidence/diagnostics 포함
```

정상 quality 흐름:

```text
1. env/current release 선택
2. manifest format v3 확인
3. global spine path resolve
4. shard manifest path resolve
5. shard 목록 순회
6. shard-local quality_events 집계
7. global locator/catalog와 shard object/document consistency 검사
8. read-only report 출력
9. repair plan은 fingerprint snapshot 저장
10. repair run은 running-root/source만 수정
11. 수정 결과 확인은 새 release force 후 재검사
```

### 30.4 Manifest v3에서 반드시 필요한 필드

`manifest.json`은 release의 public API다. 내부 함수가 어떤 구조를 쓰더라도
runtime과 operator는 manifest를 기준으로 판단한다.

필수 의미:

| 필드 | 의미 | 실패 조건 |
| --- | --- | --- |
| `format` | release format | `krw-ontology-release/v3`가 아니면 실패 |
| `env` | release environment | 기대 env와 다르면 실패 가능 |
| `release_id` | immutable release id | 누락 시 추적 불가 |
| `status` | ready 여부 | `ready`가 아니면 serving 금지 |
| `index_layout` | index topology | `global-spine-and-company-shards`가 아니면 실패 |
| `monolith_required` | runtime monolith 요구 여부 | `false`가 아니면 실패 |
| `indexes.global_spine.path` | global route DB | 누락/escape/없음이면 실패 |
| `indexes.global_spine.sha256` | deep verify digest | mismatch면 publish/promote 실패 |
| `indexes.shard_manifest.path` | shard topology | 누락/escape/없음이면 실패 |
| `indexes.company_shards` | shard set | missing shard면 실패 |

Startup verifier는 위 계약 중 cheap한 것만 확인한다. Deep verifier는 digest와
topology를 더 강하게 확인한다.

### 30.5 Source manifest와 rebuild 범위

Source manifest는 "무엇을 build할 것인가"의 권위 있는 목록이다. filesystem을
매번 broad scan해서 production build target을 추측하지 않는다.

기본 rebuild 규칙:

| 변화 | rebuild 대상 | global spine 영향 |
| --- | --- | --- |
| source artifact byte 변경 | 해당 ticker shard | 해당 fragment 재생성 후 merge |
| 새 ticker 추가 | 새 ticker shard | 새 fragment 추가 후 merge |
| ticker 삭제 | shard manifest에서 제외 | fragment 제외 후 merge |
| source manifest hash 변경 | 영향 ticker 재평가 | merge 재실행 |
| company shard schema version 변경 | 전체 shard | 전체 fragment/merge |
| spine projection version 변경 | shard 재사용 가능, fragment 재생성 | merge 재실행 |
| global spine schema version 변경 | global spine 재생성 | 전체 merge |
| builder version 변경 | version policy에 따라 invalidation | build plan에 이유 기록 |

`force`의 의미:

```text
새 release candidate를 반드시 만든다.
하지만 모든 ticker 계산을 반드시 버린다는 뜻은 아니다.
cache가 유효하면 shard/fragment는 재사용할 수 있다.
```

`--no-cache`의 의미:

```text
cache read를 우회한다.
정확성 확인, cache 오염 의심, schema invalidation 검증 때만 사용한다.
```

### 30.6 Cache 계층

최종 cache는 두 단계가 된다.

1. company shard cache
2. spine fragment cache

회사 단위 cache key에는 최소 아래가 들어가야 한다.

```text
ticker
source artifact content hashes
source manifest version/hash
company shard schema version
company shard builder version
normalization version
validator version if output-affecting
```

fragment cache key에는 최소 아래가 들어가야 한다.

```text
ticker
company shard hash
spine projection version
global key normalization version
cross-company link extraction version
```

cache hit은 결과를 믿는다는 뜻이므로, hit 전에 metadata와 digest를 검증해야 한다.
cache 파일이 존재하는 것만으로 hit 처리하면 안 된다.

### 30.7 Build DAG

build DAG는 "왜 rebuild됐는가"를 설명하기 위한 production artifact다.

권장 node:

```text
SourceManifest
CompanyInputHash:<TICKER>
CompanyShard:<TICKER>
SpineFragment:<TICKER>
GlobalSpineMerge
ShardManifest
ReleaseManifest
StartupVerify
DeepVerify
PromoteCurrent
```

각 node는 다음 값을 가져야 한다.

```text
id
type
inputs
input_hash
output_path
output_hash
cache_status
status
started_at
finished_at
duration_ms
reason
errors
```

`build_progress.jsonl`은 사용자에게 watch로 보여주는 event stream이다.
`build_plan.json`은 재현 가능한 계획이다.
`build_summary.json`은 최종 결과와 cache hit/miss 통계다.

### 30.8 Router와 MCP parity 기준

Monolith 없이 반드시 동작해야 하는 기능:

```text
query
retrieve
compare
trace
chain
discover company topics
company context
quality summary
health/metrics/diagnostics
```

Route 원칙:

```text
ticker가 있으면 해당 shard 우선
no-ticker면 global spine에서 후보 ticker를 고른 뒤 bounded fanout
trace는 global_object_locator로 owning shard를 찾음
chain은 global_chain_index/global_edge_spine으로 경로를 계획함
evidence payload는 필요한 shard에서만 fetch
missing shard는 fallback 없이 error/diagnostic으로 드러냄
```

MCP tool에서 금지:

```text
root 인자
index_path 인자
release_root 인자
global_spine_path 인자
KRW_ONTOLOGY_INDEX_PATH
runtime monolith fallback
filesystem scan으로 shard 추측
```

MCP tool에서 허용:

```text
KRW_ONTOLOGY_RELEASE_ROOT
KRW_ONTOLOGY_GLOBAL_SPINE_PATH
KRW_ONTOLOGY_SHARD_MANIFEST_PATH
configured current release
```

허용 env var는 runtime bootstrap과 test fixture 구성에만 쓰고, tool call 인자로
runtime path를 바꾸지 않는다.

### 30.9 MCP startup 계약

Startup에서 해야 하는 것:

```text
release root resolve
current symlink 여부 확인 if required
manifest.json read
format/status/layout/monolith_required 확인
relative path resolve
global_spine.sqlite exists
shard_manifest.json exists
SQLite lightweight open
runtime payload 구성
port open
```

Startup에서 하면 안 되는 것:

```text
PRAGMA integrity_check on 80GB+ database
full file sha256
모든 shard open
모든 shard count scan
smoke query
ranking quality check
quality consistency full scan
remote upload 검증
repair plan 검증
```

이유:

```text
startup은 serving 가능성을 빠르게 확인하는 단계다.
deep correctness는 release build/publish preflight에서 끝나야 한다.
startup이 deep scan을 하면 launchd health timeout과 운영 rollback 판단이 흔들린다.
```

### 30.10 Quality 상세 계약

Quality는 자동 prod gate가 아니다. 사용자가 원할 때 실행하는 read-only 검사다.
그러나 실행했을 때는 v3 topology 결함을 놓치면 안 된다.

`quality check`가 읽는 것:

```text
<publish-root>/<env>/current/manifest.json
<release>/indexes/global_spine.sqlite
<release>/indexes/shard_manifest.json
<release>/indexes/companies/<TICKER>.sqlite
```

`quality check`가 하면 안 되는 것:

```text
release 수정
current 전환
index rebuild
prod publish
repair 자동 실행
running-root 수정
```

`quality repair plan`:

```text
quality report snapshot을 repair job으로 바꿈
release fingerprint 저장
manifest sha256 저장
source manifest sha256 저장
global spine sha256 저장
```

`quality repair run`:

```text
stale plan이면 기본 실패
--allow-stale-plan은 명시 override
수정 대상은 running-root/source
current release는 수정하지 않음
```

품질 문제를 고친 뒤 다시 `release force`가 필요한 이유:

```text
현재 release는 immutable이다.
repair는 source를 고친다.
MCP와 quality는 release를 읽는다.
따라서 source 수정 결과를 보려면 새 release가 필요하다.
```

남은 quality cleanup:

```text
large release에서 topology consistency full mode와 bounded mode 비용 측정
```

### 30.11 CLI normal path

사용자가 일상적으로 외워야 할 명령은 짧아야 한다.

초기 설정:

```bash
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

평소 build:

```bash
uv run krw-ontology release force
uv run krw-ontology release watch
uv run krw-ontology release status
```

검증:

```bash
uv run krw-ontology release startup-check --env dev
uv run krw-ontology quality check --env dev
```

prod publish:

```bash
uv run krw-ontology prod doctor
uv run krw-ontology prod publish-dev --dry-run
uv run krw-ontology prod publish-dev
```

Debug/CI에서만 긴 option을 쓴다.

```bash
uv run krw-ontology release force \
  --from-root ~/krw-ontology-data-running \
  --releases-root ~/krw-ontology-data/releases \
  --env dev \
  --foreground
```

### 30.12 명령 이름 정리 기준

최종 normal path:

| 목적 | 명령 |
| --- | --- |
| build 계획 확인 | `release plan` |
| dev/current 새로 만들기 | `release force` |
| background 진행 보기 | `release watch` |
| 상태 보기 | `release status` |
| startup lightweight 검증 | `release startup-check` |
| deep 검증 | `release verify` |
| quality 요약 | `quality check` |
| high severity ticker | `quality tickers --severity high` |
| repair plan | `quality repair plan` |
| repair run | `quality repair run` |
| dev to prod | `prod publish-dev` |
| rollback | `prod rollback` |

혼동을 줄이기 위한 원칙:

```text
release deploy/publish-dev/force가 같은 일을 다르게 표현하면 하나로 수렴한다.
prod publish와 release publish-dev는 역할을 분리한다.
publish-root는 local release store다.
prod remote root는 remote deployment store다.
quality는 gate가 아니라 operator command다.
```

### 30.13 Prod publish와 delta upload

Prod publish는 dev/current를 prod/current로 복사하는 단순 복사가 아니다.
검증된 release를 remote candidate로 재구성하고, activation 전에 preflight한다.

정상 흐름:

```text
1. local dev/current resolve
2. manifest v3 hard check
3. monolith_required=false check
4. startup/deep preflight as configured
5. remote current file map 확인
6. changed file만 upload
7. remote candidate 재구성
8. remote candidate startup/deep preflight
9. prod/current atomic activate
10. MCP restart
11. health release_id 확인
12. mismatch/fail이면 rollback
```

Delta upload가 안전한 이유:

```text
remote candidate를 완성한 뒤 검증한다.
prod/current는 candidate 검증 전에는 바뀌지 않는다.
changed files만 전송해도 activation 단위는 항상 release 전체다.
```

### 30.14 실제 데이터 성능 측정 항목

실제 162 ticker 기준으로 기록해야 할 값:

| 측정 | 이유 |
| --- | --- |
| cold build seconds | 최초 전체 build 기준 |
| warm no-change force seconds | cache 효과 |
| single ticker edit seconds | partial rebuild 효과 |
| new ticker add seconds | 신규 ticker 비용 |
| ticker removal seconds | manifest exclusion 비용 |
| global spine bytes | runtime map 크기 |
| company shards bytes | evidence 저장소 크기 |
| release total bytes | prod upload/storage 비용 |
| delta upload bytes | 배포 비용 |
| startup-check seconds | MCP startup 안정성 |
| MCP health seconds | launchd timeout 안전성 |
| ticker query p50/p95 | direct shard 성능 |
| no-ticker query p50/p95 | global spine fanout 성능 |
| cross-company chain p50/p95 | ontology chain 보존 성능 |
| quality check seconds | operator 실행 비용 |

기록은 `verify/performance_baseline.json` 또는 별도 `docs` 표에 남긴다. 측정하지
않은 성능 배수는 문서에 쓰지 않는다.

### 30.15 개발 중 자주 돌릴 검색 audit

Legacy success path가 다시 들어왔는지 확인:

```bash
rg -n "krw-ontology-release/v1|krw-ontology-release/v2|RELEASE_FORMAT_V2|write_release_manifest\\(|build_release_manifest\\(" \
  src tests scripts docs
```

Legacy index path가 runtime에 노출됐는지 확인:

```bash
rg -n "KRW_ONTOLOGY_INDEX_PATH|resolve_agent_index_path|--index-path|args\\.index_path|kwargs\\[\"index_path\"\\]|kwargs\\[\"root\"\\]" \
  src tests scripts
```

Production normal path가 monolith를 여는지 확인:

```bash
rg -n "agent_index\\.sqlite|routing=\"monolith\"|routing='monolith'|routing=\"auto\"|routing='auto'|OntologyStoreRouter" \
  src tests scripts docs
```

MCP tool schema에 forbidden argument가 노출됐는지 확인:

```bash
uv run pytest -q tests/unit/test_mcp_tool_input_aliases.py tests/unit/test_mcp_server.py --maxfail=10
```

검색 결과 해석:

```text
negative assertion, historical docs, explicit low-level monolith test는 허용 가능
production command, MCP runtime, quality normal path, prod publish success path는 허용 불가
```

### 30.16 최소 검증 matrix

작은 변경:

```bash
uv run pytest -q tests/unit/test_cli.py --maxfail=10
uv run pytest -q tests/unit/test_mcp_server.py tests/unit/test_mcp_tool_input_aliases.py --maxfail=10
git diff --check
```

release/build 변경:

```bash
uv run pytest -q \
  tests/unit/test_release_v3.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_spine_schema.py \
  tests/unit/test_agent_index.py \
  --maxfail=10
```

quality 변경:

```bash
uv run pytest -q tests/unit/test_quality.py tests/unit/test_cli.py --maxfail=10
```

MCP/router 변경:

```bash
uv run pytest -q \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_agent_index.py \
  --maxfail=10
```

prod publish/front runtime 변경:

```bash
cd ~/krw-ontology-front
npm run typecheck
npm test -- --run
sh -n scripts/install-mcp-server-launchd.sh
sh -n scripts/start-mcp-server-production.sh
```

front 명령은 실제 파일명이 repo 상태와 다를 수 있으므로, 실행 전 `rg --files scripts`
로 확인한다.

### 30.17 Review checklist

PR 또는 작업 단위 리뷰에서 확인할 항목:

```text
1. v3 manifest만 success path인가?
2. current release를 직접 수정하지 않는가?
3. candidate 실패 시 current가 유지되는가?
4. startup에서 deep scan이 없는가?
5. MCP tool argument schema에 path override가 없는가?
6. quality가 release를 수정하지 않는가?
7. repair가 running-root/source만 수정하는가?
8. shard 누락이 fallback 없이 드러나는가?
9. global spine에 full evidence를 중복 저장하지 않는가?
10. cache key에 schema/projection/builder version이 포함되는가?
11. prod publish가 restart 전에 target을 preflight하는가?
12. rollback이 release_id mismatch를 처리하는가?
13. CLI help가 짧은 normal path를 안내하는가?
14. old docs가 source of truth처럼 남아 있지 않는가?
15. targeted tests와 rg audit가 기록됐는가?
```

### 30.18 남은 작업을 끝내는 순서

남은 작업은 아래 순서로 처리한다.

1. Quality naming cleanup
   - 완료: `Index:` 출력 이름을 `Global spine:`으로 변경
   - 완료: repair plan의 `source_index_path` 명칭을 `global_spine_path`로 정리
   - 완료: shard-local scanner 설명을 명확히 변경
   - 남음: large release에서 topology consistency full mode와 bounded mode 비용 측정

2. Router/MCP parity smoke 확대
   - no-ticker retrieve
   - trace
   - local chain
   - cross-company chain
   - compare
   - topic map
   - missing shard failure
   - current hot swap

3. Builder primitive naming 정리
   - production normal path의 monolith 생성이 불가능한지 확인
   - old builder primitive는 shard schema reuse 또는 offline test primitive로 분리
   - user-facing command/help에서 `agent_index.sqlite` 제거 유지

4. 실제 데이터 baseline
   - 162 ticker cold build
   - warm no-change force
   - single ticker edit
   - new ticker add
   - quality check
   - MCP startup/query

5. Prod dry-run과 rollback rehearsal
   - prod publish dry-run
   - remote target preflight
   - health release id 확인
   - forced mismatch rollback

### 30.19 개발자가 혼동하면 안 되는 용어

| 용어 | 정확한 뜻 |
| --- | --- |
| running-root | source와 pipeline output이 있는 작업 공간 |
| releases-root | env별 immutable release store |
| current | 특정 env의 활성 release를 가리키는 symlink/pointer |
| release force | 새 immutable release 생성을 강제 |
| no-cache | build cache read 우회 |
| global spine | 전체 연결성과 routing을 담은 compact SQLite |
| company shard | 회사별 full evidence/detail SQLite |
| shard manifest | ticker -> shard path/hash/count topology |
| source manifest | build input의 권위 있는 목록과 hash |
| startup check | MCP 시작 전 cheap serveability check |
| deep verify | build/publish 전 expensive correctness check |
| quality check | operator가 원할 때 실행하는 read-only quality scan |
| quality repair | running-root/source를 고치는 repair workflow |
| prod publish-dev | dev/current v3 release를 prod로 publish |

`force`는 "모든 것을 무조건 다시 계산"이 아니다. "새 release를 만든다"가 정확한
뜻이다. cache가 유효하면 계산은 건너뛸 수 있다.

### 30.20 최종 판단

이 개발의 성공 기준은 코드가 돌아가는 것만이 아니다. 최종 기준은 다음이다.

```text
운영자는 짧은 CLI만 사용한다.
release는 항상 immutable이다.
runtime은 항상 v3 release를 읽는다.
MCP startup은 빠르다.
quality는 source repair와 release verification을 혼동하지 않는다.
prod publish는 activation 전에 target을 검증한다.
monolith는 production serving 계약에서 사라진다.
전체 ontology chain은 global spine과 shard route로 보존된다.
```

## 31. 2026-06-12 현재 개발용 상세 실행 문서

이 장은 지금 branch에서 이어서 개발할 때의 실제 작업 기준이다. 위 장들이 최종
architecture와 운영 계약을 설명한다면, 이 장은 "오늘 코드를 고칠 사람이 어떤 순서로
무엇을 확인하고, 어디를 수정하고, 어떤 테스트로 증명할 것인가"에 집중한다.

이 장의 기준일은 2026-06-12이다. 상태가 바뀌면 이 장을 갱신한다. 오래된 상태를
완료처럼 두면 안 된다.

### 31.1 현재 최종 목표

한 문장으로 정의하면 다음이다.

```text
running-root의 canonical source artifact를 입력으로 immutable v3 release를 만들고,
MCP와 quality와 prod publish는 v3 release의 global_spine.sqlite와 company shards만
정상 입력으로 사용한다.
```

정상 path는 다음 하나로 수렴한다.

```text
source 수정
-> release plan
-> release force
-> release watch/status
-> release startup-check
-> quality check when wanted
-> prod publish-dev dry-run
-> prod publish-dev
-> MCP health release_id 확인
```

여기서 "정상 path"는 사용자가 일상적으로 사용하는 명령, front runtime이 호출하는
명령, prod deploy script가 의존하는 계약, MCP startup이 기대하는 release layout을
모두 포함한다.

### 31.2 현재 완료된 핵심 전환

현재 branch 기준 완료된 핵심 전환은 다음이다.

```text
v3 release manifest writer/verifier
non-v3 release hard reject
global_spine.sqlite + shard_manifest.json + company shards release layout
source artifact SQLite primitive boundary
company shard and spine fragment cache
deterministic global spine merge
startup verifier와 deep verifier 분리
MCP startup lightweight preflight
MCP external tool schema에서 root/index_path 제거
MCP internal runtime path override 제거
MCP health/metrics/diagnostics global_spine naming 수렴
QualityReleaseScanner v3 release read-only scan
quality repair plan fingerprint/stale guard
prod publish-dev v3 preflight
prod delta upload/reconstruction unit coverage
front prod launchd pre-restart preflight
front dev/prod runtime global_spine path 수렴
```

검증된 최신 local unit matrix:

```text
tests/unit/test_agent_index.py
tests/unit/test_cli.py
tests/unit/test_mcp_server.py
tests/unit/test_mcp_tool_input_aliases.py
tests/unit/test_quality.py
tests/unit/test_release_v3.py
tests/unit/test_spine_builder.py
tests/unit/test_spine_schema.py
tests/unit/test_ontology_v02.py
tests/unit/test_observability_assets.py
tests/unit/test_observability_config.py

결과: 300 passed, 1 skipped
```

이 결과는 실제 162 ticker full build, 실제 launchd restart, 실제 remote prod
activation을 대신하지 않는다. 실제 데이터 baseline과 end-to-end cutover는 별도 완료
조건이다.

### 31.3 아직 완료라고 부르면 안 되는 영역

아래 영역은 code path가 존재하더라도 final production complete로 보면 안 된다.

| 영역 | 현재 판단 | 완료 조건 |
| --- | --- | --- |
| Router/MCP parity | partial | retrieve, trace, chain, compare, topic map, company context, quality가 모두 monolith 없이 smoke 통과 |
| Quality scale | partial | 대용량 release에서 topology check 비용과 bounded/full mode 기준 확정 |
| Builder naming | partial | source artifact SQLite primitive와 production v3 builder 이름이 더 이상 monolith처럼 보이지 않음 |
| 실제 데이터 baseline | blocked | 162 ticker cold/warm/single/new ticker 측정 기록 |
| Prod cutover | blocked | dry-run, activate, health release_id check, rollback rehearsal 완료 |

중요한 점은 `partial`이 임시 구현을 뜻하지 않는다는 것이다. 최종형으로 가는 범위 안에
있지만 아직 증명되지 않은 상태라는 뜻이다.

### 31.4 CLI normal path와 명령별 의미

운영자가 평소 외울 명령은 아래만으로 충분해야 한다.

초기 설정:

```bash
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

빌드 전 계획 확인:

```bash
uv run krw-ontology release plan
```

의미:

```text
실제로 release를 만들지 않는다.
source_manifest를 기준으로 어떤 ticker가 dirty인지 보여준다.
company shard cache hit/miss 예상치를 보여준다.
spine fragment cache hit/miss 예상치를 보여준다.
build DAG node와 dependency를 보여준다.
```

새 dev/current release 생성:

```bash
uv run krw-ontology release force
```

의미:

```text
새 immutable release 생성을 강제한다.
background worker로 실행된다.
성공하면 <publish-root>/dev/current가 새 release를 가리킨다.
cache가 유효한 shard/fragment는 재사용할 수 있다.
```

`force`가 뜻하지 않는 것:

```text
active current release를 직접 수정한다는 뜻이 아니다.
모든 company shard를 무조건 다시 계산한다는 뜻이 아니다.
cache를 무조건 버린다는 뜻이 아니다.
prod에 바로 올린다는 뜻이 아니다.
```

cache까지 무시하고 싶을 때만:

```bash
uv run krw-ontology release force --no-cache
```

foreground로 직접 보고 싶을 때만:

```bash
uv run krw-ontology release force --foreground
```

상태 확인:

```bash
uv run krw-ontology release status
uv run krw-ontology release watch
```

의미:

```text
status는 current, latest candidate, worker pid, global spine 존재 여부를 요약한다.
status는 latest progress event를 progress_status/progress_count로 요약한다.
watch는 latest candidate의 release-build.log와 build_progress.jsonl을 같이 보여준다.
watch가 hidden cache directory를 고르면 CLI bug다.
```

startup 검증:

```bash
uv run krw-ontology release startup-check --env dev
```

의미:

```text
MCP startup 전에 필요한 cheap serveability만 확인한다.
manifest format/status/layout/monolith_required를 확인한다.
global_spine.sqlite와 shard_manifest.json 존재를 확인한다.
SQLite를 가볍게 열 수 있는지만 확인한다.
full sha256, full integrity_check, all shard scan은 하지 않는다.
```

quality 확인:

```bash
uv run krw-ontology quality check --env dev
```

의미:

```text
dev/current v3 release를 read-only로 검사한다.
release를 고치지 않는다.
index를 rebuild하지 않는다.
prod publish를 막는 자동 gate가 아니다.
operator가 원할 때 실행하는 독립 검사다.
```

prod publish dry-run:

```bash
uv run krw-ontology prod publish-dev --dry-run
```

의미:

```text
dev/current가 v3인지 upload 전에 확인한다.
prod에 무엇이 올라갈지 보여준다.
delta upload 계획을 확인한다.
prod current를 바꾸지 않는다.
MCP를 restart하지 않는다.
```

prod publish:

```bash
uv run krw-ontology prod publish-dev
```

의미:

```text
configured dev/current v3 release를 prod로 배포한다.
기본은 delta upload다.
remote candidate를 재구성하고 검증한 뒤 prod/current를 바꾼다.
reload command와 health_url이 설정되어 있으면 restart와 health release_id 확인까지 한다.
실패하면 기존 prod/current 유지 또는 rollback을 수행해야 한다.
```

긴 option이 필요한 경우:

```bash
uv run krw-ontology release force \
  --from-root ~/krw-ontology-data-running \
  --releases-root ~/krw-ontology-data/releases \
  --env dev \
  --foreground
```

긴 option은 CI, 복구, debug에서만 정상이다. 일상 운영에서 항상 긴 option이
필요하면 CLI 설계가 아직 덜 정리된 것이다.

### 31.5 명령 이름을 혼동하지 않는 기준

현재 CLI에는 기존 작업 흐름에서 생긴 명령들이 남아 있다. 개발자는 아래 기준으로
문서와 help를 정리해야 한다.

| 목적 | normal command | 비고 |
| --- | --- | --- |
| 계획만 보기 | `release plan` | write 없음 |
| dev/current 새로 만들기 | `release force` | background 기본 |
| candidate만 build | `release build --no-promote`에 해당 | CI/debug 중심 |
| local candidate promote | `release promote` | 운영자가 평소 쓰지 않음 |
| startup cheap check | `release startup-check` | MCP startup과 같은 성격 |
| deep verify | `release verify` | build/publish trust path |
| quality read-only 검사 | `quality check` | prod gate 아님 |
| repair plan 생성 | `quality repair plan` | snapshot/fingerprint 저장 |
| repair 실행 | `quality repair run` | running-root/source만 수정 |
| dev/current to prod | `prod publish-dev` | delta 기본 |
| prod rollback | `prod rollback` | remote current 전환 |

`release deploy`, `release publish`, `release publish-dev` 같은 명령이 남아 있더라도,
최종 사용자 문서의 normal path는 `release force`와 `prod publish-dev`로 수렴한다.
호환용 alias가 계속 필요하다면 help에서 "preferred command"를 분명히 알려야 한다.

### 31.6 Release 모듈 개발 계약

대상 파일:

```text
src/krw_ontology/release.py
src/krw_ontology/cli/main.py
tests/unit/test_release_v3.py
tests/unit/test_cli.py
```

Release writer가 해야 하는 일:

```text
candidate directory 생성
source_manifest.json 확정
v3 builder 실행
manifest.json v3 작성
deep verification 실행
성공 시 current atomic promote
실패 시 candidate quarantine 또는 failed 기록
기존 current 보존
```

Release writer가 하면 안 되는 일:

```text
current 내부에서 index를 직접 수정
v1/v2 manifest 생성
monolith agent_index.sqlite를 production 기본 산출물로 생성
verify 실패 후 current를 바꿈
manifest public path에 absolute path 기록
debug monolith를 required output으로 기록
```

Verifier 책임:

```text
verify_release_startup_v3
  MCP startup용 lightweight check
  non-v3 immediate hard reject
  full digest와 all shard scan 없음

verify_release_root
  release build/publish trust path용 deep check
  v3 manifest/layout/digest/topology 검증
  corrupt SQLite를 구조화된 failure로 반환
```

Release 관련 테스트가 증명해야 하는 것:

```text
non-v3는 SQLite open 전에 hard reject
v3 manifest가 global spine/shard manifest/company shard digest를 강제
digest mismatch가 실패로 드러남
corrupt global spine이 exception leak 없이 실패로 드러남
startup check는 deep scan을 하지 않음
build 실패 시 current가 유지됨
force는 새 release id를 만들고 promote함
no-cache는 cache bypass로 기록됨
```

### 31.7 v3 Builder 개발 계약

대상 파일:

```text
src/krw_ontology/agent_index/spine_builder.py
src/krw_ontology/agent_index/spine_schema.py
src/krw_ontology/agent_index/source_artifact_sqlite.py
src/krw_ontology/agent_index/cross_company_links.py
src/krw_ontology/agent_index/builder.py
tests/unit/test_spine_builder.py
tests/unit/test_spine_schema.py
tests/unit/test_agent_index.py
```

Builder의 최종 책임:

```text
source_manifest를 기준으로 build 대상 결정
company shard 생성 또는 cache reuse
spine fragment 생성 또는 cache reuse
global_spine.sqlite deterministic merge
shard_manifest.json 작성
build_plan.json 작성
build_summary.json 작성
build_progress.jsonl 기록
production normal path에서 monolith 미생성
```

`source_artifact_sqlite.py`의 역할:

```text
기존 low-level SQLite primitive를 production v3 이름으로 감싼 boundary
company shard 내부 storage primitive로 사용
public package root에서 legacy monolith API처럼 보이지 않게 격리
```

`builder.py`에 남아 있을 수 있는 것:

```text
company shard 내부 schema primitive
low-level test utility
source artifact SQLite helper의 내부 구현
```

`builder.py`에 남기면 안 되는 것:

```text
normal production release가 agent_index.sqlite를 만들게 하는 public command path
package root에서 build_agent_index 같은 legacy 성공 surface 재노출
production docs에서 monolith builder처럼 설명되는 API
```

Build DAG node 최소 목록:

```text
SourceManifest
CompanyInputHash:<TICKER>
CompanyShard:<TICKER>
SpineFragment:<TICKER>
CrossCompanyLinks
GlobalSpineMerge
ShardManifest
ReleaseManifest
StartupVerify
DeepVerify
PromoteCurrent
```

각 node가 가져야 하는 필드:

```text
id
stage
depends_on
status
cache_hit
cache_key
rebuild_reason
input_hash
output_path
output_hash
started_at
finished_at
duration_ms
error
```

Cache hit 조건:

```text
source artifact hash 일치
company shard schema version 일치
source artifact SQLite builder version 일치
spine projection version 일치
cross-company link algorithm version 일치
output digest 존재 및 검증
```

Cache hit으로 처리하면 안 되는 조건:

```text
파일 이름만 같음
mtime만 같음
manifest 없이 directory만 존재
schema version이 비어 있음
builder version이 바뀌었는데 output을 재사용
digest mismatch를 경고만 하고 진행
```

### 31.8 Router와 MCP parity 개발 계약

대상 파일:

```text
src/krw_ontology/agent_index/spine_router.py
src/krw_ontology/agent_index/router.py
src/krw_ontology/mcp_server/tools.py
src/krw_ontology/mcp_server/server.py
tests/unit/test_mcp_server.py
tests/unit/test_mcp_tool_input_aliases.py
```

Router가 제공해야 하는 정상 기능:

```text
ticker retrieve
no-ticker retrieve
trace
local chain
cross-company chain
compare
topic map
company context
quality summary
missing shard diagnostics
current hot swap
```

모든 user-facing result가 가져야 하는 runtime 설명:

```text
routing.mode
routing.global_spine_path
routing.shard_manifest_path
routing.route_tickers
routing.missing_shards
routing.unknown_tickers
routing.fallback_used 또는 fallback=false
```

정상 route mode 예:

```text
company_shard_direct
global_spine
global_spine_fanout
cross_company_chain
compare_fanout
topic_spine
quality_release_scan
```

Route 원칙:

```text
ticker가 명시되면 ticker normalize 후 shard_manifest에서 required shard 확인
no-ticker면 global_search_fts/global_topic_spine/global_key_stats에서 후보를 고름
trace는 global_object_locator로 owning shard를 찾음
chain은 global_chain_index/global_edge_spine을 먼저 사용
compare는 global_metric_spine/topic/factor key로 비교 대상 정규화
evidence payload는 company shard에서 lazy fetch
missing shard는 fallback 없이 diagnostic 또는 error로 노출
```

MCP tool에서 금지:

```text
root
index_path
release_root
global_spine_path
runtime path override
KRW_ONTOLOGY_INDEX_PATH
monolith fallback
filesystem broad scan fallback
```

MCP tool에서 허용:

```text
operator가 server startup 때 설정한 KRW_ONTOLOGY_RELEASE_ROOT
startup verifier가 설정한 KRW_ONTOLOGY_GLOBAL_SPINE_PATH
startup verifier가 설정한 KRW_ONTOLOGY_SHARD_MANIFEST_PATH
tool input의 ticker/topic/object/query 같은 검색 파라미터
```

현재 완료된 parity smoke:

```text
cross-company chain returns global_spine_neighbors
no-ticker retrieve reports global_spine_fanout route
retrieve payload exposes routing and missing shard diagnostics
external tool schema rejects runtime path override args
```

아직 확장해야 하는 parity smoke:

```text
trace resolves object through global_object_locator and shard
compare reports compare_fanout routing with both ticker shards
topic map reports v3 route and no fallback
company context reports direct shard route
quality tool reports release/global spine/shard topology
missing shard route fails visibly
current hot swap rotates store without stale path leak
```

테스트 작성 기준:

```text
fixture는 v3 release layout으로 만든다.
agent_index.sqlite를 만들지 않는다.
VG fixture를 clone해서 XOM/MSFT 같은 cross-company pair를 만든다.
assert는 result 내용뿐 아니라 routing/fallback/missing_shards를 함께 본다.
fallback이 false임을 확인한다.
unknown ticker와 missing shard는 정상 결과처럼 숨기지 않는다.
```

### 31.9 Quality 개발 계약

대상 파일:

```text
src/krw_ontology/quality/scanner.py
src/krw_ontology/quality/models.py
src/krw_ontology/quality/runner.py
src/krw_ontology/cli/main.py
tests/unit/test_quality.py
tests/unit/test_cli.py
```

Quality check가 읽는 입력:

```text
<publish-root>/<env>/current/manifest.json
<release-root>/source_manifest.json
<release-root>/indexes/global_spine.sqlite
<release-root>/indexes/shard_manifest.json
<release-root>/indexes/companies/<TICKER>.sqlite
```

Quality check가 하면 안 되는 일:

```text
release 수정
current symlink 변경
index rebuild
source repair 자동 실행
prod publish 실행
MCP restart 실행
```

Topology consistency에서 검사해야 하는 항목:

```text
manifest format/layout/monolith_required
global spine exists and schema ok
shard_manifest exists and non-empty
manifest ticker set == shard_manifest ticker set
global_object_locator object count vs shard object count
global_document_catalog document count vs shard document count
edge endpoint object 존재 여부
chain endpoint object 존재 여부
duplicate global object id
shard digest/count mismatch
missing required shard
```

Repair plan fingerprint가 포함해야 하는 값:

```text
release_id
release_format
release_manifest_sha256
source_manifest_sha256
global_spine_sha256
shard_manifest_sha256
shard manifest ticker set/hash
```

Quality 문제를 고친 뒤의 흐름:

```text
quality check에서 문제 확인
-> quality repair plan
-> quality repair run으로 running-root/source 수정
-> release force로 새 immutable release 생성
-> quality check 재실행
```

다시 `release force`가 필요한 이유:

```text
quality repair는 source를 고친다.
MCP와 quality는 release를 읽는다.
active release는 immutable이다.
따라서 source 수정 결과를 runtime에 반영하려면 새 release가 필요하다.
```

남은 quality 작업:

```text
large release full topology check 비용 측정
bounded mode가 필요한지 결정
quality CLI 출력에서 global spine/shard terminology 유지
quality tool MCP payload에 routing/topology 진단 포함
```

### 31.10 MCP startup과 front runtime 계약

대상 파일:

```text
src/krw_ontology/mcp_server/http_server.py
src/krw_ontology/mcp_server/server.py
src/krw_ontology/mcp_server/tools.py
ops/observability/*
krw-ontology-front scripts and MCP client files
```

Startup 순서:

```text
1. release root resolve
2. required current symlink check if configured
3. manifest.json read
4. v3 format/status/layout/monolith_required check
5. global spine relative path resolve
6. shard manifest relative path resolve
7. global spine SQLite lightweight open
8. runtime env vars set
9. MCP app create
10. port open
11. health exposes release_id/global_spine_path/shard topology
```

Startup에서 금지:

```text
PRAGMA integrity_check
full sha256 for 80GB class file
all shard open
all shard count scan
smoke query
ranking quality check
quality topology full scan
remote upload verification
repair plan validation
```

front deploy가 지켜야 하는 순서:

```text
target release preflight
-> target release_id 확인
-> 기존 MCP stop/restart
-> health URL 확인
-> health release_id가 target과 같은지 확인
-> mismatch이면 rollback 또는 failure 처리
```

하면 안 되는 순서:

```text
기존 MCP 먼저 stop
-> 새 MCP가 v1/v2 또는 corrupt release 때문에 startup에서 실패
-> health timeout
```

그 문제를 막는 핵심은 restart 전에 target release가 v3 startup-check를 통과하는지
확인하는 것이다.

### 31.11 Observability 계약

MCP metrics와 alert naming은 `global_spine` 용어로 수렴한다.

정상 용어:

```text
global_spine_present
global_spine_path
global_topic_spine_rows
company_shards_present
shard_manifest_path
```

피해야 할 용어:

```text
index_present
agent_index_present
global_topics_present
index_missing
```

현재 alert naming:

```text
KRWOntologyMCPGlobalSpineMissing
KRW_PROMETHEUS_GLOBAL_SPINE_MISSING_FOR
--global-spine-missing-for
```

Observability 변경 시 같이 고칠 파일:

```text
src/krw_ontology/observability.py
src/krw_ontology/cli/main.py
src/krw_ontology/mcp_server/server.py
ops/observability/README.md
ops/observability/prometheus-krw-ontology-mcp-alerts.yml
ops/observability/grafana-krw-ontology-mcp-dashboard.json
tests/unit/test_observability_assets.py
tests/unit/test_observability_config.py
```

### 31.12 변경 유형별 실제 rebuild 범위

Source artifact 내용만 바뀜:

```text
해당 ticker source hash 변경
-> 해당 company shard rebuild
-> 해당 spine fragment rebuild
-> global spine compact merge
-> 새 release manifest/digest
```

새 ticker 추가:

```text
source_manifest에 ticker 추가
-> 새 company shard build
-> 새 spine fragment build
-> global spine merge
-> shard_manifest ticker set 증가
-> MCP no-ticker fanout 후보에 반영
```

ticker 제거:

```text
source_manifest에서 ticker 제외
-> shard_manifest에서 제외
-> global spine merge에서 fragment 제외
-> 기존 cache 파일은 GC 대상
-> MCP route에서 unknown/missing이 정확히 드러나야 함
```

ontology source schema 변경:

```text
source artifact parser/validator version bump
-> 영향 받는 모든 ticker company shard rebuild
-> fragment rebuild
-> global spine merge
```

company shard schema 변경:

```text
company shard schema version bump
-> 모든 company shard rebuild
-> 모든 fragment rebuild
-> global spine merge
-> quality scanner와 MCP shard reader 동시 갱신
```

spine projection 변경:

```text
company shard 재사용 가능 여부 판단
-> spine fragment version bump
-> 모든 fragment rebuild
-> global spine merge
-> router parity test 확대
```

global chain algorithm 변경:

```text
cross-company link algorithm version bump
-> chain/global key stats 재생성
-> global spine merge
-> chain/compare/no-ticker retrieval smoke 필수
```

### 31.13 실제 데이터 baseline 기록 형식

실제 162 ticker 기준 측정은 완료 조건이다. 측정하지 않은 배수는 문서에 쓰지 않는다.

권장 기록 위치:

```text
<release-root>/verify/performance_baseline.json
docs/v3-production-performance-baseline.md
```

최소 JSON 형태:

```json
{
  "date": "2026-06-12",
  "git_revision": "<sha>",
  "release_id": "<release-id>",
  "ticker_count": 162,
  "artifact_count": 0,
  "cold_build_seconds": 0,
  "warm_no_change_force_seconds": 0,
  "single_ticker_edit_seconds": 0,
  "new_ticker_add_seconds": 0,
  "quality_check_seconds": 0,
  "startup_check_seconds": 0,
  "mcp_health_seconds": 0,
  "ticker_query_p50_ms": 0,
  "ticker_query_p95_ms": 0,
  "no_ticker_query_p50_ms": 0,
  "no_ticker_query_p95_ms": 0,
  "cross_company_chain_p50_ms": 0,
  "cross_company_chain_p95_ms": 0,
  "global_spine_bytes": 0,
  "company_shards_bytes": 0,
  "release_total_bytes": 0,
  "delta_upload_bytes": 0
}
```

측정 기준:

```text
cold build
  cache를 비우거나 별도 cache root에서 전체 build

warm no-change force
  source 변경 없이 release force

single ticker edit
  한 ticker의 source artifact만 의미 있게 변경

new ticker add
  source_manifest에 새 ticker 추가

quality check
  normal quality check full mode

startup check
  release startup-check wall time

MCP health
  process start부터 /health release_id 일치까지
```

### 31.14 개발자가 매 작업마다 돌릴 audit

Release와 manifest legacy success path audit:

```bash
rg -n "krw-ontology-release/v1|krw-ontology-release/v2|RELEASE_FORMAT_V2|write_release_manifest\\(|build_release_manifest\\(" \
  src tests scripts docs
```

허용되는 결과:

```text
negative rejection test
historical-only docs
migration cleanup note
```

허용되지 않는 결과:

```text
normal CLI command
release writer
release verifier success branch
MCP startup success branch
prod publish success branch
```

Runtime path override audit:

```bash
rg -n "KRW_ONTOLOGY_INDEX_PATH|resolve_agent_index_path|--index-path|args\\.index_path|kwargs\\[\"index_path\"\\]|kwargs\\[\"root\"\\]" \
  src tests scripts
```

허용되지 않는 결과:

```text
MCP external tool schema
normal quality command
normal prod publish
runtime store factory
```

Monolith production path audit:

```bash
rg -n "agent_index\\.sqlite|routing=\"monolith\"|routing='monolith'|routing=\"auto\"|routing='auto'|OntologyStoreRouter" \
  src tests scripts docs
```

해석:

```text
low-level historical primitive test는 허용 가능
production release output, MCP runtime, quality normal path, prod publish success path는 불가
```

MCP schema audit:

```bash
uv run pytest -q tests/unit/test_mcp_tool_input_aliases.py tests/unit/test_mcp_server.py --maxfail=10
```

Whitespace/static audit:

```bash
uv run python -m py_compile \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/agent_index/spine_router.py \
  tests/unit/test_mcp_server.py

git diff --check
```

### 31.15 남은 개발의 정확한 순서

남은 작업은 아래 순서로 끝낸다.

1. Router/MCP parity smoke를 완성한다.

```text
compare routing payload
topic map routing payload
company context routing payload
quality tool topology payload
trace locator to shard payload
missing shard failure payload
current hot swap regression
```

이 단계의 완료 기준:

```text
tests/unit/test_mcp_server.py에 각 tool별 v3-only smoke가 있음
모든 smoke에서 agent_index.sqlite 없이 fixture가 동작함
각 result에 routing/missing_shards/unknown_tickers/fallback 상태가 드러남
```

2. Quality scale과 bounded/full mode를 확정한다.

```text
QualityReleaseScanner topology check 비용 측정
large release에서 operator가 기다릴 수 있는 시간 기록
bounded summary와 full audit를 분리할지 결정
repair plan fingerprint에 topology hash 충분성 확인
```

이 단계의 완료 기준:

```text
large release quality_check_seconds 기록
quality output이 global spine/shard terminology만 사용
repair plan stale guard가 release 변경을 잡음
```

3. Builder naming과 package public surface를 마무리한다.

```text
source_artifact_sqlite public naming 유지
low-level builder primitive는 내부 구현으로 제한
package root에서 legacy monolith처럼 보이는 export 제거 유지
docs에서 agent_index.sqlite를 production output처럼 설명하지 않음
```

이 단계의 완료 기준:

```text
public import audit 통과
normal CLI help에서 agent_index.sqlite 없음
tests가 source artifact SQLite와 production v3 release를 혼동하지 않음
```

5. 실제 데이터 baseline을 수집한다.

```text
162 ticker cold build
warm no-change force
single ticker edit
new ticker add
quality check
startup check
MCP query smoke
prod dry-run delta
```

이 단계의 완료 기준:

```text
performance_baseline.json 기록
문서에 측정 숫자 반영
성능 병목이 있으면 correctness를 깨지 않는 최적화 계획 추가
```

6. Prod cutover rehearsal을 수행한다.

```text
prod publish-dev --dry-run
prod publish-dev
health release_id check
forced mismatch rollback test
prod rollback
front launchd restart flow 확인
```

이 단계의 완료 기준:

```text
target preflight가 restart 전에 수행됨
health가 target release_id와 일치함
mismatch 시 rollback됨
기존 healthy MCP를 target 검증 전에 내리지 않음
```

### 31.16 PR 또는 작업 완료 전 최종 체크리스트

코드를 수정한 뒤 아래 질문에 모두 답한다.

```text
1. 이 변경이 v1/v2를 성공 경로로 다시 열었는가?
2. 이 변경이 production release에 agent_index.sqlite를 다시 만들게 하는가?
3. 이 변경이 active current 내부 파일을 수정하는가?
4. 이 변경이 startup path에 full scan, sha256, integrity_check를 추가하는가?
5. 이 변경이 missing shard를 fallback으로 숨기는가?
6. 이 변경이 MCP tool input에 runtime path override를 다시 노출하는가?
7. 이 변경이 quality check를 prod gate로 강제하는가?
8. 이 변경이 quality repair로 release를 직접 고치는가?
9. cache hit 조건에 source hash와 version이 충분히 들어가는가?
10. prod publish가 restart 전에 target v3 preflight를 하는가?
11. health release_id mismatch를 실패로 보는가?
12. CLI normal path가 짧고 일관적인가?
13. 문서와 CLI help가 서로 다른 흐름을 말하지 않는가?
14. 테스트가 monolith 없이 통과하는가?
15. 실제 데이터 baseline이 필요한 변경인데 숫자 없이 완료 처리하지 않았는가?
```

하나라도 "예" 또는 "아니오"가 final contract와 충돌하면 완료가 아니다. 이 경우
코드, 테스트, 문서 중 잘못된 쪽을 수정하고 다시 검증한다.
