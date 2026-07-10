# KRW Ontology v3 최종 production 전환 상세 개발 문서

기준일: 2026-06-12

이 문서는 KRW Ontology의 index build, release, quality, MCP runtime, prod publish를
v3 최종형으로 전환하기 위한 상세 개발 문서다. 목표는 임시 전환이 아니라
production에서 계속 가져갈 최종 구조를 구현하는 것이다.

핵심 결론은 하나다.

```text
production serving = global_spine.sqlite + company shards
production serving != agent_index.sqlite monolith
```

`agent_index.sqlite`를 production 기본 산출물로 유지하지 않는다. 전체 chain과
cross-company 연결성은 `global_spine.sqlite`가 담당하고, 실제 근거와 상세 payload는
회사별 shard가 담당한다.

## 1. 문서 목적

이 문서는 다음 질문에 답한다.

1. v3 최종 architecture가 무엇인지.
2. 왜 monolith를 production 기본에서 제거하는지.
3. 전체 ontology chain을 어떻게 유지하는지.
4. 새 ticker 추가, ticker 수정, ontology schema 변경 시 rebuild 범위가 어떻게 되는지.
5. `force`, `--no-cache`, cache, partial rebuild가 정확히 무엇인지.
6. quality check와 repair plan이 어떤 release와 source를 읽는지.
7. MCP startup에서 무엇을 하면 안 되고 무엇만 해야 하는지.
8. dev release와 prod release를 어떻게 분리하고 publish하는지.
9. CLI를 운영자가 짧고 안전하게 쓰려면 어떤 command contract가 필요한지.
10. 구현 완료 판단 기준과 테스트 matrix가 무엇인지.

이 문서는 v1/v2 호환 계획을 설명하지 않는다. v1/v2는 정상 serving path가 아니다.

## 2. 최종 결정

아래 결정은 구현 선호가 아니라 production 계약이다.

| 항목 | 최종 결정 |
| --- | --- |
| release format | `krw-ontology-release/v3`만 정상 serving 대상 |
| index layout | `global-spine-and-company-shards` |
| global index | `indexes/global_spine.sqlite` |
| company detail store | `indexes/companies/<TICKER>.sqlite` |
| shard manifest | `indexes/shard_manifest.json` |
| production monolith | 기본 생성/배포/serving 금지 |
| debug monolith | 명시 debug/parity 옵션에서만 허용 |
| MCP startup | lightweight serveability check만 수행 |
| deep verification | release build 또는 prod publish preflight에서 수행 |
| quality check | 사용자가 원할 때 실행하는 read-only 검사 |
| quality repair | release 직접 수정 금지, running-root/source 수정 후 새 release 생성 |
| `force` | 새 immutable release 생성을 강제 |
| `--no-cache` | cache read를 무시하고 재계산 |
| prod publish | dev/current v3 release를 preflight 후 prod/current로 atomic activate |
| fallback | missing shard를 monolith로 숨기지 않음 |

## 3. 왜 monolith를 제거하는가

기존 `monolith-and-shards` 구조는 다음과 같다.

```text
indexes/agent_index.sqlite
  전체 ontology graph, evidence, quote, quality, search data

indexes/companies/MSFT.sqlite
indexes/companies/NVDA.sqlite
...
  회사별 상세 데이터
```

이 구조는 안전한 중간 단계로는 쓸 수 있지만 최종 production 구조로는 비용이 크다.

문제는 다음과 같다.

1. 같은 데이터가 monolith와 shard에 중복 저장된다.
2. 90GB급 monolith가 build, verify, upload, backup, rollback 비용을 계속 만든다.
3. MCP startup이 monolith full integrity check나 full sha256을 실행하면 health port가 늦게 열린다.
4. monolith fallback이 남아 있으면 shard router correctness를 끝까지 증명하기 어렵다.
5. ticker 하나만 수정해도 거대한 full DB를 다시 만지게 된다.
6. prod delta upload 효과가 줄어든다.
7. 장애 원인 분석 시 실제 serving source가 spine인지 monolith인지 흐려진다.

v3는 full graph를 없애는 것이 아니다. 역할을 분리한다.

```text
global_spine.sqlite
  전체 연결성
  route planning
  no-ticker discovery
  cross-company chain planning
  object/document locator
  compact search 후보

company shard
  full evidence
  quote
  payload
  quality events
  company-local search
  trace detail
```

비유하면:

```text
global spine = 전체 지하철 노선도
company shard = 각 역 안의 상세 문서와 근거
```

질문이 들어오면 global spine이 어느 회사 shard를 열어야 하는지 결정하고, 필요한
shard만 열어서 상세 근거를 가져온다.

## 4. 전체 ontology chain 보존 방식

사용자의 핵심 요구는 "온톨로지의 장점은 한꺼번에 연결되는 chain"이라는 점이다.
v3는 이 장점을 포기하지 않는다.

global spine은 다음 정보를 보관해야 한다.

| table group | 역할 |
| --- | --- |
| `global_object_locator` | object_id가 어느 ticker/shard에 있는지 찾음 |
| `global_document_catalog` | 문서 위치, ticker, source, period 후보를 찾음 |
| `global_edge_spine` | cross-company chain에 필요한 compact edge |
| `global_factor_spine` | factor 기준 연결 |
| `global_topic_spine` | topic 기준 연결 |
| `global_metric_spine` | metric 기준 연결 |
| `global_entity_spine` | entity 기준 연결 |
| `global_counterparty_spine` | customer, supplier, counterparty 기준 연결 |
| `global_chain_index` | object/ticker 간 chain 후보를 빠르게 찾음 |
| metadata tables | schema, builder version, source manifest hash, counts |

