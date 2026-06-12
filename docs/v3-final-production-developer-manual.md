# KRW Ontology v3 final production developer manual

기준일: 2026-06-12

이 문서는 `krw-ontology`를 v3 final production 구조로 끝까지 구현하기 위한
개발자 매뉴얼이다. 설계 토론용 문서가 아니라, 실제 코드를 고칠 때 무엇을
수정하고 무엇을 깨면 안 되며 어떤 테스트로 완료를 증명할지 정리한 실행 기준서다.

최종 결론은 하나다.

```text
production serving = immutable v3 release + global spine + company shards
```

다음은 정상 production path가 아니다.

```text
agent_index.sqlite monolith serving
monolith-and-shards layout
v1/v2 release compatibility serving
MCP startup deep verification
in-place mutable index build
quality check가 release를 직접 수정하는 흐름
prod publish가 restart 후에야 release mismatch를 발견하는 흐름
```

## 1. 문서 목적과 권위

이 문서는 다음 질문에 답한다.

1. v3 final production의 최종 구조는 무엇인가?
2. 어떤 파일이 어떤 책임을 가지는가?
3. `release force`가 내부에서 어떤 DAG를 실행해야 하는가?
4. cache와 partial rebuild는 어떤 조건에서 안전한가?
5. quality check와 repair는 release, running-root, cache와 어떻게 연결되는가?
6. MCP startup은 어떤 검증만 해야 하고 무엇을 하면 안 되는가?
7. prod publish는 어떤 순서로 preflight, upload, activate, rollback해야 하는가?
8. 어떤 테스트와 실제 데이터 baseline을 통과해야 완료인가?

이 문서는 아래 문서와 함께 사용한다.

| 문서 | 책임 |
| --- | --- |
| `v3-final-production-master-development-guide.md` | 최종 결정, 불변식, 완료 기준의 source of truth |
| `v3-final-production-implementation-runbook.md` | 실행 순서, 운영 runbook, 실패 대응 |
| `global-spine-company-shards-v3-development-guide.md` | schema와 build architecture 상세 |
| 이 문서 | 파일별 개발 지침, 구현 순서, 테스트와 review 기준 |

문서끼리 충돌하면 `master development guide`를 먼저 따른다. 코드가 문서와 다르면
둘 중 하나다.

```text
문서가 오래됨
  -> 문서를 고친다.

코드가 final contract를 아직 만족하지 못함
  -> 코드를 미완료로 분류한다.
```

## 2. 최종 제품 결정

### 2.1 v3만 정상 target이다

정상 release, MCP, quality, prod publish 경로는 v3만 성공으로 처리한다.

```text
format = krw-ontology-release/v3
index_layout = global-spine-and-company-shards
monolith_required = false
```

v1/v2 관련 코드는 다음 목적에만 남을 수 있다.

```text
negative test fixture
historical migration reference
cleanup/quarantine 대상 식별
explicit hard reject behavior 검증
```

성공 경로에서 v1/v2를 열면 final production 위반이다.

### 2.2 production 기본 산출물

v3 release의 기본 산출물은 다음이다.

```text
<release-root>/
  manifest.json
  source_manifest.json

  companies/
    <TICKER>/

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
    startup_verify.json
    routing_report.json
    quality_report.json

  logs/
    release-build.log
```

정상 production bundle에 아래 파일이 필수 또는 기본 산출물로 들어가면 안 된다.

```text
indexes/agent_index.sqlite
debug/monolith.sqlite
```

`debug/monolith.sqlite` 같은 parity 조사 산출물은 release normal output 바깥에서
명시 debug 작업으로만 만들 수 있다. 정상 `manifest.json`의 `indexes`에도
`debug_monolith` entry를 넣지 않는다. 오래된 manifest에 `debug_monolith.required`
가 `true`로 있으면 verifier가 거절해야 한다.

### 2.3 운영자가 외워야 하는 명령

초기 설정:

```bash
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
export KRW_ONTOLOGY_ENV=dev
```

평소 dev release:

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology release startup-check --env dev
```

품질 확인:

```bash
uv run krw-ontology quality check --env dev
uv run krw-ontology quality tickers --env dev --severity high
uv run krw-ontology quality explain <TICKER> --env dev
```

prod 반영:

```bash
uv run krw-ontology prod doctor
uv run krw-ontology prod publish-dev --dry-run
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

