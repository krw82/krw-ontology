# KRW Ontology v3 final production development specification

기준일: 2026-06-12

> 최종 결정, 현재 구현 상태, 남은 차단 항목, 운영 runbook의 통합 기준은
> [`v3-final-production-master-development-guide.md`](v3-final-production-master-development-guide.md)다.
> 이 문서는 세부 구현 명세로 사용한다.

이 문서는 `krw-ontology`를 v1/v2 호환 없이 v3 최종형으로 전환하기 위한
상세 개발 문서다. 목적은 단순한 방향 제안이 아니라, 실제 구현자와 리뷰어가
코드를 고치고, 테스트하고, 운영 반영 여부를 판단할 수 있게 만드는 것이다.

관련 문서의 역할은 다음과 같다.

| 문서 | 역할 |
| --- | --- |
| `global-spine-company-shards-v3-development-guide.md` | v3 schema, build DAG, router 상세 계약 |
| `v3-final-production-conversion-development-plan.md` | 단계별 실행 계획과 진행 체크리스트 |
| `v3-production-implementation-handoff.md` | 파일별 handoff, runbook, 리뷰 체크리스트 |
| 이 문서 | 최종 구현 명세, 작업 패키지, 검증 기준, 운영 판단 기준 |

이 문서에서 말하는 production은 `prod` 환경만 뜻하지 않는다. `dev`, `staging`,
`prod` 모두 같은 release 계약, 같은 index layout, 같은 verification 원칙을
가져야 한다.

## 1. 최종 결론

최종형은 아래 하나다.

```text
source/running-root
  -> immutable v3 release
  -> indexes/global_spine.sqlite
  -> indexes/companies/<TICKER>.sqlite
  -> prod publish
  -> MCP runtime serving
```

production 기본에서 제거할 것:

```text
indexes/agent_index.sqlite
monolith-and-shards layout
v1/v2 release serving compatibility
MCP startup deep verification
startup PRAGMA integrity_check
startup full sha256 scan
quality scanner requiring --index-path
prod publish accepting v1/v2
```

정상 user-facing path에서 남겨야 할 명령:

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology quality check
uv run krw-ontology prod publish-dev
```

긴 경로 옵션은 emergency, CI, debug에서만 필요해야 한다.

## 2. 왜 이 구조가 최선인가

기존 `monolith-and-shards` 구조는 안정화 중간 구조로는 안전하지만, 장기
production 최선은 아니다.

기존 구조:

```text
agent_index.sqlite
  전체 ontology graph, evidence, quality, routing data

companies/<TICKER>.sqlite
  회사별 데이터 복사본
```

문제:

1. 같은 object, edge, document, quality data가 monolith와 shard에 중복 저장된다.
2. 90GB급 monolith가 force build, verify, upload, startup 병목이 된다.
3. MCP startup이 full integrity check나 sha256을 수행하면 health port가 늦게 열린다.
4. monolith fallback이 남아 있으면 router가 spine/shard correctness를 끝까지 증명하지
   못한다.
5. 새 ticker 또는 ticker 하나 수정에도 전체 monolith 비용이 남는다.

v3 구조:

```text
global_spine.sqlite
  전체 연결성, routing, no-ticker discovery, chain planning, locator

indexes/companies/<TICKER>.sqlite
  full evidence, quote, payload, trace detail

debug/monolith.sqlite
  명시 debug 옵션에서만 생성하는 offline parity artifact