company shard는 다음 정보를 보관해야 한다.

| table group | 역할 |
| --- | --- |
| `documents` | 문서 metadata |
| `objects` | claim, metric, risk, driver, business activity 등 ontology object |
| `edges` | shard-local relation |
| `evidence` 또는 관련 payload table | quote, source span, numeric evidence |
| `quality_events` | quality scanner가 읽는 event |
| search/ranking table | company-local retrieval |

질문 처리 흐름은 다음과 같다.

```text
user question
  -> parse ticker/topic/factor/entity/metric
  -> global_spine에서 route 후보 찾기
  -> 필요한 company shard 목록 결정
  -> shard를 lazy open
  -> shard-local evidence 검색
  -> global chain context와 shard evidence merge
  -> MCP response
```

중요한 점:

```text
full evidence를 global spine에 중복 저장하지 않는다.
chain planning에 필요한 compact relation만 global spine에 둔다.
```

## 5. 최종 release layout

v3 release는 아래 layout을 따른다.

```text
<releases-root>/<env>/<release-id>/
  manifest.json
  source_manifest.json

  companies/
    <TICKER>/
      ...

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

  debug/
    monolith.sqlite
```

`debug/monolith.sqlite`는 production 기본 산출물이 아니다. 명시 debug 또는 offline
parity 검증에서만 생성한다.

production publish 기본 포함 대상:

```text
manifest.json
source_manifest.json
companies/
indexes/global_spine.sqlite
indexes/shard_manifest.json
indexes/companies/*.sqlite
verify/*.json
logs/*.log
```

production publish 기본 제외 대상:

```text
indexes/agent_index.sqlite
debug/monolith.sqlite
mutable build temp files
hidden cache directories
```

## 6. Manifest v3 계약

`manifest.json`은 release의 public contract다. MCP, quality, prod publish, rollback,
web catalog는 이 계약을 기준으로 동작해야 한다.

최소 구조:

```json
{
  "format": "krw-ontology-release/v3",
  "release_id": "20260612_120000",
  "env": "dev",
  "status": "ready",
  "index_layout": "global-spine-and-company-shards",
  "monolith_required": false,
  "global_spine_path": "indexes/global_spine.sqlite",
  "indexes": {
    "global_spine": {
      "path": "indexes/global_spine.sqlite",
      "sha256": "<sha256>",
      "schema_version": "krw-ontology-global-spine/v2",
      "counts": {}
    },
    "company_shards": {
      "dir": "indexes/companies",
      "required": true,
      "count": 0,
      "tickers": {}
    },
    "shard_manifest": {
      "path": "indexes/shard_manifest.json",
      "sha256": "<sha256>"
    },
    "debug_monolith": {
      "path": "debug/monolith.sqlite",
      "present": false,
      "required": false
    }
  },
  "verification": {
    "status": "required",
    "path": "verify/release_verify.json",
    "consistency_report": "verify/consistency_report.json",
    "chain_smoke": "verify/chain_smoke.json",
    "quality_report": "verify/quality_report.json",
    "routing_report": "verify/routing_report.json"
  },
  "builder": {
    "release_builder_version": "release-builder/v3",
    "spine_schema_version": "krw-ontology-global-spine/v2"
  }
}
```

금지:

```text
format == krw-ontology-release/v1
format == krw-ontology-release/v2
index_layout == monolith
index_layout == monolith-and-shards
monolith_required == true
indexes.agent_index만 존재하는 release
global_spine path가 release root 밖을 가리키는 경우
company shard path가 release root 밖을 가리키는 경우
```

## 7. Build DAG

v3 build는 명시적인 DAG로 봐야 한다.

```text
SourceManifest
  -> CompanyShardPlan
  -> CompanyShardBuild
  -> SpineFragmentProjection
  -> GlobalSpineMerge
  -> CrossCompanyLinkGeneration
  -> ShardManifestWrite
  -> BuildSummaryWrite
  -> ReleaseManifestWrite
  -> DeepVerify
  -> AtomicPromote
```

각 노드는 input hash, output path, output digest, counts, cache status를 기록해야 한다.

### 7.1 SourceManifest

입력:

```text
running-root 또는 release candidate root
companies/<TICKER>/*
canonical ontology artifacts
```

출력:

```text
source_manifest.json
source_manifest_hash
```

역할:

1. build input 목록을 결정한다.
2. file system 전체 탐색 결과를 매번 암묵적으로 믿지 않는다.
3. no-change 판단의 기준이 된다.
4. partial rebuild의 dirty company 계산 기준이 된다.

관련 코드:

```text
src/krw_ontology/agent_index/builder.py
  write_source_artifact_manifest

src/krw_ontology/agent_index/spine_builder.py
  build_spine_shard_release_outputs
  plan_spine_shard_release_outputs
```

### 7.2 CompanyShardPlan

입력:

```text
source_manifest.json
builder version
company shard schema version
projection version
cache metadata
```

출력:

```text
indexes/build_plan.json
dirty ticker list
cache hit/miss list
estimated cost
```

역할:

1. 어떤 ticker가 rebuild 대상인지 결정한다.
2. 변경 없는 ticker는 cache reuse 대상으로 표시한다.
3. 새 ticker는 신규 shard build 대상으로 표시한다.
4. schema/builder version이 바뀌면 필요한 범위만 invalidate한다.