긴 path option은 CI, 복구, 디버그에서만 사용한다.

## 3. 절대 불변식

아래는 취향이 아니라 production 계약이다.

1. `current`는 immutable release를 가리키는 pointer다.
2. active `current` release 내부 파일은 수정하지 않는다.
3. build, verify, publish, health 실패 시 기존 `current`는 유지되거나 rollback된다.
4. v3 release만 production serving target이다.
5. production layout은 `global-spine-and-company-shards` 하나다.
6. `monolith_required`는 반드시 `false`다.
7. `indexes/agent_index.sqlite`는 production 기본 산출물이 아니다.
8. global spine은 route와 chain planning을 위한 compact index다.
9. full evidence, quote, payload는 company shard에 둔다.
10. missing required shard는 fallback 없이 실패해야 한다.
11. manifest public path는 release root 내부 상대 경로다.
12. startup verification은 lightweight만 한다.
13. deep verification은 release build 또는 publish preflight에서 한다.
14. quality check는 read-only다.
15. quality repair는 running-root/source만 수정한다.
16. quality repair 결과를 보려면 새 release를 만든다.
17. `force`는 새 release 생성을 강제한다.
18. `--no-cache`만 cache read 우회를 의미한다.
19. prod publish는 remote restart 전에 target release를 검증한다.
20. health가 target `release_id`와 다르면 성공이 아니다.

## 4. 파일별 책임

### 4.1 Core package

| 파일 | 책임 | final contract |
| --- | --- | --- |
| `src/krw_ontology/release.py` | release writer, verifier, promote, rollback | v3만 생성/검증/승격 |
| `src/krw_ontology/cli/main.py` | CLI surface, config 기본값, worker/status/watch | 짧은 normal command 제공 |
| `src/krw_ontology/config/paths.py` | running-root, publish-root, env 해석 | legacy index path resolver 제거 |
| `src/krw_ontology/agent_index/spine_schema.py` | global spine DDL/version | compact global route schema |
| `src/krw_ontology/agent_index/spine_builder.py` | v3 build DAG, shard, fragment, merge | production normal path에서 monolith 생성 금지 |
| `src/krw_ontology/agent_index/source_artifact_sqlite.py` | source artifact SQLite primitive boundary | legacy builder internals를 v3 이름으로 격리 |
| `src/krw_ontology/agent_index/spine_verify.py` | v3 topology/digest/schema verification | startup/deep 책임 분리 |
| `src/krw_ontology/agent_index/spine_router.py` | runtime route and shard lazy open | no monolith fallback |
| `src/krw_ontology/agent_index/router.py` | public store factory | v3 spine-only open |
| `src/krw_ontology/agent_index/cross_company_links.py` | cross-company chain 후보 생성 | global spine chain 보존 |
| `src/krw_ontology/quality/scanner.py` | v3 release quality scan | read-only release scan |
| `src/krw_ontology/quality/models.py` | quality report, repair fingerprint | stale guard에 v3 topology 포함 |
| `src/krw_ontology/quality/runner.py` | repair execution | running-root/source만 수정 |
| `src/krw_ontology/mcp_server/http_server.py` | MCP startup | lightweight preflight 후 port open |
| `src/krw_ontology/mcp_server/server.py` | MCP health/metrics/diagnostics | v3 payload만 노출 |
| `src/krw_ontology/mcp_server/tools.py` | MCP tool handlers | runtime path override 금지 |

### 4.2 Tests

| 테스트 | 증명할 것 |
| --- | --- |
| `tests/unit/test_spine_schema.py` | global spine table/index/schema version |
| `tests/unit/test_spine_builder.py` | company shard, fragment, merge, cache, no monolith output |
| `tests/unit/test_release_v3.py` | manifest v3, digest, startup/deep verify, non-v3 hard reject |
| `tests/unit/test_agent_index.py` | public API v3-only, router behavior, missing shard diagnostics |
| `tests/unit/test_mcp_server.py` | MCP startup/health/tools v3-only |
| `tests/unit/test_mcp_tool_input_aliases.py` | external MCP schema에 path override 없음 |
| `tests/unit/test_quality.py` | quality scan, topology, repair stale guard |
| `tests/unit/test_cli.py` | CLI help, command defaults, forbidden legacy options |

## 5. Manifest v3 contract

정상 manifest 예:

```json
{
  "format": "krw-ontology-release/v3",
  "env": "dev",
  "release_id": "20260612_120000",
  "status": "ready",
  "index_layout": "global-spine-and-company-shards",
  "monolith_required": false,
  "indexes": {
    "global_spine": {
      "path": "indexes/global_spine.sqlite",
      "required": true,
      "sha256": "<sha256>",
      "schema_version": "krw-ontology-global-spine/v1",
      "builder_version": "global-spine-builder/v1",
      "counts": {}
    },
    "company_shards": {
      "dir": "indexes/companies",
      "required": true,
      "count": 162,
      "tickers": {
        "MSFT": {
          "path": "indexes/companies/MSFT.sqlite",
          "sha256": "<sha256>",
          "schema_version": "krw-ontology-company-shard/v1"
        }
      }
    },
    "shard_manifest": {
      "path": "indexes/shard_manifest.json",
      "required": true,
      "sha256": "<sha256>"
    }
  }
}
```

금지:

```text
indexes.debug_monolith.required = true
absolute path
../ escape
format v1/v2
status != ready
index_layout != global-spine-and-company-shards
monolith_required = true
global_spine without sha256 in release manifest
shard_manifest without sha256 in release manifest
required shard without sha256 in release manifest
```

Verifier 책임:

| 모드 | 확인 | 금지 |
| --- | --- | --- |
| startup | manifest, relative path, existence, cheap SQLite open, metadata | full sha256, integrity_check, all shard open |
| deep | digest, schema, topology, counts, chain endpoints, smoke | current 변경 |
| publish preflight | source release가 prod target으로 안전한지 확인 | upload/restart 선행 |

## 6. Source manifest

Source manifest는 build input의 권위 있는 목록이다.

필수 역할:

```text
ticker 목록 결정
artifact path 결정
artifact content hash 기록
company source hash 계산
cache key 입력 제공
rebuild reason 설명
```

금지:

```text
filesystem broad scan만으로 build target 추측
hidden cache directory를 source로 포함
failed/current release를 source input으로 섞기
source hash 없이 cache hit 인정
```

새 ticker가 추가되면:

```text
source_manifest에 ticker 추가
new_company dirty reason 생성
새 company shard build
새 spine fragment build
global spine merge
cross-company link refresh
deep verify
promote
```

기존 모든 ticker를 사람이 다시 맞추는 작업은 필요하지 않다. 다만 schema 변경이 모든
source를 invalidation하면 전체 affected ticker rebuild가 맞다.

## 7. Build DAG

최종 `release force` 내부 DAG:

```text
ResolveConfig
AcquireEnvLock
CreateCandidateRelease
SourceManifest
BuildPlan
CompanyInputHash:<TICKER>
CompanyShard:<TICKER>
SpineFragment:<TICKER>
GlobalSpineMerge
CrossCompanyLinkGeneration
ShardManifestWrite
BuildSummaryWrite
ReleaseManifestWrite
DeepVerify
PromoteCurrent
ReleaseEventWrite
```

각 node record:

```json
{
  "id": "CompanyShard:MSFT",
  "stage": "company_shard",
  "ticker": "MSFT",
  "depends_on": ["SourceManifest", "BuildPlan"],
  "status": "cache_hit",
  "reason": "source_hash_unchanged",
  "input_hash": "<hash>",
  "output_hash": "<hash>",
  "schema_version": "krw-ontology-company-shard/v1",
  "builder_version": "<version>",
  "cache_key": "<hash>",
  "started_at": "<iso8601>",
  "finished_at": "<iso8601>",
  "duration_ms": 0,
  "output": "indexes/companies/MSFT.sqlite"
}
```

Allowed status:

```text
planned
running
cache_hit
rebuilt
failed
skipped_removed
cancelled
```

Dirty reason:

```text
new_company
removed_company
company_source_hash_changed
source_artifact_hash_changed
company_shard_schema_version_changed
spine_projection_version_changed
global_spine_schema_version_changed
chain_index_version_changed
builder_version_changed
cache_missing
cache_corrupt
no_cache_requested
force_release_requested
```

`force_release_requested`는 release candidate를 새로 만들 이유다. 모든 company shard를
무조건 다시 계산한다는 뜻은 아니다.

## 8. Cache와 partial rebuild

### 8.1 Cache 계층