```

이 구조의 핵심은 "full graph를 없앤다"가 아니다. full graph의 역할을 둘로 나눈다.

```text
global spine = 전체를 연결하는 지도
company shard = 실제 증거가 있는 저장소
```

따라서 사용자의 우려인 "온톨로지의 장점은 한꺼번에 연결되는 chain"은 유지된다.
다만 그 chain을 90GB monolith에 의존하지 않고, compact global spine에서 계획한 뒤
필요한 shard만 열어 증거를 가져오는 방식으로 바꾼다.

## 3. 절대 불변식

아래 규칙은 구현 선호가 아니라 production 계약이다. PR 리뷰에서 하나라도 깨지면
merge하면 안 된다.

1. `current`는 immutable release pointer다.
2. 활성 `current` release 내부 파일은 수정하지 않는다.
3. build 실패, verify 실패, publish 실패 시 기존 `current`는 유지된다.
4. production release format은 `krw-ontology-release/v3`만 허용한다.
5. production index layout은 `global-spine-and-company-shards`만 허용한다.
6. `monolith_required=true` release는 production path에서 실패해야 한다.
7. `force`는 새 release 생성을 강제한다.
8. `--no-cache`만 cache read 우회를 의미한다.
9. MCP startup은 lightweight verification만 수행한다.
10. deep verification은 release build 또는 publish preflight에서 수행한다.
11. quality check는 gate가 아니다. 사용자가 원할 때 실행하는 read-only 검사다.
12. quality repair는 running-root/source만 수정한다.
13. quality repair 후 결과를 보려면 새 release가 필요하다.
14. shard 누락은 fallback으로 숨기지 않고 실패로 드러낸다.
15. manifest에 기록되는 index path는 release root 내부 상대 경로여야 한다.
16. full evidence text를 global spine에 중복 저장하지 않는다.
17. prod publish는 MCP restart보다 먼저 target release를 preflight한다.
18. v1/v2 release를 정상 serving 대상으로 받아들이는 user-facing path는 없다.

## 4. 최종 release layout

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

`debug/monolith.sqlite`는 production 기본 산출물이 아니다. 명시 debug/parity 옵션이
있을 때만 생성한다.

## 5. Manifest v3 계약

`manifest.json`은 release의 public contract다.

최소 구조:

```json
{
  "format": "krw-ontology-release/v3",
  "env": "dev",
  "release_id": "20260612_120000",
  "status": "ready",
  "index_layout": "global-spine-and-company-shards",
  "monolith_required": false,
  "builder": {
    "release_builder_version": "v3",
    "spine_schema_version": "krw-ontology-global-spine/v1",
    "company_shard_schema_version": "krw-ontology-company-shard/v1",
    "chain_index_version": "krw-ontology-chain-index/v1"
  },
  "indexes": {
    "global_spine": {
      "path": "indexes/global_spine.sqlite",
      "required": true,
      "sha256": "<sha256>",
      "schema_version": "krw-ontology-global-spine/v1",
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
      "required": true,
      "sha256": "<sha256>"
    }
  },
  "verification": {
    "release_verify": "verify/release_verify.json",
    "quality_report": "verify/quality_report.json"
  }
}
```

Verifier가 반드시 거절할 조건:

| 조건 | 실패 이유 |
| --- | --- |
| `format != krw-ontology-release/v3` | v1/v2 serving 차단 |
| `status != ready` | 준비되지 않은 release |
| `index_layout != global-spine-and-company-shards` | monolith layout 차단 |
| `monolith_required != false` | runtime fallback 차단 |
| `indexes.global_spine.path` 누락 | routing 불가 |
| `indexes.shard_manifest.path` 누락 | shard topology 불명 |
| `indexes.company_shards.dir` 누락 | evidence fetch 불가 |
| absolute path | portable release 위반 |
| path escaping release root | 보안 및 correctness 위반 |
| shard count mismatch | topology 불일치 |
| required shard missing | serving 불가 |
| `debug.monolith` required | production bundle 오염 |

## 6. Source manifest 계약

`source_manifest.json`은 build input의 source of truth다.

역할:

```text
1. 어떤 회사/ticker가 build 대상인지 결정한다.
2. 각 source artifact의 content hash를 기록한다.
3. build cache key의 입력이 된다.
4. rebuild/skip 이유를 설명할 근거가 된다.
```

금지:

```text
filesystem 전체를 매번 ad hoc scan해서 build input을 암묵적으로 결정
hidden cache directory를 source로 오인
current release나 failed release를 source input으로 섞기
회사별 source hash 없이 shard cache hit 인정
```

source manifest에는 최소한 아래가 필요하다.

```json
{
  "format": "krw-ontology-source-manifest/v1",
  "root": ".",
  "created_at": "<iso8601>",
  "companies": {
    "MSFT": {
      "ticker": "MSFT",
      "company_source_hash": "<hash>",
      "artifacts": [
        {
          "path": "companies/MSFT/ontology/...",
          "sha256": "<hash>",
          "kind": "ontology-artifact"
        }
      ]
    }
  }
}
```

## 7. Build DAG

v3 builder는 선형 shell script가 아니라 DAG다.

필수 node:

| node | 입력 | 출력 | cache 가능 |
| --- | --- | --- | --- |
| `SourceManifest` | source artifacts | `source_manifest.json` | no |
| `BuildPlan` | source manifest, versions, config | `indexes/build_plan.json` | no |
| `CompanyShard:<TICKER>` | ticker source artifacts | `indexes/companies/<TICKER>.sqlite` | yes |
| `SpineFragment:<TICKER>` | company shard | `indexes/fragments/spine/<TICKER>.sqlite` | yes |
| `GlobalSpineMerge` | all spine fragments | `indexes/global_spine.sqlite` | conditional |
| `CrossCompanyLinks` | spine key tables | `global_chain_index` rows | conditional |
| `ShardManifest` | shard outputs | `indexes/shard_manifest.json` | no |
| `ReleaseVerify` | manifest draft, spine, shards | `verify/*.json` | no |
| `ManifestWrite` | verified outputs | `manifest.json` | no |
| `PromoteCurrent` | verified release | `current` symlink | no |

Node record 예시:

```json
{
  "id": "CompanyShard:MSFT",
  "stage": "company_shard",
  "ticker": "MSFT",
  "status": "rebuilt",
  "reason": "company_source_hash_changed",
  "input_hash": "<hash>",
  "output_hash": "<hash>",
  "builder_version": "global-spine-builder/v1",
  "schema_version": "krw-ontology-company-shard/v1",
  "started_at": "<iso8601>",
  "finished_at": "<iso8601>",
  "duration_ms": 0
}
```

허용 status:

```text
planned
cache_hit
rebuilt
failed
skipped_removed
cancelled
```

대표 dirty reason:

```text
new_company
removed_company
company_source_hash_changed
source_artifact_hash_changed
company_shard_schema_version_changed
spine_projection_version_changed
chain_index_version_changed
builder_version_changed
cache_missing
cache_corrupt
no_cache_requested
force_release_requested
```

중요:

```text
force_release_requested는 새 release를 만들 이유다.
변경 없는 shard를 반드시 rebuild해야 한다는 뜻이 아니다.
```

## 8. Cache 정책

cache는 성능 최적화다. correctness의 필수 조건이 아니다.

cache 종류:

| cache | key 구성 | 저장 대상 |
| --- | --- | --- |
| company shard cache | company source hash, shard schema version, builder version | company shard SQLite |
| spine fragment cache | company source hash, shard schema version, spine projection version, builder version | spine fragment SQLite |
| global merge cache | fragment hash set, chain version, builder version | global spine SQLite |
| delta upload cache | file sha256, size, relative path | upload skip list |

cache hit 인정 조건:

```text
file exists
SQLite opens
metadata table exists
schema version matches
cache key matches
required table set exists
counts are internally consistent
```

cache corrupt 처리:

```text
1. corrupt cache를 hit로 인정하지 않는다.
2. source artifact에서 rebuild한다.
3. rebuild 성공 전에는 기존 valid artifact를 지우지 않는다.
4. rebuild 실패 시 release candidate를 fail 처리한다.
5. current는 그대로 둔다.
```

운영 의미:

```bash
uv run krw-ontology release force
```

위 명령은 새 release를 만든다. cache는 사용할 수 있다.

```bash
uv run krw-ontology release force --no-cache
```

위 명령은 cache를 무시하고 다시 계산한다. 일반 품질 수정 후에는 보통 필요 없다.

## 9. 새 ticker, 수정 ticker, schema 변경

### 9.1 새 ticker 추가

흐름:

```text
source manifest에 새 ticker 등장
-> BuildPlan marks new_company
-> 새 company shard build
-> 새 spine fragment build
-> global spine merge가 기존 fragment + 새 fragment 병합
-> cross-company links refresh
-> verify
-> 새 release promote
```

사람이 기존 모든 회사 artifact를 다시 맞출 필요는 없다. schema가 맞는 source
artifact가 추가되면 builder가 source manifest 기준으로 포함한다.

### 9.2 ticker 하나 수정

흐름:

```text
MSFT source hash 변경
-> CompanyShard:MSFT dirty
-> SpineFragment:MSFT dirty
-> 다른 ticker shard는 cache hit
-> global spine merge는 전체 fragment set을 compact merge
-> impacted cross-company keys refresh
-> verify
```

전체 monolith rebuild가 없어야 한다.

### 9.3 ticker 제거

흐름:

```text
source manifest에서 ticker 제거
-> BuildPlan marks removed_company
-> shard_manifest에서 제거
-> global spine merge에서 해당 ticker rows 제외
-> old shard는 새 release에 포함하지 않음
-> previous release에는 그대로 남아 rollback 가능
```

### 9.4 ontology schema 변경

schema 변경은 세 종류로 나눠야 한다.

| 변경 | rebuild 범위 |
| --- | --- |
| source artifact field 추가, shard 저장 필요 없음 | spine projection만 rebuild 가능 |
| shard table 또는 evidence layout 변경 | company shards rebuild |
| chain/routing key 의미 변경 | spine fragments와 global spine rebuild |

schema version bump 규칙:

```text
company shard schema 변경 -> company_shard_schema_version bump
spine projection 변경 -> spine_projection_version bump
chain link algorithm 변경 -> chain_index_version bump
manifest contract 변경 -> release format 또는 manifest version bump
```

## 10. Global spine schema 책임

`global_spine.sqlite`는 full evidence store가 아니다. 아래 목적만 담당한다.

```text
object location
document discovery
edge skeleton
factor/topic/metric/entity/counterparty key normalization
no-ticker query routing
chain planning
compare query candidate selection
quality aggregate
search candidate generation
```

필수 table:

```text
metadata
global_object_locator
global_document_catalog
global_edge_spine
global_factor_spine
global_topic_spine
global_metric_spine
global_entity_spine
global_counterparty_spine
global_chain_index
global_key_stats
global_search_fts
```

금지:

```text
full quote text 저장
full filing text 저장
large payload JSON 중복 저장
company shard로 resolve할 수 없는 locator 저장
generic key만으로 cross-company chain 과대 생성
```

## 11. Company shard 계약

company shard는 한 ticker의 full evidence store다.

필수 책임:

```text
documents
objects
edges
support links
quotes
numeric evidence
quality_events
trace detail
retrieval text
metadata
```

필수 metadata:

```text
ticker
company_source_hash
shard_schema_version
builder_version
source_manifest_hash
created_at
row counts
```

검증:

```text
shard opens
metadata exists
ticker matches filename and manifest
required tables exist
object ids are unique inside shard
document ids are unique inside shard
quality_events table readable
row counts match shard_manifest
```

## 12. Cross-company chain 설계

chain은 v3에서 약해지면 안 된다. 오히려 monolith scan 없이 더 명확해야 한다.

Chain node type:

```text
company
object
document
factor
topic
metric
entity
counterparty
risk
driver
```

Chain edge type:

```text
same_factor
same_topic
same_metric
same_entity
supplier_customer
competitor
segment_overlap
macro_exposure
evidence_supports
document_related
object_relation
```

Algorithm:

```text
1. Query를 factor/topic/metric/entity key로 normalize한다.
2. global spine에서 candidate companies와 object locators를 찾는다.
3. global_chain_index에서 path 후보를 만든다.
4. generic key penalty와 source quality score를 적용한다.
5. top path에 필요한 shard만 연다.
6. shard에서 quote/evidence/detail을 lazy fetch한다.
7. answer composer에는 path + evidence를 같이 넘긴다.
```

검증:

```text
path endpoint가 locator로 resolve된다.
path가 missing shard를 참조하면 실패한다.
generic key만 있는 path는 낮은 score를 받는다.
chain result에는 evidence fetch 가능성이 보장된다.
```

## 13. Serving router 계약

주 파일:

```text
src/krw_ontology/agent_index/spine_router.py
src/krw_ontology/agent_index/router.py
src/krw_ontology/mcp_server/server.py
```

Router 생성:

```text
release root resolve
manifest v3 check
global spine open
shard manifest load
company shard handles lazy pool
```

Ticker query:

```text
ticker가 명시됨
-> shard_manifest에서 shard path resolve
-> 해당 shard open
-> retrieval/trace/detail 수행
```

No-ticker query:

```text
ticker 없음
-> global_search_fts/global_topic_spine/global_factor_spine 검색
-> candidate ticker 제한
-> 필요한 shard만 fanout
-> 결과 merge
```

Trace query:

```text
object/document id
-> global_object_locator 또는 global_document_catalog resolve
-> target shard open
-> full trace detail fetch
```

Compare query:

```text
metric/topic/factor key normalize
-> global spine에서 candidate ticker 선택
-> shard fanout
-> normalized result merge
```

Production에서 금지:

```text
agent_index.sqlite fallback
filesystem scan으로 missing shard 숨기기
v1/v2 manifest를 열어 serving
startup 시 모든 shard open
```

## 14. MCP startup 계약

MCP startup은 빠르게 health port를 열어야 한다.

금지:

```text
PRAGMA integrity_check on huge SQLite
full sha256 of global or shard files
all shard open
smoke query
ranking quality
quality check
monolith open
```

허용:

```text
prod/current resolve
manifest.json read
format == krw-ontology-release/v3
status == ready
env matches expected env
index_layout == global-spine-and-company-shards
monolith_required == false
manifest paths are relative and inside root
global_spine.sqlite exists
shard_manifest.json exists
cheap SQLite open of global_spine
```

Startup sequence:

```text
1. resolve runtime release root
2. verify_release_startup_v3(check_sqlite=True)
3. export runtime env paths
4. open HTTP health endpoint
5. initialize router lazily
6. serve requests
```

Health payload:

```json
{
  "ok": true,
  "release_id": "20260612_120000",
  "env": "prod",
  "format": "krw-ontology-release/v3",
  "index_layout": "global-spine-and-company-shards",
  "global_spine_present": true,
  "shard_manifest_present": true,
  "company_shard_count": 162,
  "router_generation": 1
}
```

## 15. Quality 계약

Quality는 gate가 아니다. 사용자가 원할 때 실행하는 read-only 검사다.

기본 명령:

```bash
uv run krw-ontology quality check
```

기본 대상:

```text
<publish-root>/<env>/current
```

Quality scanner 입력:

```text
release_root/manifest.json
release_root/indexes/global_spine.sqlite
release_root/indexes/shard_manifest.json
release_root/indexes/companies/*.sqlite
```

Quality가 하지 말아야 할 것:

```text
release build
index rebuild
current mutation
prod publish gate
agent_index.sqlite 요구
```

Quality가 해야 할 것:

```text
manifest v3 check
global spine existence/open check
shard manifest existence/open check
shard count consistency
locator -> shard object resolution
document catalog -> shard document resolution
edge endpoint resolution
chain endpoint resolution
quality_events aggregate
coverage summary
repair plan stale guard
```

품질 수정 흐름:

```bash
uv run krw-ontology quality check
# running-root/source 수정
uv run krw-ontology release force
uv run krw-ontology quality check
```

`release force`가 다시 필요한 이유:

```text
quality check는 immutable current release를 읽는다.
수정은 running-root/source에 한다.
수정된 source가 release로 반영되려면 새 immutable release가 필요하다.
```

## 16. CLI 계약

### 16.1 Config

최초 설정:

```bash
cd ~/krw-ontology
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

해석 순서:

```text
--from-root option
-> config running-root
-> fail with clear message

--releases-root option
-> config publish-root
-> fail with clear message

--env option
-> KRW_ONTOLOGY_ENV
-> dev
```

### 16.2 Release commands

Normal:

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
```

Debug/CI:

```bash
uv run krw-ontology release force --foreground
uv run krw-ontology release force --no-cache
uv run krw-ontology release startup-check --env dev
uv run krw-ontology release inspect \
  --releases-root ~/krw-ontology-data/releases \
  --env dev
```

`release deploy`, `release publish`, `release build` 같은 긴 명령은 내부 호환 또는
debug path로 남더라도, help text에서 normal path보다 앞에 나오면 안 된다.

### 16.3 Index commands

Index command는 release transaction 없이 root 내부 index outputs만 다루는
developer/debug surface다.

```bash
uv run krw-ontology index plan --root ~/krw-ontology-data-running
uv run krw-ontology index build --root ~/krw-ontology-data-running
uv run krw-ontology index verify --root ~/krw-ontology-data-running
uv run krw-ontology index inspect --root ~/krw-ontology-data-running
uv run krw-ontology index explain-last-build --root ~/krw-ontology-data-running
uv run krw-ontology index cache status --root ~/krw-ontology-data-running
uv run krw-ontology index cache gc --root ~/krw-ontology-data-running
```

금지:

```text
--index-path as production option
--layout monolith-and-shards as default
agent_index.sqlite output in normal path
```

### 16.4 Queue and pipeline commands

대상:

```text
queue run
queue start
build-research-pipeline
build-company-context
```

최종 option:

```text
--refresh-index
--no-refresh-index
```

legacy option 제거:

```text
--rebuild-agent-index
--no-rebuild-agent-index
--index-path
```

동작:

```text
--refresh-index
  source 작업 후 v3 index outputs를 root에 갱신한다.

--no-refresh-index
  source만 갱신하고 index output은 건드리지 않는다.
  다음 단계는 release force 또는 명시 refresh-index다.
```

## 17. Release force 알고리즘

Pseudo-code:

```text
resolve env, running-root, releases-root
acquire env build lock
create candidate release id
materialize source from running-root into candidate
write source_manifest.json
build v3 index outputs:
  plan dirty companies
  build/reuse company shards
  build/reuse spine fragments
  merge global spine
  generate cross-company links
  write shard_manifest.json
  write build_summary.json
write v3 manifest draft
run deep verification
write verify reports
if verification passes:
  atomically switch current -> candidate
  mark worker ready
else:
  quarantine candidate under failed/
  keep old current
release lock
```

Failure handling:

```text
source materialize failure -> candidate failed, current unchanged
index build failure -> candidate failed, current unchanged
verify failure -> candidate failed, current unchanged
promote failure -> current unchanged or rollback to previous pointer
```

## 18. Prod publish 알고리즘

Pseudo-code:

```text
resolve source env dev/current
verify source is v3
verify source status ready
verify source has required reports
compute delta manifest against prod/current
copy/upload changed files to prod candidate
verify prod candidate v3 startup contract
verify prod candidate deep contract if local or report available
atomically switch prod/current
restart or reload MCP
poll health
ensure health release_id == target release_id
if health mismatch or failure:
  rollback prod/current to previous
  keep failure report
```

Delta include:

```text
manifest.json
source_manifest.json
indexes/global_spine.sqlite
indexes/shard_manifest.json
changed indexes/companies/*.sqlite
verify/*.json
```

Delta exclude:

```text
debug/monolith.sqlite
indexes/agent_index.sqlite
unchanged company shards
hidden cache directories
```

## 19. Front runtime deploy alignment

대상 repo:

```text
~/krw-ontology-front
```

기존 문제:

```text
script restarts launchd first
-> new MCP startup runs deep verification
-> 81GB DB scan
-> health timeout
```

최종 contract:

```text
1. target prod/current resolve
2. target v3 startup preflight
3. target invalid이면 기존 MCP를 내리지 않음
4. restart/reload
5. /health 확인
6. release_id와 index_layout 확인
7. 실패 시 rollback 또는 old service 유지
```

이 repo 책임:

```text
verify_release_startup_v3 제공
MCP startup lightweight
health payload에 release_id/index_layout 제공
prod publish가 v3 target만 activate
```

front repo 책임:

```text
install-mcp-server-launchd.sh restart 전 preflight
health response의 release_id 비교
index_layout 비교
invalid target에서 restart 금지
```

## 20. 구현 작업 패키지

### 20.1 Schema and builder

Files:

```text
src/krw_ontology/agent_index/spine_schema.py
src/krw_ontology/agent_index/spine_builder.py
src/krw_ontology/agent_index/cross_company_links.py
tests/unit/test_spine_schema.py
tests/unit/test_spine_builder.py
```

완료 기준:

```text
empty global spine 생성 가능
company shard direct build 가능
spine fragment build 가능
deterministic merge 가능
cache hit/miss/corrupt path 테스트 존재
```

### 20.2 Release model

Files:

```text
src/krw_ontology/release.py
tests/unit/test_release_v3.py
```

완료 기준:

```text
write_release_manifest_v3
verify_release_startup_v3
verify_release_root v3 branch
v1/v2 hard reject in production paths
relative path enforcement
missing shard failure
```

### 20.3 CLI

Files:

```text
src/krw_ontology/cli/main.py
tests/unit/test_cli.py
```

완료 기준:

```text
release force default v3
release status/watch worker aware
prod publish default config-driven
quality check default current release
queue/pipeline refresh-index terminology
no production --index-path
no production --rebuild-agent-index
```

### 20.4 Router and MCP

Files:

```text
src/krw_ontology/agent_index/spine_router.py
src/krw_ontology/agent_index/router.py
src/krw_ontology/mcp_server/http_server.py
src/krw_ontology/mcp_server/server.py
tests/unit/test_mcp_server.py
tests/unit/test_agent_index.py
```

완료 기준:

```text
startup cheap
health v3 payload
retrieve smoke
trace smoke
chain smoke
compare smoke
no monolith fallback
missing shard failure visible
```

### 20.5 Quality

Files:

```text
src/krw_ontology/quality/scanner.py
src/krw_ontology/quality/models.py
src/krw_ontology/quality/__init__.py
tests/unit/test_quality.py
tests/unit/test_cli.py
```

완료 기준:

```text
QualityReleaseScanner reads v3 release root
quality check does not mutate
quality repair stale guard
quality plan references release/source hashes
locator/shard consistency checks
```

### 20.6 Prod publish and remote activation

Files:

```text
src/krw_ontology/cli/main.py
src/krw_ontology/release.py
tests/unit/test_cli.py
```

완료 기준:

```text
source dev/current v3 preflight
prod candidate v3 manifest rewrite
delta changed file manifest
remote activation v3 preflight
health release_id mismatch rollback
debug monolith excluded by default
```

### 20.7 Front deploy script

Files:

```text
~/krw-ontology-front/scripts/install-mcp-server-launchd.sh
~/krw-ontology-front/package.json
```

완료 기준:

```text
restart 전 target v3 preflight
invalid target does not stop healthy old MCP
health release_id/index_layout check
timeout is not the primary fix
```

## 21. Test matrix

Core v3:

```bash
uv run pytest -q \
  tests/unit/test_spine_schema.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_release_v3.py
```

CLI:

```bash
uv run pytest -q tests/unit/test_cli.py
```

MCP/router:

```bash
uv run pytest -q \
  tests/unit/test_agent_index.py \
  tests/unit/test_mcp_server.py
```

Quality:

```bash
uv run pytest -q \
  tests/unit/test_quality.py \
  tests/unit/test_cli.py::TestProdCommand::test_quality_check_uses_configured_release_current_by_default
```

Targeted release CLI:

```bash
uv run pytest -q \
  tests/unit/test_cli.py::TestReleaseCommand::test_release_prepare_dev_sets_config_and_retargets_pending_queue \
  tests/unit/test_cli.py::TestReleaseCommand::test_release_finalize_dev_rebuilds_manifest_promotes_and_clears_config \
  tests/unit/test_cli.py::TestReleaseCommand::test_release_materialize_prod_copies_dev_release_and_rewrites_manifest
```

Full unit:

```bash
uv run pytest -q tests/unit
```

Static:

```bash
uv run python -m py_compile \
  src/krw_ontology/cli/main.py \
  src/krw_ontology/release.py \
  src/krw_ontology/web_catalog.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/agent_index/spine_router.py
```

Whitespace:

```bash
git diff --check
```

## 22. Integration test scenarios

### 22.1 Dev release

```text
given running-root with two tickers
when release force runs
then dev/<release-id>/manifest.json is v3
and indexes/global_spine.sqlite exists
and indexes/shard_manifest.json exists
and indexes/companies/<ticker>.sqlite exists
and current points to release id
and indexes/agent_index.sqlite does not exist
```

### 22.2 Warm no-change force

```text
given current v3 release and unchanged source
when release force runs
then new release id is created
and unchanged company shards are cache_hit
and global spine is rebuilt or reused according to fragment hash set
and old current remains available for rollback
```

### 22.3 Single ticker change

```text
given 162 tickers
when MSFT source changes
then only MSFT company shard and spine fragment are dirty
and global spine merges all fragments
and other shard files are reused or copied
```

### 22.4 Quality repair

```text
given quality check reports issue
when repair modifies running-root
then current release is unchanged
and quality check still sees old current
when release force completes
then quality check sees fixed release
```

### 22.5 MCP startup

```text
given prod/current v3 release
when MCP starts
then health port opens without full integrity check
and health payload reports v3 layout
and router opens shards lazily
```

### 22.6 Prod publish rollback

```text
given dev/current v3 release
and prod/current old v3 release
when prod publish activates target
and health release_id mismatches
then prod/current rolls back to previous release
and failure report is recorded
```

## 23. Performance baseline

측정해야 할 숫자:

| metric | 의미 |
| --- | --- |
| cold full v3 build time | cache 없는 전체 build |
| warm force no-change time | source 변경 없는 새 release |
| single ticker change time | ticker 하나 수정 후 release force |
| new ticker add time | 새 ticker 추가 후 release force |
| global spine size | compact routing DB 크기 |
| total shard size | company shard 총합 |
| release bundle size | prod publish 대상 크기 |
| delta upload size | 변경분 크기 |
| MCP startup time | process start에서 health ok까지 |
| first query latency | lazy router 첫 요청 |
| steady query latency | warm router 요청 |
| quality check time | v3 release quality scan |

측정 기록 형식:

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
  "mcp_startup_seconds": 0,
  "quality_check_seconds": 0
}
```

## 24. 운영 runbook

### 24.1 최초 설정

```bash
cd ~/krw-ontology
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

### 24.2 dev release 생성

```bash
uv run krw-ontology release force
uv run krw-ontology release watch
```

확인:

```bash
uv run krw-ontology release status
uv run krw-ontology release startup-check --env dev
```

### 24.3 quality 확인

```bash
uv run krw-ontology quality check
```

문제가 있으면:

```text
1. running-root/source 수정
2. uv run krw-ontology release force
3. uv run krw-ontology quality check
```

### 24.4 prod publish

```bash
uv run krw-ontology prod publish-dev
```

확인:

```bash
uv run krw-ontology prod status
```

### 24.5 MCP startup 실패

먼저 확인:

```bash
uv run krw-ontology release verify \
  --startup-check \
  --root ~/krw-ontology-data/releases/prod/current \
  --env prod \
  --require-current-symlink
```

흔한 원인:

```text
manifest v1/v2
missing global_spine
missing shard_manifest
manifest path outside root
env mismatch
SQLite open failure
front script restarted before preflight
```

대응:

```text
v1/v2 manifest
  -> dev v3 release 생성 후 prod publish

missing shard
  -> dev release rebuild 또는 prod delta 재전송

health timeout
  -> startup deep scan이 남아 있는지 확인

release_id mismatch
  -> activation rollback path 확인
```

## 25. 리뷰 체크리스트

```text
[ ] production path가 v3 manifest만 허용한다.
[ ] production path가 agent_index.sqlite를 요구하지 않는다.
[ ] current release 내부를 직접 쓰지 않는다.
[ ] force와 no-cache 의미가 섞이지 않는다.
[ ] startup verifier가 deep scan을 하지 않는다.
[ ] quality check가 release를 mutate하지 않는다.
[ ] quality repair가 running-root만 수정한다.
[ ] cache key에 source hash와 version constants가 들어간다.
[ ] cache corrupt는 source rebuild로 복구된다.
[ ] manifest path는 상대 경로다.
[ ] missing shard가 failure로 드러난다.
[ ] full evidence text가 global spine에 중복 저장되지 않는다.
[ ] prod publish preflight가 MCP restart보다 앞선다.
[ ] health release_id mismatch rollback이 테스트된다.
[ ] tests가 success path와 failure path를 모두 포함한다.
```

## 26. Definition of done

v3 완료는 아래가 모두 참일 때만 선언한다.

1. `release force`가 기본으로 v3 release를 만든다.
2. production release에 `indexes/agent_index.sqlite`가 필요 없다.
3. `dev/current`와 `prod/current`가 v3 release를 가리킨다.
4. MCP startup이 deep scan 없이 health를 연다.
5. MCP serving이 `global_spine.sqlite`와 company shards만 사용한다.
6. ticker query smoke가 통과한다.
7. no-ticker query smoke가 통과한다.
8. trace query smoke가 통과한다.
9. chain query smoke가 통과한다.
10. compare query smoke가 통과한다.
11. quality smoke가 통과한다.
12. `quality check`가 v3 release root를 read-only로 검사한다.
13. quality repair 후에는 `release force`로 새 release를 만든다.
14. `prod publish`가 v1/v2와 monolith-required release를 거절한다.
15. prod delta publish가 global spine과 changed shards만 업로드한다.
16. remote activation이 release_id health mismatch에서 rollback한다.
17. front runtime deploy가 restart 전에 target v3 preflight를 한다.
18. legacy v2 user-facing command가 제거, hidden, 또는 hard reject 된다.
19. cold/warm/single ticker/new ticker performance baseline이 기록된다.
20. full unit tests와 end-to-end integration tests가 통과한다.

## 27. 현재 남은 핵심 작업

현재 branch가 이미 구현한 항목은 `v3-production-implementation-handoff.md`의
"현재 branch 구현 상태"를 따른다. final production complete로 부르기 전 남은
핵심은 아래다.

| 영역 | 남은 일 |
| --- | --- |
| router API parity | retrieve, trace, chain, compare, topic map, quality smoke 확대 |
| quality finalization | topology consistency와 large release performance budget 확대 |
| release trust path | public writer/verifier v3-only 단일화와 digest enforcement 완료; 실제 데이터 integration 필요 |
| MCP tool internal helpers | 일부 unit fixture용 `root/index_path` override를 test-only seam으로 격리 또는 제거 |
| front runtime deploy | prod/dev/docker v3 runtime와 legacy cleanup targeted 검증 완료 |
| integration tests | dev release, MCP startup, prod publish, rollback end-to-end |
| progress events | build DAG node별 progress JSONL 상세화 |
| performance baseline | cold/warm/single ticker/new ticker/MCP startup 수치 기록 |

## 28. 아주 쉬운 설명

기존에는 큰 책 한 권에 모든 내용을 넣고, 회사별 작은 책도 따로 만들었다.
그래서 같은 내용이 두 번 저장되고, 큰 책을 검사하느라 시작이 느렸다.

v3는 이렇게 바꾼다.

```text
global_spine.sqlite
  어느 회사와 어떤 주제가 연결되는지 알려주는 지도

company shard
  실제 증거와 세부 내용이 들어 있는 회사별 책
```

질문이 들어오면 먼저 지도에서 어디를 볼지 찾고, 필요한 회사 책만 연다.
그래서 온톨로지의 전체 연결성은 유지하면서, 큰 monolith 파일 없이 serving한다.

운영자가 평소 쓰는 명령은 이것뿐이다.

```bash
uv run krw-ontology release force
uv run krw-ontology release watch
uv run krw-ontology quality check
uv run krw-ontology prod publish-dev
```