관련 코드:

```text
src/krw_ontology/agent_index/builder.py
  _plan_artifact_index_inputs

src/krw_ontology/agent_index/spine_builder.py
  _plan_v3_artifact_inputs
  _company_build_specs
```

### 7.3 CompanyShardBuild

입력:

```text
ticker별 artifact 목록
company shard schema version
cache key
```

출력:

```text
indexes/companies/<TICKER>.sqlite
company shard metadata
company shard digest
```

역할:

1. 각 ticker의 full evidence store를 만든다.
2. shard-local document, object, edge, quality, search data를 넣는다.
3. global spine이 아닌 상세 payload는 shard에 둔다.
4. cache hit이면 기존 shard artifact를 재사용한다.

관련 코드:

```text
src/krw_ontology/agent_index/builder.py
  _build_artifact_index_sqlite

src/krw_ontology/agent_index/spine_builder.py
  build_company_shard_direct
  _build_company_shards_for_release
```

### 7.4 SpineFragmentProjection

입력:

```text
company shard
ticker
source manifest hash
projection version
```

출력:

```text
indexes/fragments/spine/<TICKER>.sqlite
```

역할:

1. shard에서 global routing에 필요한 compact record만 projection한다.
2. full evidence text를 복사하지 않는다.
3. factor/topic/metric/entity/counterparty key를 normalize한다.
4. cross-company chain 후보 생성에 필요한 최소 정보를 남긴다.

관련 코드:

```text
src/krw_ontology/agent_index/spine_builder.py
  _emit_spine_fragments_for_release
  emit_spine_fragment_for_company
```

### 7.5 GlobalSpineMerge

입력:

```text
spine fragments
source_manifest_hash
global spine schema version
```

출력:

```text
indexes/global_spine.sqlite
```

역할:

1. ticker별 spine fragment를 deterministic하게 merge한다.
2. global object locator와 chain index를 만든다.
3. no-ticker query가 열 ticker 후보를 찾을 수 있게 한다.
4. chain/compare/trace가 monolith 없이 동작할 수 있게 한다.

관련 코드:

```text
src/krw_ontology/agent_index/spine_builder.py
  merge_spine_fragments

src/krw_ontology/agent_index/spine_schema.py
  create_global_spine_schema
  verify_global_spine_schema
```

### 7.6 CrossCompanyLinkGeneration

입력:

```text
global factor/topic/metric/entity/counterparty spine
global edge spine
```

출력:

```text
global_chain_index
```

역할:

1. 같은 factor를 공유하는 회사 연결.
2. 같은 topic을 공유하는 회사 연결.
3. 같은 metric이나 counterparty를 공유하는 회사 연결.
4. object 간 chain 후보 생성.

관련 코드:

```text
src/krw_ontology/agent_index/cross_company_links.py
  generate_cross_company_links
```

### 7.7 ShardManifestWrite

입력:

```text
company shard results
shard digests
counts
```

출력:

```text
indexes/shard_manifest.json
```

역할:

1. ticker -> shard path를 권위 있게 기록한다.
2. MCP와 quality가 file system 탐색 없이 shard를 찾게 한다.
3. missing shard를 명확히 실패로 드러낸다.
4. prod delta upload와 remote verification의 기준이 된다.

### 7.8 DeepVerify

입력:

```text
manifest.json
global_spine.sqlite
shard_manifest.json
company shards
verify reports
```

출력:

```text
verify/release_verify.json
verify/consistency_report.json
verify/routing_report.json
verify/chain_smoke.json
verify/quality_report.json
```

역할:

1. release가 v3 계약을 만족하는지 확인한다.
2. global spine schema와 metadata를 확인한다.
3. shard manifest와 실제 shard 파일을 비교한다.
4. digest mismatch를 잡는다.
5. route, chain, trace smoke를 돌린다.

주의:

```text
DeepVerify는 MCP startup에서 실행하지 않는다.
DeepVerify는 build/promote/publish preflight 책임이다.
```

관련 코드:

```text
src/krw_ontology/release.py
  verify_release_root
  write_release_verification_report

src/krw_ontology/agent_index/spine_verify.py
  verify_spine_shard_release
```

## 8. `force`, `--no-cache`, partial rebuild

가장 많이 헷갈리는 지점이다.

### 8.1 `force`

`force`는 "새 release를 만들어라"는 뜻이다.

```bash
uv run krw-ontology release force
```

의미:

```text
running-root를 읽는다.
새 release id를 만든다.
v3 index outputs를 만든다.
manifest v3를 쓴다.
verify를 통과하면 dev/current를 새 release로 바꾼다.
```

`force`가 모든 cache를 버린다는 뜻은 아니다. 변경 없는 ticker는 cache를 쓸 수 있다.

### 8.2 `--no-cache`

`--no-cache`는 "cache read를 믿지 말고 다시 계산하라"는 뜻이다.

```bash
uv run krw-ontology release force --no-cache
```

사용 상황:

1. builder bug가 있었고 cache 자체를 믿기 어려울 때.
2. schema migration 검증을 위해 전부 다시 계산해야 할 때.
3. cache corruption이 의심될 때.

일반 quality 수정 후에는 보통 `--no-cache`가 아니라 `release force`가 맞다.

### 8.3 partial rebuild

partial rebuild의 목표:

```text
변경된 ticker의 shard와 spine fragment만 다시 만들고,
변경 없는 ticker의 shard와 spine fragment는 cache에서 재사용한다.
global_spine.sqlite는 compact merge로 다시 만든다.
```