| cache | key 입력 | 출력 |
| --- | --- | --- |
| company shard cache | ticker, source artifact hashes, source manifest version, shard schema version, shard builder version | `indexes/companies/<TICKER>.sqlite` |
| spine fragment cache | company shard digest/key, projection version, global key normalization version | `indexes/fragments/spine/<TICKER>.sqlite` |

### 8.2 Cache hit 조건

파일 존재만으로 hit가 아니다.

필수 조건:

```text
cache key 일치
파일 존재
SQLite open 가능
metadata table 존재
schema version 일치
builder/projection version 일치
ticker 일치
필수 table 존재
output digest 일치
```

Cache가 손상되면 source에서 rebuild한다. 성공 전에 기존 valid cache를 지우지 않는다.

### 8.3 Force와 no-cache

```text
release force
  새 immutable release를 만든다.
  valid cache는 사용할 수 있다.

release force --no-cache
  새 immutable release를 만든다.
  cache read를 우회한다.
```

quality 수정 후 일반적으로 필요한 것은 `release force`다. `--no-cache`는 cache 오염,
version invalidation 검증, cold baseline 측정 때만 쓴다.

### 8.4 변경 유형별 rebuild

| 변경 | company shard | spine fragment | global spine |
| --- | --- | --- | --- |
| source 변경 없음 | cache hit | cache hit | deterministic merge 또는 future reuse |
| MSFT source 변경 | MSFT만 rebuild | MSFT만 rebuild | merge |
| 새 ticker 추가 | 새 ticker build | 새 ticker fragment | merge |
| ticker 제거 | 해당 shard 제외 | 해당 fragment 제외 | merge |
| shard schema 변경 | 전체 affected rebuild | 전체 affected rebuild | merge |
| spine projection 변경 | shard cache 유지 가능 | 전체 fragment rebuild | merge |
| chain algorithm 변경 | shard 유지 가능 | fragment 유지 가능 | chain index rebuild |

## 9. Global spine

Global spine은 전체 ontology chain을 보존하기 위한 compact routing DB다.

담아야 하는 것:

```text
object locator
document catalog
global relation edge
topic/factor/metric/entity/counterparty route key
cross-company chain 후보
generic key penalty/idf
compact FTS search text
```

담으면 안 되는 것:

```text
full evidence JSON
긴 quote 전체
company shard의 모든 payload
company-local FTS 전체 복제
```

필수 table:

| table | 책임 |
| --- | --- |
| `metadata` | schema/layout/builder version |
| `global_object_locator` | object_id -> ticker/shard/local id |
| `global_document_catalog` | document -> ticker/shard/source |
| `global_edge_spine` | 전역 relation edge |
| `global_factor_spine` | factor key route |
| `global_topic_spine` | topic key route |
| `global_metric_spine` | metric key route |
| `global_entity_spine` | entity key route |
| `global_counterparty_spine` | counterparty route |
| `global_chain_index` | cross-company chain 후보 |
| `global_key_stats` | frequency/idf/generic penalty |
| `global_search_fts` | no-ticker discovery |

## 10. Company shard

Company shard는 회사별 full evidence store다.

역할:

```text
documents
objects
edges
quality_events
object search text
traceability
metrics
events
factors
topics
quotes/evidence payload
```

Router는 global spine에서 필요한 ticker와 object를 고른 뒤 해당 shard만 연다.

금지:

```text
missing shard를 agent_index.sqlite fallback으로 숨김
filesystem scan으로 manifest에 없는 shard를 암묵 사용
global spine에 shard payload 전체 중복
```

## 11. Router와 MCP

### 11.1 Routing

Ticker가 있는 query:

```text
parse ticker
validate ticker exists in shard manifest
open company shard lazily
fetch evidence/detail
return traceable result
```

Ticker가 없는 query:

```text
compact global search
topic/entity/factor/metric route 후보 추출
bounded fanout ticker 선정
필요 shard만 open
merge/rank
return diagnostics with selected shards
```

Trace:

```text
object id 또는 document id
-> global locator
-> owning ticker/shard
-> shard-local evidence
```

Chain:

```text
global_edge_spine/global_chain_index
-> route path
-> 필요한 endpoint shard만 fetch
```

### 11.2 Missing shard

Required shard가 없으면 결과는 성공처럼 보이면 안 된다.

정상 diagnostics:

```text
missing_company_shards
required_shard_missing
not_answerable_from_current_release
```