예:

```text
MSFT source만 수정
  -> MSFT shard rebuild
  -> MSFT spine fragment rebuild
  -> global spine merge
  -> shard manifest update
  -> release verify
```

변경 없는 ticker:

```text
AAPL unchanged
NVDA unchanged
...
  -> cached shard reuse
  -> cached spine fragment reuse
```

### 8.4 새 ticker 추가

새 ticker가 추가되면:

```text
new ticker source artifacts 발견
  -> source_manifest에 새 ticker 추가
  -> 새 ticker shard build
  -> 새 ticker spine fragment build
  -> global spine merge
  -> shard manifest ticker 목록 증가
  -> release verify
```

기존 ticker를 사람이 전부 다시 맞출 필요는 없다. global spine merge가 새 ticker의
factor/topic/metric/entity/counterparty key를 기존 ticker와 연결한다.

### 8.5 ontology schema 변경

schema 변경은 종류별로 rebuild 범위가 다르다.

| 변경 종류 | rebuild 범위 |
| --- | --- |
| source artifact field 추가, 기존 shard schema 영향 없음 | affected ticker shard와 fragment |
| company shard table schema 변경 | 모든 shard rebuild 필요 가능 |
| spine projection logic 변경 | 모든 spine fragment rebuild 필요 가능 |
| global spine schema 변경 | global spine rebuild 필요 |
| cross-company link logic 변경 | global chain index rebuild 필요 |
| ranking/search algorithm만 변경 | 관련 search table 또는 shard rebuild |

중요한 원칙:

```text
schema version과 builder version을 cache key에 넣는다.
version이 바뀐 영역만 정확히 invalidate한다.
```

즉 schema가 새로 생긴다고 해서 항상 사람이 회사별로 수동 정렬하는 것이 아니다.
build DAG와 cache key가 자동으로 재계산 범위를 결정해야 한다.

## 9. CLI 최종 계약

운영자가 일상적으로 쓰는 명령은 짧아야 한다.

초기 설정:

```bash
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
```

dev release 생성:

```bash
uv run krw-ontology release force
```

상태 확인:

```bash
uv run krw-ontology release status
```

로그 watch:

```bash
uv run krw-ontology release watch
```

취소:

```bash
uv run krw-ontology release cancel
```

startup check:

```bash
uv run krw-ontology release startup-check --env dev
```

quality check:

```bash
uv run krw-ontology quality check --env dev
```

prod publish:

```bash
uv run krw-ontology prod publish-dev
```

prod 상태 확인:

```bash
uv run krw-ontology prod status
```

긴 경로 옵션은 emergency/debug/CI에서만 사용한다.

```bash
uv run krw-ontology release force \
  --from-root ~/krw-ontology-data-running \
  --releases-root ~/krw-ontology-data/releases \
  --env dev
```

### 9.1 dev와 prod 분리

dev와 prod는 release root가 다르다.

```text
~/krw-ontology-data/releases/dev/current
~/krw-ontology-data/releases/prod/current
```

흐름:

```text
running-root
  -> release force
  -> dev/current
  -> quality check if wanted
  -> prod publish-dev
  -> prod/current
```

`quality check`는 prod publish gate가 아니다. 사용자가 원할 때 실행하는 독립 명령이다.

### 9.2 background worker와 watch

긴 build는 foreground보다 background가 안전하다.

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
```

요구사항:

1. worker pid를 기록한다.
2. build log를 기록한다.
3. progress JSONL을 기록한다.
4. interrupted candidate를 current로 promote하지 않는다.
5. `release cleanup-interrupted`로 정리할 수 있어야 한다.
6. `release watch`가 hidden cache directory를 release candidate로 오인하면 안 된다.

## 10. Quality 동작 계약

Quality는 release를 읽는 read-only 검사다.

```text
quality check
  -> releases-root/env/current 확인
  -> manifest v3 확인
  -> global_spine.sqlite 확인
  -> shard_manifest.json 확인
  -> company shards 순회
  -> quality report 출력
```

관련 코드:

```text
src/krw_ontology/quality/scanner.py
  QualityReleaseScanner
  QualityShardScanner

src/krw_ontology/quality/models.py
  RepairPlan.global_spine_path

src/krw_ontology/cli/main.py
  quality check
  quality tickers
  quality explain
  quality events
  quality repair plan
  quality repair run
```

### 10.1 Quality check 후 수정 흐름

품질 문제가 발견되면:

```text
quality check
  -> 문제 확인
  -> running-root/source 수정
  -> release force
  -> quality check 재실행
```

release를 직접 수정하지 않는다. current release는 immutable이기 때문이다.

### 10.2 quality repair plan

repair plan은 다음 fingerprint를 가져야 한다.

```text
release_id
release_root
global_spine_path
global_spine_sha256
shard_manifest_sha256
source_manifest_hash
```

repair plan 실행 전에는 plan이 stale인지 확인해야 한다. 다른 release로 current가
바뀌었는데 예전 plan을 실행하면 잘못된 source를 고칠 수 있다.

### 10.3 quality와 cache

quality 자체는 index를 build하지 않는다.

```text
quality check = read-only
quality repair = source 수정
release force = 새 immutable release 생성
```

수정된 ticker만 dirty가 되도록 source manifest와 cache key가 작동해야 한다.
따라서 quality 수정 후에도 전체 full rebuild가 아니라 partial rebuild가 목표다.

## 11. MCP runtime 계약

MCP startup은 빠르게 떠야 한다. startup에서 대형 DB scan을 하면 안 된다.

금지:

```text
PRAGMA integrity_check on large DB
full global_spine sha256 calculation
all shard sha256 calculation
all shard open
full row count scan
smoke query
ranking quality check
quality scan
```

허용:

```text
release root가 current symlink인지 확인
manifest.json 존재 확인
manifest format == krw-ontology-release/v3 확인
env/status/release_id 확인
index_layout == global-spine-and-company-shards 확인
monolith_required == false 확인
global_spine path가 release root 내부인지 확인
global_spine 파일 존재 확인
shard_manifest 파일 존재 확인
SQLite open 가능 여부 확인
global spine metadata 최소 확인
```

관련 코드:

```text
src/krw_ontology/release.py
  verify_release_startup_v3

src/krw_ontology/mcp_server/http_server.py
  server startup preflight

src/krw_ontology/mcp_server/server.py
  health, diagnostics, runtime status

src/krw_ontology/mcp_server/tools.py
  tool runtime store
```

### 11.1 MCP tool path override 금지

외부 MCP tool input schema에 아래 인자가 노출되면 안 된다.

```text
root
index_path
global_spine_path
release_root
manifest_path
```

이유:

1. 사용자가 tool call마다 다른 index를 지정하면 release trust path가 깨진다.
2. MCP가 configured current release가 아닌 임의 파일을 열 수 있다.
3. prod health와 actual serving release가 달라질 수 있다.
4. 보안/운영상 path traversal 위험이 커진다.

MCP는 configured runtime release만 읽어야 한다.

## 12. Router 기능 계약

Router normal path는 `OntologySpineRouter`다.

관련 코드:

```text
src/krw_ontology/agent_index/spine_router.py
  OntologySpineRouter

src/krw_ontology/agent_index/store.py
  open_ontology_store
```

필수 기능:

| 기능 | v3 동작 |
| --- | --- |
| query | ticker/topic/factor/entity를 global spine에서 route하고 shard evidence를 fetch |
| retrieve | query 결과를 MCP context에 맞는 compact payload로 반환 |
| trace | `global_object_locator`로 object 위치를 찾고 shard에서 detail 반환 |
| chain | global chain index로 path 후보를 찾고 필요한 shard detail fetch |
| compare | ticker별 shard 결과를 metric/topic/factor 기준으로 normalize merge |
| topic map | global topic spine과 shard topic evidence를 결합 |
| quality | shard quality data와 release topology quality를 읽음 |
| no-ticker query | global spine에서 candidate ticker를 찾은 뒤 bounded fanout |

금지:

```text
missing shard를 monolith fallback으로 숨김
global spine 없이 file system scan으로 임의 shard 탐색
tool input으로 path override
non-v3 release를 best effort로 serving
```

## 13. prod publish 계약

prod publish는 dev/current v3 release를 prod/current로 안전하게 옮기는 작업이다.

흐름:

```text
dev/current v3 release
  -> local preflight
  -> bundle or delta bundle 생성
  -> remote incoming upload
  -> remote candidate materialize
  -> remote v3 preflight
  -> prod/current atomic symlink switch
  -> reload command
  -> health check
  -> 실패 시 rollback
```

관련 코드:

```text
src/krw_ontology/cli/main.py
  prod publish
  prod publish-dev
  _publish_prod_root
  _build_prod_release_delta_bundle
  _prod_activation_script
  _prod_rollback_script
```

### 13.1 delta upload

delta upload는 전체 release를 매번 올리지 않고 changed/removed file list만 업로드한다.

전제:

1. remote current가 정상 v3 release여야 한다.
2. local target release와 remote current file map을 비교할 수 있어야 한다.
3. delta manifest가 sha256을 가진다.
4. remote materialization 후 v3 preflight를 통과해야 한다.
5. activation은 atomic이어야 한다.

delta upload가 실패하면 full bundle fallback 또는 publish 실패로 처리한다. 실패한
candidate를 current로 바꾸면 안 된다.

### 13.2 MCP restart 전 preflight

절대 순서:

```text
target release preflight 성공
  -> current switch
  -> MCP restart
```

금지:

```text
기존 healthy MCP stop
  -> 그 다음 target release 검증