금지:

```text
fallback_used = true
monolith_open = true
agent_index.sqlite open
manifest 밖 filesystem scan
```

### 11.3 MCP startup

Startup에서 허용:

```text
release root resolve
current symlink check if required
manifest.json read
format/status/env/layout/monolith_required check
relative path/root containment check
global spine file exists
shard manifest file exists
SQLite cheap open
metadata schema/layout read
health payload prepare
port open
```

Startup에서 금지:

```text
PRAGMA integrity_check
full sha256
all shard open
all shard count scan
smoke query
ranking quality
quality full scan
remote delta verification
```

Startup이 오래 걸리면 launchd나 runtime deploy가 정상 MCP를 내린 뒤 새 MCP health를
못 받는 문제가 생긴다. timeout을 늘리는 것은 근본 해결이 아니다.

### 11.4 MCP tool schema

외부 tool input에서 금지:

```text
root
index_path
release_root
global_spine_path
shard_manifest_path
```

Tool은 configured runtime release를 사용해야 한다. 사용자가 tool call마다 path를
바꾸면 serving trust boundary가 깨진다.

## 12. Quality

### 12.1 Quality check

`quality check`는 release read-only 검사다.

읽는 것:

```text
<releases-root>/<env>/current/manifest.json
<release>/indexes/global_spine.sqlite
<release>/indexes/shard_manifest.json
<release>/indexes/companies/<TICKER>.sqlite
```

하지 않는 것:

```text
release 수정
current 전환
index rebuild
prod publish
repair 자동 실행
running-root/source 수정
```

### 12.2 Quality topology

검사해야 하는 것:

```text
shard manifest ticker set
required shard existence
shard digest
global_object_locator -> shard object 존재
global_document_catalog -> shard document 존재
global_edge_spine endpoint 존재
global_chain_index endpoint 존재
duplicate object id
quality_events count/severity
missing source/evidence
```

대용량 release에서는 full mode와 bounded mode를 분리할 수 있지만, final deep
quality verification에서는 topology 결함을 숨기면 안 된다.

### 12.3 Repair plan

Repair plan은 snapshot을 저장해야 한다.

```text
release_id
manifest sha256
source_manifest sha256
global_spine sha256
shard_manifest sha256
company shard topology hash
issue ids
selected ticker set
created_at
```

Repair run 전에 snapshot이 달라졌으면 stale plan으로 실패한다. `--allow-stale-plan`
은 operator override이며 normal path가 아니다.

### 12.4 Repair 후 rebuild

흐름:

```bash
uv run krw-ontology quality check --env dev
uv run krw-ontology quality repair plan --env dev
uv run krw-ontology quality repair run --plan <plan-id> --limit 20 --yes
uv run krw-ontology release force
uv run krw-ontology release watch
uv run krw-ontology quality check --env dev
```

다시 `release force`가 필요한 이유:

```text
repair는 running-root/source를 고친다.
MCP와 quality는 release를 읽는다.
release는 immutable이다.
따라서 수정 결과를 보려면 새 release가 필요하다.
```

## 13. CLI 상세 계약

### 13.1 `release force`

현재 구현된 help 기준:

```bash
uv run krw-ontology release force [OPTIONS]
```

Options:

```text
--from-root PATH       기본값: configured running-root
--releases-root PATH   기본값: configured publish-root
--env TEXT             기본값: KRW_ONTOLOGY_ENV 또는 dev
--release-id TEXT      기본값: timestamp id
--no-cache             v3 artifact/company shard/spine fragment cache read 우회
--foreground           background worker 대신 foreground 실행
```

정상 동작:

```text
configured root 해석
background worker 시작
candidate release 생성
v3 build DAG 실행
deep verify
current promote
worker state/log/progress 기록
```

### 13.2 `release status`

보여야 하는 정보:

```text
env
configured roots
current release id/path
current manifest format/layout
running worker pid/state
latest candidate release id
log path
progress path
latest error
```

### 13.3 `release watch`

정상 동작:

```text
running worker release id 우선
없으면 latest candidate release id 선택
hidden cache directory 제외
publish-dev 또는 force worker log/progress 출력
```

`.index_fragment_cache`를 release로 잡으면 CLI bug다.

### 13.4 `release startup-check`

명령:

```bash
uv run krw-ontology release startup-check --env dev
```

의미:

```text
MCP가 시작 전에 수행할 lightweight contract를 CLI로 확인한다.
```

긴 `release verify --startup-check --root ...`보다 normal operator path에서는
`startup-check`가 더 쉽다.

### 13.5 `prod publish-dev`

명령:

```bash
uv run krw-ontology prod publish-dev --dry-run
uv run krw-ontology prod publish-dev
```

Options:

```text
--root PATH          기본값: configured publish-root/dev/current
--host TEXT
--remote-root TEXT
--reload-command TEXT
--health-url TEXT
--keep-releases INT
--dry-run
--delta / --full     기본값: delta
```

정상 흐름:

```text
local dev/current resolve
v3 preflight
remote current file map
delta 계산
changed files upload
remote candidate reconstruction
remote candidate preflight
prod/current atomic activate
reload command
health poll
target release_id/layout 확인
failure면 rollback
```

## 14. Prod publish와 rollback

### 14.1 Preflight

upload 전에 확인:

```text
source release exists
manifest format v3
status ready
layout global-spine-and-company-shards
monolith_required false
global spine exists
shard manifest exists
company shard topology consistent
required digest present
shard_manifest quality_summary present
shard_manifest quality_summary recomputes exactly from shard SQLite
```

remote restart 전에 확인:

```text
remote candidate reconstructed
manifest v3
relative paths contained
global spine opens cheaply
global spine required tables and metadata match v3
shard manifest exists
shard manifest digest matches manifest
required shard files exist
company shard digest matches manifest or shard manifest
shard quality_summary format matches
shard quality_summary recomputes exactly from shard SQLite
verify/release_verify.json is ok for target prod release
```

### 14.2 Delta upload

Delta는 파일 일부를 덮어쓰는 mutable publish가 아니다.

```text
remote current file map
local target file map
changed path list
removed path list
upload changed files
remote candidate directory complete reconstruction
candidate verify
atomic current switch
```

Activation 단위는 항상 release 전체다.

### 14.3 Rollback

Rollback 조건:

```text
remote preflight failure
upload failure
remote verify failure
reload command failure
health timeout
health ok false
health release_id mismatch
health layout mismatch
```

Rollback은 이전 prod/current를 다시 가리키도록 atomic switch하고 health를 다시
확인해야 한다.

## 15. Error contract

운영자가 바로 원인을 알 수 있어야 한다.

좋은 error:

```text
manifest_format_unsupported: expected krw-ontology-release/v3, got krw-ontology-release/v2
manifest_indexes_global_spine_path_outside_root
global_spine_sha256_mismatch
shard:MSFT:sha256_mismatch
shard:NVDA:shard_missing
debug_monolith_required
tool input field index_path is not allowed in production MCP
current is not a symlink
```

나쁜 error:

```text
invalid release
failed
not found
sqlite error
timeout
```

## 16. Observability

### 16.1 Build progress

`indexes/build_progress.jsonl` 또는 release progress file은 최소 이벤트를 남긴다.

```text
release_started
source_manifest_started
source_manifest_completed
build_plan_written
company_shard_cache_hit
company_shard_build_started
company_shard_build_completed
spine_fragment_cache_hit
spine_fragment_build_completed
global_spine_merge_started
global_spine_merge_completed
cross_company_links_started
cross_company_links_completed
shard_manifest_written
release_manifest_written
deep_verify_started
deep_verify_completed
current_promoted
release_completed
release_failed
```

각 이벤트:

```text
timestamp
release_id
node
stage
ticker
status
reason
input_hash
output_hash
duration_ms
path
error
```

### 16.2 MCP health

Health payload에 있어야 하는 정보:

```text
ok
env
release_id
release_root
index_layout
global_spine_path
company_shard_count
company_shards_present
missing_company_shards
startup_check
store_generation
```

Health payload에 있으면 안 되는 정보:

```text
monolith_open
agent_index_path
global_topics_path as separate v2 store
fallback_used as normal success
```

## 17. 실제 데이터 baseline

최종 전환 전 실제 162 ticker 기준으로 측정한다.

| 항목 | 왜 필요한가 |
| --- | --- |
| cold build seconds | 최초 전체 build 비용 |
| warm no-change force seconds | cache 효과 |
| single ticker edit seconds | partial rebuild 효과 |
| new ticker add seconds | 신규 회사 비용 |
| ticker removal seconds | manifest exclusion 검증 |
| company shard cache hits/misses | cache correctness |
| spine fragment cache hits/misses | projection cache 효과 |
| global spine merge seconds | 전체 chain merge 비용 |
| global spine size | runtime map 크기 |
| total company shard size | evidence storage 비용 |
| largest shard size | worst case latency/storage |
| startup-check seconds | MCP startup 안정성 |
| MCP health seconds | launchd timeout 안전성 |
| ticker query p50/p95 | shard direct 성능 |
| no-ticker query p50/p95 | global spine fanout 성능 |
| trace p50/p95 | locator route 성능 |
| chain p50/p95 | cross-company chain 성능 |
| quality check seconds | operator 비용 |
| delta upload bytes | prod publish 비용 |

측정하지 않은 배수나 퍼센트는 문서에 약속하지 않는다.

## 18. 구현 순서

### Phase 1. Contract cleanup

완료 기준:

```text
normal release writer는 v3만 생성
normal verifier는 non-v3를 SQLite open 전에 hard reject
normal manifest indexes에 debug_monolith 없음
public open_ontology_store는 v3 spine-only
MCP external schema에 root/index_path 없음
```

검증:

```bash
uv run pytest -q tests/unit/test_release_v3.py tests/unit/test_agent_index.py --maxfail=10
uv run pytest -q tests/unit/test_mcp_server.py tests/unit/test_mcp_tool_input_aliases.py --maxfail=10
```

### Phase 2. Builder and cache

완료 기준:

```text
release force가 production agent_index.sqlite를 만들지 않음
company shard와 spine fragment cache key가 source/schema/builder/projection version 포함
build_plan/build_summary/build_progress가 rebuild reason을 설명
cache corrupt 시 rebuild
```

검증:

```bash
uv run pytest -q tests/unit/test_spine_schema.py tests/unit/test_spine_builder.py --maxfail=10
```

### Phase 3. Router parity

완료 기준:

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
missing shard failure
current hot swap
```

검증:

```bash
uv run pytest -q tests/unit/test_agent_index.py tests/unit/test_mcp_server.py --maxfail=10
```

### Phase 4. Quality finalization

완료 기준:

```text
quality check가 v3 release read-only
repair plan stale guard가 manifest/source/global spine/shard topology 포함
missing shard/topology mismatch를 report
large release runtime 측정
```

검증:

```bash
uv run pytest -q tests/unit/test_quality.py tests/unit/test_cli.py --maxfail=10
```

### Phase 5. Prod publish

완료 기준:

```text
prod publish-dev가 dev/current v3만 받음
delta reconstruction 후 remote candidate verify
restart 전 preflight
health release_id mismatch rollback
```

검증:

```bash
uv run pytest -q tests/unit/test_cli.py tests/unit/test_release_v3.py --maxfail=10
```

### Phase 6. Real data cutover

완료 기준:

```text
162 ticker cold build
warm force
single ticker edit
new ticker add
quality check
MCP startup
query smoke
prod dry-run
prod publish
rollback rehearsal
baseline 기록
```

## 19. 개발 중 audit 명령

Legacy release success path audit:

```bash
rg -n "krw-ontology-release/v1|krw-ontology-release/v2|RELEASE_FORMAT_V2|write_release_manifest\\(|build_release_manifest\\(" \
  src tests scripts docs
```

Legacy index path audit:

```bash
rg -n "KRW_ONTOLOGY_INDEX_PATH|resolve_agent_index_path|--index-path|args\\.index_path|kwargs\\[\"index_path\"\\]|kwargs\\[\"root\"\\]" \
  src tests scripts
```

Monolith production path audit:

```bash
rg -n "agent_index\\.sqlite|routing=\"monolith\"|routing='monolith'|routing=\"auto\"|routing='auto'|OntologyStoreRouter" \
  src tests scripts docs
```

결과 해석:

```text
negative assertion, historical docs, explicit rejection fixture
  -> 허용 가능

production command, MCP runtime, quality normal path, prod publish success path
  -> 허용 불가
```

## 20. 테스트 matrix

Targeted core:

```bash
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
  --maxfail=10
```

Static:

```bash
python3 -m py_compile \
  src/krw_ontology/agent_index/router.py \
  src/krw_ontology/agent_index/source_artifact_sqlite.py \
  src/krw_ontology/agent_index/spine_router.py \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_schema.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/release.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/mcp_server/http_server.py \
  src/krw_ontology/mcp_server/server.py \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/cli/main.py