```

이 순서가 되면 target이 invalid일 때 정상 MCP까지 내려간다.

## 14. 현재 branch 기준 구현 상태

현재 작업트리 기준으로 다음 축은 구현되어 있거나 targeted test를 통과했다.

| 영역 | 상태 |
| --- | --- |
| v3 global spine schema | implemented, verified |
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
| non-v3 manifest hard reject | implemented, verified |
| digest 계약 분리 | implemented, verified |
| release force background worker | implemented, verified |
| release plan/status/watch/cancel | implemented, verified |
| v3 quality release scanner | implemented, verified |
| quality repair fingerprint/stale guard | implemented, verified |
| prod v3 preflight | implemented, unit coverage exists |
| prod delta bundle/reconstruction | implemented, unit coverage exists |
| MCP tool schema path override 제거 | implemented, verified |
| MCP runtime path override 제거 | implemented, verified |
| MCP health/runtime payload `global_spine_path` 수렴 | implemented, verified |
| default ontology store routing v3 spine-only 수렴 | implemented, verified |
| legacy `KRW_ONTOLOGY_INDEX_PATH` resolver 제거 | implemented, verified |
| quality CLI/report naming `global_spine_path` 수렴 | implemented, verified |
| `agent_index` package public surface legacy builder export 제거 | implemented, verified |

아직 final completion 전에 더 강화해야 하는 영역:

| 영역 | 상태 | 완료 기준 |
| --- | --- | --- |
| router API parity | partial | query/retrieve/trace/chain/compare/topic/quality end-to-end smoke 확대 |
| missing shard behavior | partial | fallback 없이 구조화된 failure 반환/검증 |
| quality scale | partial | 대용량 shard 전체 검사 비용 측정 |
| release integration smoke | partial | 실제 dev v3 release에서 router/quality/MCP smoke |
| build progress | partial | DAG node별 progress JSONL 확장 |
| real production baseline | blocked by actual run | 162 ticker cold/warm/single/new ticker 측정 |
| end-to-end cutover | blocked by actual run | dev build, prod publish, MCP health, rollback 실측 |

## 15. 구현 작업 패키지

### WP-1 Router/MCP parity 완성

목표:

```text
monolith 없이 MCP tool 전체가 v3 release만으로 동작한다.
```

작업:

1. `OntologySpineRouter.query` smoke 확대.
2. `retrieve_tool` v3-only runtime smoke.
3. `trace_tool` object locator 기반 smoke.
4. `chain_tool` global chain index 기반 smoke.
5. `compare_tool` multi-ticker shard fanout smoke.
6. `topic_map_tool` global topic spine smoke.
7. `quality_tool` release/shard quality smoke.
8. missing shard case에서 fallback이 없는지 테스트.
9. `current` hot swap 후 runtime cache가 새 release를 보는지 테스트.

완료 기준:

```text
tests/unit/test_mcp_server.py
tests/unit/test_mcp_tool_input_aliases.py
tests/unit/test_agent_index.py
```

에서 v3-only parity test가 통과한다.

### WP-2 Quality topology와 repair flow 완성

목표:

```text
quality가 global spine + shard topology를 읽고, repair는 source만 수정한다.
```

작업:

1. `QualityReleaseScanner.scan`이 shard missing을 명확히 report.
2. topology consistency 검사 확대.
3. quality repair plan fingerprint 강화.
4. stale plan 실행 차단.
5. quality 수정 후 partial rebuild smoke 작성.

완료 기준:

```text
quality check --env dev
quality repair plan --env dev
quality repair run
release force
quality check --env dev
```

흐름이 release 직접 수정 없이 동작한다.

### WP-3 Build DAG progress와 cache observability

목표:

```text
왜 rebuild됐는지, 왜 skip됐는지, 얼마나 걸렸는지 알 수 있다.
```

작업:

1. DAG node별 progress JSONL record 추가.
2. ticker별 cache hit/miss reason 기록.
3. source_manifest_hash, builder_version, schema_version 기록.
4. shard build seconds, fragment projection seconds, merge seconds 기록.
5. `release status`와 `release watch`에서 핵심 진행률 표시.

완료 기준:

```text
release status
release watch
index cache status
```

에서 operator가 병목을 이해할 수 있다.

### WP-4 prod publish와 remote activation hardening

목표:

```text
prod publish가 invalid target으로 MCP를 내리지 않는다.
```

작업:

1. local v3 preflight hard reject 유지.
2. remote v3 preflight hard reject 유지.
3. delta manifest 검증 강화.
4. remote current release id와 health release id mismatch rollback 테스트.
5. prod publish dry-run 결과를 operator 친화적으로 출력.

완료 기준:

```text
uv run krw-ontology prod publish-dev --dry-run
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

가 v3 release만 대상으로 동작한다.

### WP-5 실제 162 ticker baseline 측정

목표:

```text
최종 구조의 성능을 추정이 아니라 숫자로 판단한다.
```

측정 항목:

| 항목 | 의미 |
| --- | --- |
| cold force seconds | cache 없는 전체 v3 build |
| warm no-change force seconds | 변경 없는 release force |
| single ticker edit seconds | ticker 하나 수정 후 release force |
| new ticker seconds | 새 ticker 추가 후 release force |
| global spine merge seconds | fragment merge 비용 |
| largest shard size | 가장 큰 company shard |
| total shard bytes | shard 전체 크기 |
| global spine bytes | global spine 크기 |
| prod delta changed bytes | delta upload 크기 |
| MCP startup seconds | health port가 뜨는 시간 |
| first query p50/p95 | MCP query 응답 |
| trace p50/p95 | object trace 응답 |
| chain p50/p95 | cross-company chain 응답 |

완료 기준:

```text
docs 또는 verify/performance_baseline.json에 숫자를 남긴다.
```

## 16. 테스트 matrix

최소 unit/integration test:

```bash
uv run pytest -q \
  tests/unit/test_agent_index.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_spine_schema.py \
  tests/unit/test_release_v3.py \
  tests/unit/test_cli.py \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_input_aliases.py \
  tests/unit/test_quality.py \
  tests/unit/test_ontology_v02.py \
  --maxfail=10
```

문법 검사:

```bash
python3 -m py_compile \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_router.py \
  src/krw_ontology/agent_index/spine_schema.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/release.py \
  src/krw_ontology/cli/main.py \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/mcp_server/server.py \
  src/krw_ontology/mcp_server/http_server.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/quality/models.py
```

diff whitespace:

```bash
git diff --check
```

legacy public surface audit:

```bash
uv run python - <<'PY'
import krw_ontology.agent_index as a
legacy = [
    "build_agent_index",
    "plan_agent_index",
    "build_index_shards",
    "verify_index_shards",
    "compile_artifact_fragment",
    "merge_fragments",
]
print([name for name in legacy if hasattr(a, name)])
PY
```

정상 결과:

```text
[]
```

MCP schema audit:

```bash
rg -n "\"root\"|\"index_path\"|\"global_spine_path\"|\"release_root\"" \
  src/krw_ontology/mcp_server/tools.py \
  tests/unit/test_mcp_tool_input_aliases.py
```

정상 기대:

```text
외부 tool input schema에는 path override 인자가 없어야 한다.
테스트에서 forbidden argument negative case만 허용된다.
```

## 17. 운영 runbook

### 17.1 최초 설정

```bash
cd ~/krw-ontology

uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
```

왜 필요한가:

```text
매번 긴 --from-root, --releases-root를 치지 않기 위해서다.
CLI가 기본 입력/출력 위치를 기억한다.
```

### 17.2 dev release 만들기

```bash
uv run krw-ontology release force
```

왜 필요한가:

```text
running-root의 최신 source를 읽어 새 immutable dev release를 만든다.
v3 global spine과 company shards를 만든다.
성공하면 dev/current가 새 release를 가리킨다.
```

### 17.3 진행 확인

```bash
uv run krw-ontology release status
uv run krw-ontology release watch
```

왜 필요한가:

```text
긴 build가 어디까지 갔는지, 어떤 release id가 작업 중인지 확인한다.
```

### 17.4 startup check

```bash
uv run krw-ontology release startup-check --env dev
```

왜 필요한가:

```text
MCP가 빠르게 열 수 있는 최소 release 조건을 확인한다.
deep verification이 아니라 startup-safe check다.
```

### 17.5 quality check

```bash
uv run krw-ontology quality check --env dev
```

왜 필요한가:

```text
사용자가 원할 때 품질 문제를 확인한다.
prod publish gate는 아니다.
```

### 17.6 prod publish

```bash
uv run krw-ontology prod publish-dev
```

왜 필요한가:

```text
검증된 dev/current를 prod/current로 옮긴다.
기본은 delta upload다.
target release를 먼저 검증한 뒤 MCP restart가 이어진다.
```

### 17.7 rollback

```bash
uv run krw-ontology prod rollback <release-id>
```

왜 필요한가:

```text
prod/current를 이전 정상 release로 되돌린다.
rollback 대상도 v3 preflight를 통과해야 한다.
```

## 18. 장애 처리 기준

### 18.1 release force가 오래 걸림

확인:

```bash
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology index cache status
```

판단:

```text
cache miss가 많은가?
특정 ticker shard build가 오래 걸리는가?
global spine merge가 오래 걸리는가?
disk IO가 병목인가?
```

대응:

```text
정상 build면 기다린다.
worker가 죽었으면 cleanup-interrupted 후 다시 force.
cache corruption이면 --no-cache.
```

### 18.2 watch가 이상한 release를 봄

증상:

```text
Log does not exist: .../.index_fragment_cache/logs/publish-dev.log
```

원인:

```text
watch가 hidden cache directory를 candidate release처럼 잡은 CLI bug일 수 있다.
```

대응:

```text
release status로 실제 worker release_id 확인
release watch <release-id>처럼 명시
hidden directory를 candidate로 잡지 않도록 코드 수정/테스트
```

### 18.3 MCP health가 늦게 뜸

확인:

```bash
uv run krw-ontology release startup-check --env prod
curl http://127.0.0.1:8765/health
```

원인 후보:

```text
startup에서 deep verification 실행
full sha256 계산
SQLite integrity_check 실행
v1/v2 manifest hard reject
global_spine missing
shard_manifest missing
```

대응:

```text
startup path를 lightweight verifier로 고정
prod/current를 v3 release로 재생성/publish
MCP restart 전 preflight 순서 확인
```

### 18.4 quality 수정 후 결과가 안 바뀜

원인:

```text
quality는 release를 읽는다.
source를 고친 뒤 새 release를 만들지 않으면 current release는 그대로다.
```

대응:

```bash
uv run krw-ontology release force
uv run krw-ontology quality check --env dev
```

### 18.5 missing shard

증상:

```text
shard_manifest에는 ticker가 있는데 indexes/companies/<TICKER>.sqlite가 없음
```

대응:

```text
fallback으로 숨기지 않는다.
candidate release를 실패 처리한다.
release force로 새 release를 만든다.
```

## 19. 코드 소유권 지도

| 영역 | 파일 |
| --- | --- |
| v3 build DAG | `src/krw_ontology/agent_index/spine_builder.py` |
| global spine schema | `src/krw_ontology/agent_index/spine_schema.py` |
| global spine verification | `src/krw_ontology/agent_index/spine_verify.py` |
| cross-company links | `src/krw_ontology/agent_index/cross_company_links.py` |
| legacy artifact shard primitive | `src/krw_ontology/agent_index/builder.py` |
| v3 router | `src/krw_ontology/agent_index/spine_router.py` |
| store entry point | `src/krw_ontology/agent_index/store.py` |
| release manifest/verify/promote | `src/krw_ontology/release.py` |
| CLI | `src/krw_ontology/cli/main.py` |
| quality scanner | `src/krw_ontology/quality/scanner.py` |
| quality models | `src/krw_ontology/quality/models.py` |
| MCP startup | `src/krw_ontology/mcp_server/http_server.py` |
| MCP health | `src/krw_ontology/mcp_server/server.py` |
| MCP tools | `src/krw_ontology/mcp_server/tools.py` |
| config paths | `src/krw_ontology/config/paths.py` |

## 20. Review checklist

PR review에서 아래 질문에 모두 답해야 한다.

1. 이 변경이 v3 release만 정상 path로 취급하는가?
2. `agent_index.sqlite` production dependency를 새로 만들지 않았는가?
3. `current` release 내부를 수정하지 않는가?
4. 실패 시 기존 current가 유지되는가?
5. path가 release root 밖으로 escape할 수 없는가?
6. MCP startup에서 대형 scan이 실행되지 않는가?
7. MCP tool schema에 path override가 노출되지 않는가?
8. quality가 release를 read-only로 읽는가?
9. quality repair가 source만 수정하는가?
10. `force`와 `--no-cache` 의미가 섞이지 않았는가?
11. cache key에 schema/builder/projection version이 들어가는가?
12. missing shard가 fallback으로 숨겨지지 않는가?
13. prod publish가 restart 전에 target release를 preflight하는가?
14. delta upload가 remote에서 다시 검증되는가?
15. rollback 대상도 v3 preflight를 통과하는가?
16. 테스트가 normal path와 negative path를 모두 덮는가?
17. CLI help가 운영자에게 짧은 명령을 안내하는가?
18. docs가 v1/v2 호환을 정상 path처럼 설명하지 않는가?

## 21. Definition of done

v3 전환 완료는 아래를 모두 만족해야 한다.

1. `release force`가 v3 release만 만든다.
2. v3 release에는 production 기본으로 `indexes/agent_index.sqlite`가 없다.
3. `manifest.json`은 v3 format과 `global-spine-and-company-shards` layout을 가진다.
4. `global_spine.sqlite`와 `shard_manifest.json`이 필수다.
5. company shard가 manifest에 기록된 모든 ticker에 존재한다.
6. deep verify가 global spine schema, shard manifest, shard existence, digest를 확인한다.
7. MCP startup은 lightweight check만 실행한다.
8. MCP health가 `release_id`, `env`, `global_spine_path`, `company_shards_present`를 노출한다.
9. MCP tool external schema에 `root`, `index_path`, `global_spine_path`, `release_root` override가 없다.
10. query/retrieve/trace/chain/compare/topic/quality가 monolith 없이 동작한다.
11. quality check가 release root의 global spine과 shards를 읽는다.
12. quality repair plan이 release fingerprint와 stale guard를 가진다.
13. source 수정 후 `release force`가 partial rebuild를 수행한다.
14. 새 ticker 추가가 기존 ticker 수동 재정렬 없이 global spine merge로 반영된다.
15. ontology schema 변경 시 versioned cache invalidation이 작동한다.
16. prod publish가 v3 preflight 후 activate한다.
17. prod delta upload가 changed/removed file을 검증한다.
18. prod rollback이 v3 release만 대상으로 동작한다.
19. hidden cache directory가 release candidate로 선택되지 않는다.
20. 162 ticker real build baseline이 기록된다.
21. MCP startup과 first query baseline이 기록된다.
22. targeted unit/integration tests가 통과한다.
23. `git diff --check`가 통과한다.
24. legacy public surface audit가 통과한다.

## 22. 초등학생도 이해하는 요약

예전 방식:

```text
큰 책 한 권을 통째로 만들고,
회사별 작은 책도 또 만들었다.
```

문제:

```text
같은 내용이 두 번 들어가서 무겁고 느리다.
```

새 방식:

```text
전체 지도 한 장을 만든다.
회사별 자세한 책은 따로 둔다.
```

질문이 오면:

```text
먼저 지도에서 어디로 가야 하는지 찾는다.
그 다음 필요한 회사 책만 연다.
```

`force`는:

```text
새 책 세트를 만드는 버튼이다.
```

`--no-cache`는:

```text
전에 만들어 둔 재료를 믿지 말고 처음부터 다시 만들라는 버튼이다.
```

quality check는:

```text
새 책 세트를 검사하는 일이다.
책을 직접 고치지는 않는다.
```

quality 문제를 고치면:

```text
원본을 고친다.
새 책 세트를 다시 만든다.
다시 검사한다.
```

MCP startup은:

```text
책 전체를 다 읽고 시작하면 안 된다.
책 세트가 맞는지만 빠르게 확인하고 문을 열어야 한다.
```

prod publish는:

```text
dev에서 만든 좋은 책 세트를 prod 책장으로 옮기는 일이다.
옮기기 전에 책 세트가 맞는지 먼저 검사한다.
```

## 23. 다음 개발자가 바로 할 일

우선순위는 다음이다.

1. Router/MCP parity smoke 확대.
2. missing shard negative test 추가.
3. current hot swap test 추가.
4. quality topology 검사 비용과 missing shard report 강화.
5. release watch가 hidden cache directory를 후보로 잡지 않는지 test 보강.
6. 실제 162 ticker dev `release force` baseline 측정.
7. dev/current v3 release로 MCP startup/query smoke.
8. prod publish dry-run.
9. prod publish 실제 실행.
10. rollback smoke.

작업 전 실행:

```bash
git status --short
```

작업 중 원칙:

```text
사용자 변경을 revert하지 않는다.
current release를 직접 수정하지 않는다.
v1/v2 호환을 정상 path로 되살리지 않는다.
임시 monolith fallback을 만들지 않는다.
```