git diff --check
```

CLI smoke:

```bash
uv run krw-ontology release --help
uv run krw-ontology release force --help
uv run krw-ontology release startup-check --help
uv run krw-ontology index --help
uv run krw-ontology quality --help
uv run krw-ontology prod publish-dev --help
```

## 21. Review checklist

코드 리뷰에서 아래 질문에 답한다.

```text
1. 이 변경이 v1/v2 normal serving path를 다시 열지 않는가?
2. 이 변경이 production agent_index.sqlite 의존성을 다시 만들지 않는가?
3. 이 변경이 active current release를 직접 수정하지 않는가?
4. 이 변경이 startup에서 expensive verify를 실행하지 않는가?
5. 이 변경이 MCP external schema에 path override를 노출하지 않는가?
6. 이 변경이 missing shard를 fallback으로 숨기지 않는가?
7. 이 변경이 quality check를 mutating operation으로 만들지 않는가?
8. 이 변경이 repair output을 release/current에 쓰지 않는가?
9. 이 변경이 cache key에 source/schema/builder/projection version을 포함하는가?
10. 이 변경이 prod restart 전에 target preflight를 수행하는가?
11. 이 변경이 rollback failure path를 테스트하는가?
12. 이 변경이 CLI help를 복잡하게 만들지 않는가?
13. 이 변경이 문서의 normal command와 실제 help를 일치시키는가?
14. 이 변경을 162 ticker real data에서 측정할 수 있는가?
```

하나라도 불명확하면 final production 변경으로 merge하면 안 된다.

## 22. 완료 기준

아래가 모두 참일 때 v3 final production complete다.

1. `release force`가 v3 release만 만든다.
2. production release에 `indexes/agent_index.sqlite`가 없다.
3. 정상 manifest `indexes`에 `debug_monolith`가 없다.
4. dev/current와 prod/current가 v3만 가리킨다.
5. v1/v2 release는 normal path에서 hard reject된다.
6. MCP startup이 deep scan 없이 health를 연다.
7. MCP health와 tools가 v3-only payload를 사용한다.
8. ticker query가 direct company shard로 동작한다.
9. no-ticker query가 global spine과 bounded fanout으로 동작한다.
10. trace가 global locator와 shard로 동작한다.
11. local/cross-company chain smoke가 통과한다.
12. compare/topic/company context/quality smoke가 통과한다.
13. missing shard가 fallback 없이 실패한다.
14. quality check가 immutable v3 release를 read-only로 검사한다.
15. quality repair가 running-root/source만 수정한다.
16. repair plan stale guard가 v3 topology를 포함한다.
17. prod publish가 v1/v2와 monolith-required release를 거절한다.
18. delta publish가 remote candidate를 완성한 뒤 검증한다.
19. remote activation/rollback preflight가 shard digest와 `quality_summary` 재계산을 검증한다.
20. health target release id mismatch가 rollback된다.
21. targeted unit/static/CLI smoke가 통과한다.
22. real data cold/warm/single/new ticker baseline이 기록된다.
23. real MCP startup/query baseline이 기록된다.
24. prod cutover와 rollback rehearsal가 완료된다.

## 23. 아주 쉬운 설명

예전 방식은 전체 내용을 담은 큰 책과 회사별 작은 책을 둘 다 만들었다.

```text
큰 책 = agent_index.sqlite
작은 책 = company shards
```

그래서 같은 내용이 중복되고, 큰 책을 다시 만들고 검사하느라 오래 걸렸다.

v3는 역할을 나눈다.

```text
global_spine.sqlite
  전체 회사가 어떻게 연결되는지 알려주는 지도

company shard
  실제 문장, 숫자, 근거가 들어 있는 회사별 책
```

질문이 들어오면 먼저 지도에서 필요한 회사를 찾고, 그 회사 책만 연다. 그래서 전체
ontology chain은 유지하면서 production에서 거대한 monolith를 없앨 수 있다.

평소 작업은 이 흐름이다.

```text
source 수정
-> release force
-> release watch/status
-> quality check
-> prod publish-dev
```

`force`는 새 release를 만든다는 뜻이다. 모든 회사를 무조건 다시 계산한다는 뜻은
아니다. 바뀌지 않은 회사는 cache를 쓸 수 있다.
