# KRW Ontology v3 최종형 개발 실행 문서

기준일: 2026-06-12

이 문서는 KRW Ontology를 v3 최종 production 구조로 끝까지 전환하기 위한
개발 실행 문서다. 설계 배경만 설명하는 문서가 아니라, 개발자가 어떤 코드를
어떤 순서로 수정하고, 어떤 명령으로 검증하고, 어떤 상태가 되어야 완료인지
판단할 수 있게 쓰는 기준서다.

이 문서의 결론은 하나다.

```text
production serving = immutable v3 release + global spine + company shards
```

`agent_index.sqlite` 기반 monolith production serving, v1/v2 호환 serving,
MCP startup deep verification, in-place mutable build는 최종 구조에 포함하지
않는다.

## 1. 확정된 제품 결정

### 1.1 호환성 결정

정상 production path에서 v1/v2 호환성은 제공하지 않는다.

의미는 다음과 같다.

```text
v1 release를 새 MCP가 serving하지 않음
v2 monolith-and-shards release를 새 MCP가 serving하지 않음
prod publish가 v1/v2 target을 받아들이지 않음
quality check가 v1/v2 monolith를 정상 입력으로 삼지 않음
CLI normal flow가 v1/v2 release 생성을 목표로 하지 않음
```

남길 수 있는 v1/v2 코드는 다음 용도뿐이다.

```text
negative test fixture
historical migration 참고
old candidate cleanup
rejection behavior 검증
```

즉, v1/v2가 성공 경로로 남아 있으면 미완료다.

### 1.2 production의 의미

여기서 production은 `prod` 환경만 뜻하지 않는다.

```text
dev release도 production-grade layout
staging release도 production-grade layout
prod release도 production-grade layout
```

환경별 차이는 다음뿐이다.

```text
dev
  로컬 검증과 품질 확인 대상

staging
  필요 시 prod 직전 검증 대상

prod
  실제 MCP serving 대상
```

데이터 구조, manifest contract, verification contract는 모두 같아야 한다.

### 1.3 임시 구현 금지

이번 전환에서 허용하지 않는 접근은 다음과 같다.

```text
일단 monolith도 같이 만든다
일단 v2도 열리게 둔다
일단 MCP startup timeout만 늘린다
일단 index_path option을 남긴다
일단 filesystem scan fallback을 둔다
일단 quality만 예전 DB를 읽게 둔다
일단 prod publish가 v2도 받게 둔다
```

이런 방식은 문제를 뒤로 미루는 것이 아니라 최종 구조를 깨는 것이다.

## 2. 최종 구조 한눈에 보기

### 2.1 데이터 흐름

```text
running-root
  -> source manifest discovery
  -> v3 build DAG
  -> company fragments
  -> company shards
  -> global spine
  -> immutable release candidate
  -> deep verify
  -> dev/current atomic promote
  -> quality check
  -> prod delta publish
  -> prod/current atomic activate
  -> MCP lightweight startup
  -> query routing through global spine and company shards
```

### 2.2 runtime 구조

```text
manifest.json
source_manifest.json
indexes/
  global_spine.sqlite
  shard_manifest.json
  build_plan.json
  build_summary.json
  build_progress.jsonl
  fragments/
    spine/
      MSFT.sqlite
      NVDA.sqlite
      ...
  companies/
    MSFT.sqlite
    NVDA.sqlite
    ...
verify/
  topology.json
  startup.json
  quality.json
logs/
  release.log
```

Production 기본 산출물에 포함하지 않는 것:

```text
indexes/agent_index.sqlite
debug/monolith.sqlite
mutable current 내부 쓰기
runtime fallback용 broad filesystem scan
```

`debug/monolith.sqlite`는 별도 parity 조사용으로 명시적으로 만들 수는 있지만,
production release bundle의 기본 산출물이 아니다.

### 2.3 chain 보존 방식

온톨로지의 핵심 장점은 한 회사만 보는 것이 아니라 여러 회사, topic, factor,
metric, entity가 한꺼번에 연결되는 chain이다. v3는 이 장점을 없애지 않는다.

v3에서 역할은 다음처럼 분리한다.

```text
global spine
  전체 연결 지도
  cross-company edge
  topic/entity/factor routing
  object locator
  shard locator
  no-ticker query fanout 계획

company shard
  회사별 상세 object
  document evidence
  quotes
  quality events
  local full-text/search payload
```

질문이 들어오면 MCP는 먼저 global spine을 본다.

```text
질문
  -> ticker/topic/entity 후보 찾기
  -> 필요한 회사 shard 목록 결정
  -> 해당 shard만 열어서 evidence와 detail 조회
  -> answer composer가 traceable result 생성
```

즉, v3는 전체 chain을 monolith에 몰아넣지 않고 global spine이 연결성을 보관하고,
shard가 상세 근거를 보관한다.

## 3. 왜 monolith를 production 기본에서 제거하는가

기존 `monolith-and-shards` 구조는 다음과 같다.

```text
agent_index.sqlite
  전체 graph와 evidence

companies/MSFT.sqlite
companies/NVDA.sqlite
...
  회사별 graph와 evidence
```

이 구조는 correctness를 확보하는 중간 단계로는 안전했지만, 장기 최선은 아니다.

문제는 다음이다.

```text
저장공간 중복
  같은 object, edge, evidence가 monolith와 shard에 중복 저장된다.

build 시간 증가
  ticker 하나만 바뀌어도 90GB급 monolith rewrite 비용이 남는다.

verify 시간 증가
  PRAGMA integrity_check와 full sha256이 큰 파일 전체를 읽는다.

startup 병목
  MCP가 포트를 열기 전에 큰 DB 검증을 하면 health check가 실패한다.

router correctness 약화
  monolith fallback이 있으면 shard/global spine route가 틀려도 숨겨진다.

upload 비용 증가
  prod publish 때 큰 파일 전체를 다시 보내거나 검증해야 한다.
```

따라서 최종 구조는 다음이어야 한다.

```text
global spine + company shards
```

monolith는 production serving의 기본값이 아니다.

## 4. 용어

| 용어 | 의미 |
| --- | --- |
| running-root | 현재 research pipeline이 artifact를 쓰는 작업 루트 |
| release root | immutable release 하나의 루트 |
| releases-root | dev/prod release들을 보관하는 상위 루트 |
| env | dev, staging, prod 같은 release namespace |
| current | 해당 env의 활성 release를 가리키는 symlink |
| source manifest | build 입력 artifact 목록과 digest의 권위 있는 목록 |
| global spine | 전체 ontology chain과 shard routing을 담는 compact SQLite |
| company shard | 특정 ticker의 상세 evidence와 search payload를 담는 SQLite |
| shard manifest | ticker별 shard path, digest, schema, stats 목록 |
| release manifest | release 전체의 format, layout, digest, status, schema 계약 |
| deep verify | publish/activate 전에 수행하는 무거운 검증 |
| startup check | MCP 시작 전에 수행하는 가벼운 검증 |
| force | mutable 수정이 아니라 새 immutable release candidate를 강제로 만드는 명령 |
| partial rebuild | 바뀐 ticker/company/shard만 다시 만들고 나머지는 재사용하는 build |
| delta publish | prod로 바뀐 파일만 업로드하고 manifest로 검증한 뒤 current를 전환하는 publish |

## 5. Repository 책임

### 5.1 core repository

경로:

```text
~/krw-ontology
```

주요 책임:

```text
v3 source manifest discovery
v3 build DAG
global spine builder
company shard builder
release writer
release verifier
quality scanner
quality repair planner
MCP runtime
CLI
unit/integration tests
```

주요 파일:

```text
src/krw_ontology/agent_index/spine_schema.py
src/krw_ontology/agent_index/spine_builder.py
src/krw_ontology/agent_index/spine_router.py
src/krw_ontology/agent_index/spine_verify.py
src/krw_ontology/agent_index/router.py
src/krw_ontology/release.py
src/krw_ontology/quality/scanner.py
src/krw_ontology/mcp_server/http_server.py
src/krw_ontology/mcp_server/server.py
src/krw_ontology/mcp_server/tools.py
src/krw_ontology/cli/main.py
```

### 5.2 front/runtime repository

경로:

```text
~/krw-ontology-front
```

주요 책임:

```text
Mac worker preflight
launchd install/restart script
MCP production health verification
frontend MCP client health parsing
deploy scripts
```

front 쪽은 index를 build하지 않는다. build와 release의 권위는 core CLI에 있다.
front는 이미 생성된 v3 release가 올바른지 preflight하고 MCP를 기동한다.

## 6. 최종 CLI 계약

### 6.1 최초 설정

일상 명령에서 긴 경로를 반복하지 않기 위해 config를 먼저 저장한다.

```bash
uv run krw-ontology config set running-root ~/krw-ontology-data-running
uv run krw-ontology config set publish-root ~/krw-ontology-data/releases
```

의미:

```text
running-root
  build 입력 artifact가 있는 곳

publish-root
  dev/prod release가 쌓이는 곳
```

이 설정이 있으면 대부분의 명령은 경로 option 없이 실행되어야 한다.

### 6.2 dev release 생성

기본 명령:

```bash
uv run krw-ontology release force
```

의미:

```text
running-root를 읽음
source_manifest.json 생성 또는 갱신
v3 build DAG 실행
global_spine.sqlite 생성
company shards 생성 또는 재사용
manifest.json v3 작성
deep verify 실행
성공하면 dev/current를 새 release로 전환
```

`force`는 현재 release 폴더를 직접 고치는 명령이 아니다.

올바른 의미:

```text
새 immutable release candidate를 만든다
검증이 끝난 뒤 current symlink를 바꾼다
```

잘못된 의미:

```text
현재 current 내부 DB를 직접 덮어쓴다
```

### 6.3 상태 확인

```bash
uv run krw-ontology release status
```

의미:

```text
현재 env current가 어떤 release를 가리키는지 확인
latest worker가 살아 있는지 확인
최근 release 목록 확인
```

### 6.4 로그 보기

```bash
uv run krw-ontology release watch
```

의미:

```text
현재 실행 중이거나 최신 release worker 로그를 따라간다
```

특정 release를 보고 싶을 때는 release id를 준다.

```bash
uv run krw-ontology release watch 20260611_100844
```

### 6.5 dev release 검증

```bash
uv run krw-ontology release verify --env dev
```

의미:

```text
dev/current release manifest 확인
global spine 존재와 digest 확인
shard manifest 확인
company shard 존재와 digest 확인
topology consistency 확인
```

MCP startup에 가까운 가벼운 검증은 다음 명령이다.

```bash
uv run krw-ontology release startup-check --env dev
```

### 6.6 quality check

```bash
uv run krw-ontology quality check --env dev
```

의미:

```text
dev/current v3 release를 읽음
global spine과 company shard의 quality table을 조회
quality issue 요약 출력
release를 수정하지 않음
index를 다시 build하지 않음
```

Quality는 사용자가 원할 때만 실행한다. prod publish의 자동 gate로 강제하지 않는다.

### 6.7 prod publish

권장 명령:

```bash
uv run krw-ontology prod publish-dev
```

의미:

```text
dev/current를 source로 삼음
prod remote preflight 수행
delta upload로 바뀐 파일만 전송
remote manifest와 digest 확인
prod/current를 atomic activate
필요 시 MCP reload와 health 확인
```

긴 명령으로 쓰면 다음 흐름과 같다.

```bash
uv run krw-ontology prod publish --from-env dev --delta
```

최종 운영 CLI에서는 `prod publish-dev`가 더 좋은 사용자 경험이다.

### 6.8 사용하지 말아야 하는 CLI 형태

정상 운영 문서에 남기지 않을 명령 패턴:

```text
--index-path
--layout monolith-and-shards
--rebuild-agent-index
--allow-v2
--accept-legacy
agent_index.sqlite를 직접 지정하는 명령
prod publish가 v1/v2 current를 받아들이는 흐름
```

Debug나 negative test 내부에서만 필요한 경우에는 help text와 문서에서 production
기본처럼 보이지 않게 숨기거나 명확히 분리한다.

## 7. Build DAG

### 7.1 최종 DAG

```text
ConfigResolve
  -> SourceManifestDiscover
  -> SourceManifestVerify
  -> ArtifactDigest
  -> CompanyInputPlan
  -> CompanyProjectionBuild
  -> CompanyShardBuild
  -> SpineFragmentBuild
  -> GlobalSpineMerge
  -> ShardManifestWrite
  -> ReleaseManifestWrite
  -> DeepVerify
  -> CurrentPromote
```

각 노드는 다음 값을 가져야 한다.

```text
node name
input paths
input digests
output paths
output digests
started_at
finished_at
status
cache_hit
reason
```

### 7.2 왜 DAG가 필요한가

DAG는 성능 최적화 장식이 아니다. production correctness에도 필요하다.

이유:

```text
무엇이 바뀌어서 rebuild됐는지 설명 가능
무엇이 안 바뀌어서 skip됐는지 설명 가능
중간 실패 시 어디까지 안전한지 판단 가능
partial rebuild가 correctness를 깨지 않았는지 검증 가능
release manifest가 build 입력과 정확히 연결됨
```

### 7.3 build progress

`indexes/build_progress.jsonl`은 사람이 보는 로그가 아니라 machine-readable
progress stream이어야 한다.

권장 event:

```json
{"event":"node_started","node":"CompanyShardBuild","ticker":"MSFT","ts":"..."}
{"event":"node_finished","node":"CompanyShardBuild","ticker":"MSFT","cache_hit":false,"duration_ms":12345}
{"event":"node_skipped","node":"CompanyShardBuild","ticker":"NVDA","reason":"input_digest_unchanged"}
{"event":"node_failed","node":"GlobalSpineMerge","error":"..."}
```

## 8. Source manifest-only discovery

### 8.1 원칙

Production build 대상은 filesystem을 매번 전부 훑어서 즉석에서 결정하지 않는다.
권위 있는 source manifest가 build input이다.

```text
source manifest = 이번 release가 읽는 artifact 목록의 원장
```

### 8.2 source manifest가 담아야 하는 것

```text
format
schema_version
generated_at
running_root
tickers
artifacts
artifact path
artifact type
artifact digest
artifact size
artifact modified time
company id
filing id
document type
required/optional flag
```

### 8.3 source manifest가 필요한 이유

```text
IO 감소
build input 결정성 확보
누락 artifact 감지
중복 artifact 감지
cache key 안정화
partial rebuild 판단
prod release 재현 가능성 확보
```

### 8.4 누락 처리

Required artifact가 없으면 build는 실패해야 한다.

허용하지 않는 fallback:

```text
근처 폴더에서 비슷한 파일 찾기
이전 release의 파일을 몰래 읽기
빈 shard를 만들어 성공 처리
monolith fallback으로 숨기기
```

Optional artifact는 manifest에 optional로 기록하고, build summary와 quality event에
남겨야 한다.

## 9. Cache와 partial rebuild

### 9.1 cache 레벨

최종 cache는 한 단계가 아니라 여러 단계다.

```text
artifact digest cache
  개별 source artifact 변경 여부

company input cache
  회사 하나의 전체 입력 변경 여부

company projection cache
  회사별 normalized projection 변경 여부

company shard cache
  회사 shard SQLite 재사용 가능 여부

spine fragment cache
  global spine에 merge할 회사별 compact fragment 재사용 여부

global spine merge cache
  전체 routing table과 cross-company edge merge 재사용 가능 여부

verification cache
  동일 manifest/digest 조합의 deep verify 결과 재사용 가능 여부

delta publish cache
  prod remote에 이미 있는 blob/file 재업로드 방지
```

### 9.2 ticker 하나가 바뀐 경우

예: `MSFT` artifact 하나 수정

```text
MSFT source digest 변경
MSFT company input hash 변경
MSFT projection rebuild
MSFT company shard rebuild
MSFT spine fragment rebuild
global spine merge 갱신
shard manifest 갱신
release manifest 갱신
deep verify
dev/current promote
```

변하지 않는 회사:

```text
NVDA shard 재사용
AAPL shard 재사용
XOM shard 재사용
...
```

중요한 점:

```text
global spine은 갱신된다
하지만 90GB monolith 전체 rewrite는 없다
```

### 9.3 새 ticker가 추가된 경우

예: `AVGO` 추가

```text
source manifest에 AVGO artifact 추가
AVGO projection 생성
AVGO shard 생성
AVGO spine fragment 생성
global spine에 AVGO object/edge/topic/entity merge
shard manifest에 AVGO 추가
release manifest 갱신
deep verify
```

기존 ticker shard는 재사용한다.

새 ticker가 cross-company edge를 만들면 global spine merge 단계에서 전체 chain이
갱신된다. 그래도 모든 회사 shard를 다시 만들 필요는 없다.

### 9.4 ontology schema가 바뀐 경우

schema 변경은 두 종류로 나눈다.

```text
compatible schema extension
  새 optional column/table 추가
  기존 query contract 유지
  필요한 shard만 재생성 가능

breaking schema change
  object id 규칙 변경
  edge 의미 변경
  required table 변경
  query contract 변경
```

Breaking schema change는 전체 shard와 global spine을 다시 만들어야 한다.

이 경우에도 원칙은 변하지 않는다.

```text
새 immutable v3 release 생성
old release 직접 수정 금지
v1/v2 호환 serving 금지
manifest schema version bump
deep verify 후 current 전환
```

질문에 대한 답을 명확히 쓰면:

```text
새 ontology schema가 전체 object/edge 의미를 바꾸면 전체 회사 재빌드가 맞다.
하지만 ticker 하나 수정이나 새 ticker 추가 때문에 전체 회사를 다시 맞추는 구조는 아니다.
```

### 9.5 force와 cache의 관계

`release force`는 cache를 무시하고 모든 파일을 새로 쓰라는 뜻이 아니다.

최종 의미:

```text
release transaction은 강제로 시작한다
current와 같은 입력이라도 candidate를 만들 수 있다
하지만 DAG 내부에서는 안전한 cache hit을 사용할 수 있다
```

다만 다음 상황에서는 cache를 쓰면 안 된다.

```text
schema version 변경
builder version 변경
cache key에 포함된 code digest 변경
manifest format 변경
verification rule 변경
operator가 no-cache를 명시
```

## 10. Release manifest 계약

### 10.1 필수 조건

정상 release는 다음 조건을 만족해야 한다.

```text
format == krw-ontology-release/v3
index_layout == global-spine-and-company-shards
status == ready
env == dev|staging|prod
monolith_required == false
indexes.global_spine.path 존재
indexes.global_spine.sha256 존재
indexes.shard_manifest.path 존재
indexes.company_shards 존재
모든 path는 release root 내부 상대 경로
```

### 10.2 금지 조건

다음이면 production path에서 실패해야 한다.

```text
format이 v3가 아님
index_layout이 global-spine-and-company-shards가 아님
monolith_required가 true
global_spine digest 없음
shard digest 없음
path가 release root 밖을 가리킴
status가 ready가 아님
required shard 누락
```

### 10.3 manifest가 해야 할 일

Manifest는 설명 파일이 아니다. runtime과 verifier가 믿는 계약이다.

따라서 manifest에는 다음이 들어가야 한다.

```text
release id
created at
env
format
index layout
schema versions
builder versions
source manifest digest
global spine path/digest/size/stats
shard manifest path/digest
company shard list/path/digest/size/stats
build summary path/digest
verification summary
status
```

## 11. Verification 계층

### 11.1 startup check

MCP startup에서 허용되는 검증:

```text
release root가 존재하는가
current symlink target인가
manifest.json이 존재하는가
manifest format이 v3인가
index_layout이 v3인가
status가 ready인가
monolith_required가 false인가
global_spine.sqlite가 release root 내부에 존재하는가
shard_manifest.json이 release root 내부에 존재하는가
SQLite 파일을 열 수 있는가
필수 metadata table을 짧게 읽을 수 있는가
```

MCP startup에서 금지되는 검증:

```text
PRAGMA integrity_check
81GB급 full file scan
global spine full sha256 계산
모든 company shard sha256 계산
모든 shard SQLite open
smoke query 대량 실행
ranking quality calibration
quality full scan
```

이유:

```text
MCP는 먼저 포트를 열어 health 응답을 해야 한다.
deep correctness는 release publish 전에 끝나야 한다.
startup은 release trust를 빠르게 확인하는 단계다.
```

### 11.2 deep verify

Deep verify는 release 생성 또는 prod publish 전에 수행한다.

검증해야 하는 것:

```text
manifest schema
path containment
digest match
global spine SQLite integrity
shard manifest digest
company shard 존재 여부
company shard SQLite integrity
global object locator consistency
edge endpoint existence
cross-company edge routeability
topic/entity/factor index consistency
required ticker coverage
quality table readability
build summary consistency
```

### 11.3 prod preflight

Prod publish는 기존 healthy MCP를 내리기 전에 target release를 확인해야 한다.

순서:

```text
local dev/current verify
remote disk/permission check
remote target candidate upload
remote manifest v3 check
remote digest check
remote startup-check equivalent
then activate current
then reload MCP
then health check
```

이 순서를 지키지 않으면 v1/v2 mismatch나 corrupt release 때문에 정상 MCP를 먼저
내리는 사고가 생긴다.

## 12. MCP runtime 계약

### 12.1 환경 변수

MCP runtime이 이해해야 하는 핵심 환경 변수:

```text
KRW_ONTOLOGY_ENV
KRW_ONTOLOGY_ROOT
KRW_ONTOLOGY_RELEASE_ROOT
KRW_ONTOLOGY_GLOBAL_SPINE_PATH
```

최종 운영에서는 `KRW_ONTOLOGY_RELEASE_ROOT`가 가장 중요하다.

```text
KRW_ONTOLOGY_RELEASE_ROOT=/data/krw-ontology/releases/prod/current
```

`KRW_ONTOLOGY_GLOBAL_SPINE_PATH`는 runtime이 release manifest를 해석한 뒤 내부적으로
확정하는 값이다. 사용자가 일상적으로 직접 지정하는 option이 아니어야 한다.

### 12.2 health payload

Health payload는 generic `index_path`가 아니라 v3 용어를 써야 한다.

필수 field:

```text
ok
env
release_root
release_id
manifest_format
index_layout
global_spine_path
global_spine_manifest_path
company_shards_present
status
```

금지 field:

```text
index_path
agent_index_path
monolith_path
```

### 12.3 MCP tool schema

외부 MCP tool schema에는 runtime path override가 없어야 한다.

금지 parameter:

```text
root
index_path
global_spine_path
release_root
```

이유:

```text
tool caller가 임의 DB를 지정하면 release trust가 깨진다.
prod MCP는 현재 활성 release만 serving해야 한다.
테스트 편의를 production schema로 노출하면 운영 사고가 난다.
```

테스트는 환경 변수나 fixture setup으로 runtime을 구성하고 tool을 호출해야 한다.

## 13. Router 계약

### 13.1 ticker가 있는 query

```text
query mentions MSFT
  -> global spine에서 MSFT shard locator 확인
  -> MSFT shard open
  -> local search/detail 조회
  -> 필요한 cross-company edge가 있으면 spine에서 연결 회사 확인
  -> 추가 shard fanout 제한적으로 수행
```

### 13.2 ticker가 없는 query

```text
query has no explicit ticker
  -> global spine topic/entity/factor index 조회
  -> candidate ticker ranking
  -> fanout budget 적용
  -> top company shards 조회
  -> answer compose
```

### 13.3 trace/chain query

```text
object id
  -> global_object_locator에서 owning shard 확인
  -> owning shard에서 detail 조회
  -> global spine에서 inbound/outbound edge 확장
  -> 필요한 shard만 추가 조회
```

### 13.4 fallback 금지

Router가 실패했을 때 하면 안 되는 것:

```text
agent_index.sqlite 열기
filesystem에서 임의 shard scan
없는 shard를 무시하고 partial answer를 정상 성공으로 표시
current가 아닌 release를 몰래 읽기
```

실패는 명확히 실패해야 한다. 그래야 missing manifest, missing shard, route bug를
빨리 찾을 수 있다.

## 14. Quality 계약

### 14.1 quality check가 하는 일

Quality check는 release를 검사한다.

```text
release manifest 확인
global spine에서 ticker/object/topic coverage 확인
company shard quality_events 조회
documents/objects/edges의 품질 상태 집계
severity별 issue 출력
repair plan에 필요한 metadata 제공
```

Quality check는 다음을 하지 않는다.

```text
index build
release 수정
current 변경
prod publish 차단
source artifact 자동 수정
```

### 14.2 quality issue 수정 후 흐름

품질 체크에서 문제가 나왔을 때 흐름:

```text
quality check
  -> issue 확인
  -> source artifact 또는 ontology generation logic 수정
  -> release force
  -> release status/watch
  -> quality check 재실행
  -> 만족하면 prod publish-dev
```

품질 문제를 고친 뒤 다시 release를 만들어야 하는 이유:

```text
MCP와 prod는 release를 serving한다.
source만 고쳤다고 current release가 자동으로 바뀌지 않는다.
새 release가 만들어져야 global spine/shard/manifest가 새 상태를 담는다.
```

### 14.3 quality repair plan

Quality repair는 무작정 실행하지 않는다.

권장 구조:

```text
quality repair plan
  issue를 ticker/document/object 단위 작업으로 변환

quality repair show
  operator가 작업 범위 확인

quality repair run
  source/running-root를 수정하는 repair job 실행

release force
  수정 결과를 새 release로 materialize
```

Repair plan도 release를 직접 고치지 않는다.

### 14.4 quality cache

Quality cache는 다음 기준을 포함해야 한다.

```text
release_id
manifest digest
global spine digest
shard manifest digest
quality scanner version
threshold config
```

이 중 하나라도 바뀌면 cache는 stale이다.

## 15. Prod publish 계약

### 15.1 기본 흐름

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release startup-check --env dev
uv run krw-ontology quality check --env dev
uv run krw-ontology prod publish-dev
```

Quality는 사용자가 원할 때만 실행한다. 자동 gate로 강제하지 않는다.

### 15.2 delta upload

Delta upload는 다음 원칙이다.

```text
remote에 이미 같은 digest의 파일이 있으면 재업로드하지 않음
바뀐 shard만 업로드
바뀐 global spine만 업로드
manifest는 마지막에 업로드
remote verify 후 current activate
```

금지:

```text
파일 몇 개 올리고 바로 current 바꾸기
remote manifest 검증 없이 reload
기존 current를 직접 수정
```

### 15.3 rollback

Rollback은 current symlink만 이전 verified release로 돌린다.

```bash
uv run krw-ontology prod rollback
```

Rollback이 하지 않는 일:

```text
DB 파일 수정
release manifest 수정
old release rebuild
```

## 16. Failure model

### 16.1 build 실패

Build 실패 시:

```text
candidate는 current가 되면 안 됨
실패 로그가 logs에 남아야 함
worker state가 failed로 남아야 함
cleanup-interrupted가 quarantine 가능해야 함
```

### 16.2 verify 실패

Verify 실패 시:

```text
current promote 금지
manifest status ready 금지
failure report 기록
원인 path와 ticker를 출력
```

### 16.3 publish 실패

Prod publish 실패 시:

```text
기존 prod/current 유지
새 candidate는 inactive 또는 quarantined
MCP reload 금지 또는 rollback
operator가 재시도 가능해야 함
```

### 16.4 MCP startup 실패

MCP startup 실패는 빠르게 실패해야 한다.

금지:

```text
큰 DB full scan 때문에 몇 분 동안 포트를 열지 못함
timeout만 늘려서 숨김
v1/v2 manifest를 읽어보려고 오래 대기
```

## 17. 개발 작업 순서

### 17.1 Phase A: path/naming 정리

목표:

```text
production runtime surface에서 index_path 사고방식 제거
global_spine_path 용어로 통일
MCP tool 내부 root/index override 제거
```

작업:

```text
tools.py 함수 시그니처에서 root/index_path 제거
regression scripts의 --index-path를 --global-spine-path 또는 env setup으로 변경
unit tests는 env fixture로 runtime 구성
external MCP schema negative test 유지
front health reader는 global_spine_path만 읽음
```

완료 기준:

```bash
rg -n "payload\\.index_path|runtime_index_path|resolved_index_path|retired_indexes" src tests scripts
uv run pytest -q tests/unit/test_mcp_server.py
```

정상 결과:

```text
old generic index_path surface 없음
MCP test 통과
```

### 17.2 Phase B: builder primitive 소유권 정리

목표:

```text
company shard build가 legacy monolith builder의 의미를 빌려 쓰지 않게 정리
```

작업:

```text
agent_index builder의 reusable table writer를 shard writer로 분리
monolith production builder 명명 제거
company shard schema version 명확화
test fixture도 shard/global spine 용어로 변경
```

완료 기준:

```text
release force가 production agent_index.sqlite를 만들지 않음
normal CLI help에 monolith layout이 기본처럼 보이지 않음
builder tests가 shard/global spine output을 직접 검증
```

### 17.3 Phase C: router parity 확대

목표:

```text
모든 user-facing retrieval 기능이 monolith 없이 동작
```

대상:

```text
catalog
query
topic_map
retrieve
trace
chain
quality
compare
plan_query
index_context
company_context
query_context
```

완료 기준:

```text
각 tool이 v3 release fixture에서 성공
각 tool이 missing shard를 명확히 실패로 보고
각 tool이 agent_index.sqlite 없이 통과
external schema에 runtime path override 없음
```

### 17.4 Phase D: quality scale 검증

목표:

```text
quality check가 대용량 v3 release에서도 운영 가능한 시간과 메모리로 동작
```

작업:

```text
quality scanner가 release root를 기준으로만 동작하는지 확인
shard별 streaming scan 적용
global spine summary 우선 사용
quality cache stale guard 검증
repair plan이 source/running-root 수정으로 이어지는지 확인
```

완료 기준:

```text
quality check가 agent_index.sqlite 없이 동작
full 162 ticker release에서 시간/메모리 baseline 측정
quality issue 수정 후 release force로 반영되는 흐름 검증
```

### 17.5 Phase E: prod/front runtime 검증

목표:

```text
front deploy와 launchd가 v3 release만 preflight하고, 기존 healthy MCP를 불필요하게 내리지 않음
```

작업:

```text
mac-worker-preflight가 v3 manifest 확인
verify-mcp-server-production이 global_spine_path health 확인
launchd script가 restart 전에 target release preflight
MCP health timeout은 startup lightweight 기준으로 유지
```

완료 기준:

```text
v1/v2 prod/current면 restart 전에 실패
v3 prod/current면 빠르게 health 응답
health payload에 index_path 없음
```

### 17.6 Phase F: real data baseline

목표:

```text
실제 162 ticker 데이터로 최종 구조 성능과 correctness 확인
```

측정 항목:

```text
initial full build time
single ticker rebuild time
new ticker add time
global spine size
total shard size
release verify time
startup check time
quality check time
prod delta upload time
query latency by tool
```

완료 기준:

```text
production monolith 없이 build/publish/query/quality가 모두 동작
baseline 문서화
병목이 DAG node 단위로 설명 가능
```

## 18. 테스트 matrix

### 18.1 core static checks

```bash
uv run python -m py_compile \
  src/krw_ontology/agent_index/spine_schema.py \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_router.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/release.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/mcp_server/http_server.py \
  src/krw_ontology/mcp_server/server.py \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/cli/main.py
```

### 18.2 unit tests

```bash
uv run pytest -q tests/unit/test_spine_schema.py
uv run pytest -q tests/unit/test_spine_builder.py
uv run pytest -q tests/unit/test_release_v3.py
uv run pytest -q tests/unit/test_mcp_server.py
uv run pytest -q tests/unit/test_quality.py
uv run pytest -q tests/unit/test_cli.py
```

### 18.3 CLI smoke

```bash
uv run krw-ontology release plan
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release startup-check --env dev
uv run krw-ontology quality check --env dev
uv run krw-ontology prod doctor
uv run krw-ontology prod status
```

### 18.4 negative tests

반드시 실패해야 하는 것:

```text
v1 manifest serving
v2 manifest serving
monolith_required true
missing global spine
missing shard manifest
missing required shard
path escape in manifest
status not ready
MCP external schema root/index_path exposure
prod publish from non-v3 dev/current
```

### 18.5 front/runtime tests

```bash
cd ~/krw-ontology-front
npm run typecheck
sh -n scripts/mac-worker-preflight.sh scripts/verify-mcp-server-production.sh scripts/verify-mcp-server-dev.sh
npx vitest run \
  src/lib/deploy-scripts.test.ts \
  src/lib/agent/worker-heartbeat.test.ts \
  src/lib/agent/job-runner.test.ts \
  src/app/api/healthz/deep/route.test.ts
```

## 19. 운영 runbook

### 19.1 평소 dev release 만들기

```bash
cd ~/krw-ontology
uv run krw-ontology release force
uv run krw-ontology release watch
```

끝났는지 확인:

```bash
uv run krw-ontology release status
uv run krw-ontology release startup-check --env dev
```

### 19.2 quality 확인

```bash
uv run krw-ontology quality check --env dev
uv run krw-ontology quality tickers --env dev --severity high
uv run krw-ontology quality explain MSFT --env dev
```

품질 문제를 고친 뒤:

```bash
uv run krw-ontology release force
uv run krw-ontology quality check --env dev
```

### 19.3 prod 반영

```bash
uv run krw-ontology prod doctor
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

### 19.4 rollback

```bash
uv run krw-ontology prod rollback
uv run krw-ontology prod status
```

## 20. 현재 구현 상태 기준

### 20.1 구현 완료로 볼 수 있는 축

현재 branch 기준으로 완료 또는 상당 부분 구현된 축:

```text
v3 global spine schema/build primitive
v3 company shard layout
v3 release manifest writer
v3 startup/deep verifier
non-v3 release hard reject before SQLite open
MCP startup lightweight split
MCP health payload global_spine_path naming
external MCP tool schema root/index_path 제거
CLI release force/status/watch/publish-dev 흐름
build_progress.jsonl 기반 status/watch
quality scanner v3 release root 기준 동작
quality bounded scan의 manifest quality_summary rollup 사용
deep verifier의 quality_summary 재계산 검증
prod remote activation/rollback shard digest와 quality_summary preflight
front preflight/health global_spine_path 인식
```

### 20.2 아직 끝났다고 말하면 안 되는 축

남은 축:

```text
MCP tool 내부 direct-call root/index_path override 정리
builder.py legacy monolith primitive와 company shard primitive 소유권 분리
router parity smoke 확대
quality 대용량 성능 baseline
162 ticker 실데이터 full integration
prod remote cutover rehearsal
```

이 항목이 남아 있으면 "core v3 path가 있다"는 표현은 가능하지만,
"최종 production 전환 완료"라고 말하면 안 된다.

## 21. 코드 리뷰 체크리스트

리뷰어는 다음을 확인한다.

```text
새 코드가 current release 내부를 직접 수정하지 않는가
새 CLI option이 index_path/monolith 사고방식을 다시 노출하지 않는가
manifest path가 release root 밖을 가리킬 수 없는가
startup path에서 deep verify가 실행되지 않는가
prod publish가 restart 전에 target release를 preflight하는가
quality check가 release를 수정하지 않는가
router가 missing shard를 fallback으로 숨기지 않는가
cache key에 schema/builder/source digest가 포함되는가
test fixture가 production 성공 path와 negative fixture를 섞지 않는가
```

## 22. 금지 패턴

다음 패턴은 발견 즉시 수정 대상이다.

```text
open agent_index.sqlite in production runtime
if v3 fails, try v2
if shard missing, scan filesystem
if startup slow, increase timeout
write into current release directory
quality check mutates release
prod publish activates before verify
tool schema accepts root/index_path
debug option appears as normal help path
```

## 23. 완료 정의

최종 완료 조건:

```text
1. release force가 v3 release만 만든다.
2. production release에 indexes/agent_index.sqlite가 필요 없다.
3. manifest format은 krw-ontology-release/v3이다.
4. index_layout은 global-spine-and-company-shards이다.
5. monolith_required는 false이다.
6. MCP startup은 lightweight check 후 빠르게 health를 연다.
7. MCP health payload는 global_spine_path를 사용한다.
8. 외부 MCP tool schema에는 root/index_path가 없다.
9. router는 monolith fallback 없이 query/retrieve/trace/chain/quality를 처리한다.
10. quality check는 release root 기준으로 동작하고 release를 수정하지 않는다.
11. quality issue 수정 후 release force로 새 release가 생성된다.
12. prod publish는 dev/current v3만 받는다.
13. prod publish는 remote preflight 후 atomic activate한다.
14. v1/v2 release는 normal path에서 hard reject된다.
15. missing shard, corrupt digest, path escape는 실패한다.
16. partial rebuild는 ticker 단위 변경을 전체 monolith rebuild 없이 처리한다.
17. delta publish는 바뀐 파일만 업로드한다.
18. 162 ticker 실데이터 baseline이 문서화된다.
19. front launchd/runtime preflight가 v3 contract를 따른다.
20. full targeted test matrix가 통과한다.
```

이 20개를 만족해야 "v3 최종 production 전환 완료"라고 부른다.

## 24. 선임 개발자에게 확인할 질문

남은 구현을 리뷰받을 때 질문은 넓게 묻지 말고 아래처럼 구체적으로 묻는다.

```text
1. global spine이 cross-company chain의 canonical routing source가 되는 데 동의하는가?
2. company shard가 상세 evidence의 canonical serving source가 되는 데 동의하는가?
3. production에서 agent_index.sqlite fallback을 완전히 제거하는 데 동의하는가?
4. v1/v2 release를 normal path에서 hard reject하는 데 동의하는가?
5. MCP startup에서 deep verify를 하지 않고 release publish trust를 전제로 하는 데 동의하는가?
6. quality check가 prod publish 자동 gate가 아니라 operator-invoked check인 데 동의하는가?
7. breaking ontology schema 변경 시 전체 v3 rebuild가 필요하다는 기준에 동의하는가?
8. ticker 추가/수정은 partial rebuild로 처리하되 global spine merge는 갱신하는 데 동의하는가?
9. prod publish는 remote preflight 후 current symlink atomic activate여야 한다는 데 동의하는가?
10. debug monolith는 production bundle 기본 산출물에서 제외하는 데 동의하는가?
```

이 질문들의 답이 모두 yes라면 현재 최종 방향과 일치한다.

## 25. 마지막 판단 기준

이 전환의 핵심은 "monolith를 없애서 빨라진다"가 아니다.

정확한 핵심은 다음이다.

```text
전체 ontology chain은 global spine에 남긴다.
상세 evidence는 company shard에 둔다.
release는 immutable하게 만든다.
runtime은 current release만 읽는다.
검증은 publish 전에 끝낸다.
startup은 가볍게 한다.
quality는 release를 검사하되 수정하지 않는다.
prod publish는 검증된 v3만 원자적으로 activate한다.
```

이 구조가 되어야 데이터가 커져도 build, verify, startup, publish, query를 각각
독립적으로 최적화할 수 있다.

## 26. 개발자 상세 구현 지시서

이 장은 실제 개발자가 코드를 수정할 때 따르는 작업 지시서다. 앞 장들이
아키텍처와 운영 기준을 설명한다면, 이 장은 "어느 파일을 어떤 책임으로 고칠지",
"어떤 public surface를 남기고 어떤 surface를 제거할지", "어떤 테스트가 끝나야
완료인지"를 구체적으로 고정한다.

### 26.1 최종 구현 원칙

모든 코드 변경은 아래 원칙을 만족해야 한다.

```text
normal production path는 v3 release만 처리한다.
v3 release는 global spine + company shards만 production index로 가진다.
agent_index.sqlite는 production serving fallback이 아니다.
current release directory에는 쓰지 않는다.
runtime은 release manifest를 신뢰하되 startup에서는 가벼운 검증만 한다.
deep verification은 release 생성, publish, preflight 단계에서만 수행한다.
quality check는 release를 읽고 보고서를 만들 뿐 release를 수정하지 않는다.
CLI는 operator가 보통 짧은 명령만 쓰게 한다.
MCP external tool schema에는 root/index_path/release path override가 없다.
missing shard, corrupt manifest, path escape는 성공으로 숨기지 않는다.
```

이 원칙과 충돌하는 코드는 legacy compatibility가 아니라 final contract 위반으로
본다.

### 26.2 핵심 파일 책임

각 파일의 최종 책임은 다음과 같다.

```text
src/krw_ontology/release.py
  v3 immutable release 생성, manifest 작성, startup/deep verifier, prod publish
  trust boundary 소유.

src/krw_ontology/agent_index/spine_schema.py
  global spine SQLite schema와 schema version 소유.

src/krw_ontology/agent_index/spine_builder.py
  source manifest, company shard fragment, global spine merge, shard manifest 생성.

src/krw_ontology/agent_index/spine_verify.py
  v3 global spine과 shard topology 검증.

src/krw_ontology/agent_index/spine_router.py
  runtime query routing. monolith fallback 없이 global spine과 company shard만 사용.

src/krw_ontology/agent_index/router.py
  public open_ontology_store 진입점. normal path는 v3 spine routing만 허용.

src/krw_ontology/agent_index/builder.py
  shard build에 필요한 reusable table writer와 과거 builder primitive 보유.
  최종 정리 시 monolith production builder 명명과 public exposure를 제거한다.

src/krw_ontology/mcp_server/http_server.py
  MCP HTTP startup. startup에서는 lightweight release check만 수행.

src/krw_ontology/mcp_server/server.py
  MCP process/runtime configuration. operator 입력에서 release trust path를 resolve.

src/krw_ontology/mcp_server/tools.py
  MCP tools. caller가 root/index_path를 지정하지 못하고 active release만 조회.

src/krw_ontology/quality/scanner.py
  v3 release root 기준 quality scan. global spine과 company shards를 읽음.

src/krw_ontology/quality/models.py
  quality report, repair plan, stale guard fingerprint model.

src/krw_ontology/cli/main.py
  operator-facing command surface. release/quality/prod 명령을 짧고 안전하게 제공.

src/krw_ontology/config/paths.py
  running-root, publish-root, env 기본값 resolve.

src/krw_ontology/web_catalog.py
  v3 release topology에서 web catalog export. monolith index path에 의존하지 않음.
```

테스트 파일 책임은 다음과 같다.

```text
tests/unit/test_spine_schema.py
  global spine schema와 version contract 검증.

tests/unit/test_spine_builder.py
  shard build, spine merge, shard manifest, deterministic build 검증.

tests/unit/test_release_v3.py
  release manifest, startup/deep verification, v1/v2 hard reject 검증.

tests/unit/test_mcp_server.py
  MCP tools가 v3 release에서 동작하고 path override를 노출하지 않는지 검증.

tests/unit/test_mcp_tool_input_aliases.py
  external schema alias가 root/index_path를 다시 열지 않는지 검증.

tests/unit/test_quality.py
  quality scanner, repair plan stale guard, missing shard diagnostics 검증.

tests/unit/test_cli.py
  operator CLI default, short command, status/watch/cancel, invalid input 검증.

tests/unit/test_agent_index.py
  public agent index API가 v3-only인지, legacy routing이 normal path에서 막히는지 검증.
```

### 26.3 구현 순서

최종 구현은 아래 순서로 진행한다. 순서를 바꾸면 test failure 원인을 추적하기
어려워진다.

```text
1. public API surface 잠금
2. release manifest와 verifier v3-only 고정
3. builder가 production agent_index.sqlite를 만들지 않도록 정리
4. global spine + company shard build DAG 확정
5. router와 MCP tool parity 확장
6. quality scanner/repair plan v3 release 기준 확정
7. CLI default와 background worker UX 정리
8. prod publish와 front preflight 연결
9. full real-data baseline 측정
10. legacy reference와 debug-only path 격리
```

각 단계는 다음 단계로 넘어가기 전에 targeted test를 통과해야 한다.

### 26.4 Public API surface 잠금

최종 public entrypoint는 `open_ontology_store()`가 v3 global spine release만 여는
형태다.

허용:

```python
open_ontology_store(global_spine_path)
open_ontology_store(global_spine_path, routing="spine")
```

비허용:

```python
open_ontology_store(agent_index_path, routing="monolith")
open_ontology_store(agent_index_path, routing="shards")
open_ontology_store(any_path, routing="auto")
```

저수준 `OntologyStore`는 company shard 내부 query와 test fixture 용도로 남길 수
있다. 그러나 normal public API가 이를 production fallback으로 호출하면 안 된다.

완료 기준:

```bash
rg -n 'routing="monolith"|routing="shards"|routing="auto"|OntologyStoreRouter' src tests
uv run pytest -q tests/unit/test_agent_index.py
```

허용되는 잔여 mention은 negative test와 명시적 legacy rejection test뿐이다.

### 26.5 Release manifest contract

v3 manifest의 필수 contract:

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
      "sha256": "...",
      "size_bytes": 123
    },
    "shard_manifest": {
      "path": "indexes/shard_manifest.json",
      "sha256": "...",
      "size_bytes": 123
    },
    "company_shards": {
      "MSFT": {
        "path": "indexes/companies/MSFT.sqlite",
        "sha256": "...",
        "size_bytes": 123
      }
    }
  }
}
```

금지:

```text
index_sha256만 있고 indexes가 없는 v2-style manifest
indexes.agent_index를 production required field로 사용
absolute path 저장
release root 밖 path 저장
status != ready인데 current로 promote
monolith_required true
```

검증 함수는 v1/v2를 SQLite open 전에 거절해야 한다. 이유는 81GB급 legacy DB에
대한 integrity check나 sha256 계산이 시작되면 startup/publish timeout 문제가
다시 생기기 때문이다.

### 26.6 Build DAG contract

최종 DAG는 아래 의미를 가진다.

```text
SourceDiscovery
  running-root와 canonical manifest에서 build input 목록 결정

InputFingerprint
  source artifact digest, schema version, builder version, ticker 목록 계산

CompanyShardBuild
  변경된 ticker shard만 생성 또는 cache reuse

SpineFragmentBuild
  shard에서 global spine merge용 compact fragment 생성 또는 reuse

GlobalSpineMerge
  모든 valid fragment를 deterministic order로 merge

ShardManifestWrite
  ticker -> shard path/digest/stats 기록

ReleaseManifestWrite
  v3 manifest 작성

DeepVerify
  manifest, spine, shard topology, digest, path containment 검증

AtomicPromote
  current symlink를 새 release로 전환
```

각 node는 다음 값을 기록해야 한다.

```text
node_name
input_hash
output_hash
cache_hit
started_at
finished_at
duration_ms
affected_tickers
output_paths
failure_reason
```

이 기록은 `indexes/build_plan.json`, `indexes/build_summary.json`,
`indexes/build_progress.jsonl`에 나뉘어 저장할 수 있다.

### 26.7 Cache key contract

cache key는 "파일이 같아 보인다"가 아니라 "같은 최종 산출물을 만들 수 있다"를
보장해야 한다.

company shard cache key:

```text
env
ticker
source artifact digests for ticker
source manifest version
ontology schema version
company shard schema version
company shard builder version
normalizer version
quality event schema version
build options that affect output
```

spine fragment cache key:

```text
ticker
company shard sha256
global spine schema version
fragment builder version
cross-company edge extraction version
```

global spine merge cache key:

```text
sorted fragment digests
global spine schema version
merge builder version
cross-company resolver version
```

cache hit 시에도 다음은 검증한다.

```text
cached output file exists
cached output digest matches metadata
schema version matches expected
path is inside release/cache root
```

검증 실패 시 cache miss로 처리하고 rebuild한다. corrupt cache를 성공으로 숨기면
안 된다.

### 26.8 Company shard contract

company shard는 한 ticker의 상세 evidence serving source다.

필수 속성:

```text
한 shard는 한 ticker만 포함한다.
objects/documents/edges/quality_events는 local ticker 상세 조회에 충분해야 한다.
global object id는 release 전체에서 안정적이어야 한다.
document id는 global spine document catalog와 연결 가능해야 한다.
quality scanner가 shard 단위로 streaming scan할 수 있어야 한다.
```

금지:

```text
shard build가 agent_index.sqlite monolith를 입력으로 요구
shard missing 시 runtime이 filesystem scan으로 임의 DB 발견
shard 안의 ticker와 shard manifest ticker가 다름
schema version 없는 shard를 정상으로 인정
```

### 26.9 Global spine contract

global spine은 전체 ontology chain의 routing source다.

반드시 담아야 하는 것:

```text
release metadata
ticker catalog
company shard locator
object locator
document catalog
topic/entity/factor index
cross-company edge summary
chain expansion index
query candidate index
quality summary
```

담지 않아도 되는 것:

```text
모든 원문 quote 전문
모든 document body 전문
회사별 dense local evidence payload 전체
debug-only parity table
```

global spine은 "전체 chain"을 보존한다. 단, detail payload를 모두 들고 있지 않고
필요한 shard를 찾는 지도 역할을 한다.

### 26.10 Router contract

router는 다음 규칙으로 동작해야 한다.

```text
ticker가 명확한 query
  global spine에서 ticker shard locator 확인
  해당 shard만 open
  local detail 조회

ticker가 없는 query
  global spine에서 topic/entity/factor 후보 검색
  candidate ticker ranking
  제한된 shard fanout
  evidence merge

trace
  global object locator로 owning shard 확인
  owning shard detail 조회
  global spine의 edge summary로 연결 확장

chain
  global chain index로 endpoint 후보 찾기
  필요한 shard만 lazy open

compare
  global normalizer와 ticker catalog로 비교 대상 확정
  각 ticker shard fanout
  merged comparison 반환

quality
  global summary + shard quality_events 조회
```

router가 절대 하면 안 되는 것:

```text
monolith fallback
missing shard 무시
모든 shard full scan을 기본값으로 수행
caller가 준 임의 index_path open
current release 밖 파일 open
```

missing shard는 성공 결과가 아니라 release integrity 문제다. tool별로 사람이
이해할 수 있는 payload를 반환하되, `not_answerable_from_current_release` 같은
상태를 명확히 표시해야 한다.

### 26.11 MCP tool contract

MCP external tool input schema에서 금지되는 필드:

```text
root
index_path
runtime_index_path
resolved_index_path
global_spine_path
release_root
manifest_path
```

tool caller가 DB 위치를 지정하지 못해야 하는 이유:

```text
active release trust가 깨진다.
prod MCP가 current가 아닌 release를 serving할 수 있다.
검증되지 않은 local DB가 답변 근거가 될 수 있다.
path escape와 stale data 문제가 생긴다.
```

MCP는 process startup 때 내부 설정으로 release root를 resolve한다.

```text
KRW_ONTOLOGY_ENV
KRW_ONTOLOGY_PUBLISH_ROOT
KRW_ONTOLOGY_RELEASE_ROOT
```

위 설정에서 active release를 결정하고, tool 실행 시에는 이미 결정된
`OntologySpineRouter`만 사용한다.

### 26.12 MCP startup contract

startup에서 해야 하는 것:

```text
release root 존재 확인
current symlink 여부 확인
manifest.json 존재 확인
format == krw-ontology-release/v3 확인
env/status/index_layout/monolith_required 확인
global_spine path가 release root 내부인지 확인
shard_manifest path가 release root 내부인지 확인
파일 존재 확인
SQLite open 가능 여부만 짧게 확인
포트 open
```

startup에서 하면 안 되는 것:

```text
PRAGMA integrity_check on large DB
global spine full sha256
all company shard sha256
all shard SQLite open
smoke query
ranking quality test
full count scan
```

startup은 release 생성/배포 단계에서 이미 deep verify가 끝났다는 trust를 전제로
한다. 이것이 startup을 빠르게 만드는 핵심이다.

### 26.13 Quality contract

quality check는 operator가 원할 때 실행하는 read-only 검사다.

기본 명령:

```bash
uv run krw-ontology quality check --env dev
```

동작:

```text
publish-root/env/current resolve
manifest v3 확인
global spine open
shard_manifest open
필요한 company shard를 순차/streaming scan
quality event 집계
report 출력
```

quality check가 하지 않는 것:

```text
release build
release mutate
prod publish gate 강제
current symlink 변경
quality issue 자동 수정
```

quality issue를 수정한 뒤 다시 `release force`가 필요한 이유:

```text
수정은 source/running-root 쪽에 반영된다.
MCP/prod는 source를 직접 serving하지 않는다.
새 release를 만들어야 global spine, shard, manifest가 수정 결과를 담는다.
```

repair plan stale guard는 다음 값을 포함해야 한다.

```text
release_id
manifest_sha256
global_spine_sha256
shard_manifest_sha256
source_manifest_sha256
created_at
target_tickers
issue_ids
```

이 중 하나라도 달라지면 기존 repair plan은 stale이다.

### 26.14 CLI 최종 UX

operator가 자주 쓰는 명령은 짧아야 한다.

```bash
uv run krw-ontology release force
uv run krw-ontology release status
uv run krw-ontology release watch
uv run krw-ontology release startup-check --env dev
uv run krw-ontology quality check --env dev
uv run krw-ontology prod doctor
uv run krw-ontology prod publish-dev
uv run krw-ontology prod status
```

긴 경로 옵션은 CI나 특수 환경에서만 쓴다.

```bash
uv run krw-ontology release force \
  --from-root ~/krw-ontology-data-running \
  --releases-root ~/krw-ontology-data/releases \
  --env dev
```

`release force`의 의미:

```text
새 immutable release transaction을 강제로 시작한다.
바뀐 shard는 rebuild한다.
안 바뀐 shard는 cache를 재사용할 수 있다.
current는 검증이 끝난 뒤에만 새 release로 바뀐다.
```

`force`가 의미하지 않는 것:

```text
모든 source를 무조건 다시 다운로드
모든 shard를 무조건 새로 write
cache 사용 금지
prod 자동 publish
quality 자동 수정
```

별도의 `--no-cache`가 있다면 그것만 cache 재사용 금지를 의미해야 한다.

### 26.15 Prod publish contract

prod publish는 dev/current v3 release를 prod/current로 안전하게 반영한다.

순서:

```text
dev/current resolve
dev manifest v3 확인
deep verification freshness 확인
prod target staging directory 준비
delta copy/upload
remote manifest digest 확인
prod candidate startup-check
global spine cheap schema/metadata 확인
shard manifest digest 확인
company shard digest 확인
shard_manifest quality_summary 재계산 확인
prod current atomic activate
MCP restart or reload
health release_id 확인
```

절대 금지:

```text
target preflight 전에 기존 healthy MCP 중지
v1/v2 release를 prod로 publish
copy 중인 directory를 current로 지정
digest mismatch 무시
health release_id 미확인
```

### 26.16 Front/launchd 연동 contract

`krw-ontology-front` 쪽 deploy script는 다음 순서여야 한다.

```text
target prod release resolve
v3 startup-check 실행
manifest env/status/layout 확인
global_spine_path health expectation 준비
launchd restart
/health poll
health payload release_id/global_spine_path/index_layout 확인
```

health timeout을 늘리는 것은 근본 해결이 아니다. startup 자체가 lightweight여야
한다.

### 26.17 Error message contract

운영자가 바로 원인을 알 수 있도록 error는 구체적이어야 한다.

좋은 예:

```text
release format krw-ontology-release/v2 is not supported by v3 runtime
missing required shard MSFT at indexes/companies/MSFT.sqlite
manifest path escapes release root: ../outside.sqlite
global spine sha256 mismatch
current is not a symlink: /data/releases/prod/current
tool input field index_path is not allowed in production MCP
```

나쁜 예:

```text
invalid release
sqlite error
not found
failed
timeout
```

실패는 숨기면 안 되지만, 사용자가 다음 행동을 알 수 있게 해야 한다.

### 26.18 Logging/progress contract

대용량 build는 오래 걸린다. 그래서 operator가 진행 상태를 볼 수 있어야 한다.

`build_progress.jsonl`에는 최소한 다음 이벤트가 있어야 한다.

```text
release_started
source_discovery_started
source_discovery_completed
company_shard_cache_hit
company_shard_build_started
company_shard_build_completed
spine_fragment_cache_hit
spine_fragment_build_completed
global_spine_merge_started
global_spine_merge_completed
manifest_written
deep_verify_started
deep_verify_completed
current_promoted
release_completed
release_failed
```

각 이벤트는 다음을 포함한다.

```text
timestamp
release_id
node
ticker
message
duration_ms
input_hash
output_hash
path
error
```

`release watch`는 이 파일을 읽어 사람이 이해할 수 있게 보여준다.

### 26.19 Real-data baseline

최종 전환 전 실제 데이터로 반드시 측정한다.

측정 명령 예:

```bash
time uv run krw-ontology release force --foreground
uv run krw-ontology release status
uv run krw-ontology release startup-check --env dev
time uv run krw-ontology quality check --env dev
```

기록할 값:

```text
release_id
ticker_count
source artifact count
changed ticker count
cache hit count
company shard build time p50/p95/max
global spine merge time
deep verify time
startup check time
quality check time
global spine size
total shard size
largest shard size
prod delta upload size
MCP health startup latency
query latency for catalog/query/retrieve/trace/chain/compare/quality
```

baseline은 "빠르다/느리다" 감상이 아니라 다음 최적화의 기준 데이터다.

### 26.20 Code review 질문

PR 또는 큰 patch 리뷰 때 아래 질문에 모두 답해야 한다.

```text
이 변경이 v1/v2 normal serving path를 다시 열지 않는가?
이 변경이 production agent_index.sqlite 의존성을 다시 만들지 않는가?
이 변경이 current release directory에 쓰지 않는가?
이 변경이 startup에서 expensive verify를 실행하지 않는가?
이 변경이 MCP external schema에 path override를 노출하지 않는가?
이 변경이 missing shard를 fallback으로 숨기지 않는가?
이 변경이 quality check를 mutating operation으로 만들지 않는가?
이 변경이 cache key에 schema/builder/source digest를 포함하는가?
이 변경의 negative test가 있는가?
이 변경을 162 ticker release에서 측정할 수 있는가?
```

하나라도 답이 불명확하면 final production 전환 작업으로 merge하면 안 된다.

### 26.21 개발자가 바로 실행할 검증 명령

작은 변경 후:

```bash
uv run pytest -q tests/unit/test_agent_index.py tests/unit/test_mcp_server.py --maxfail=10
```

release/quality 변경 후:

```bash
uv run pytest -q \
  tests/unit/test_release_v3.py \
  tests/unit/test_quality.py \
  tests/unit/test_cli.py \
  --maxfail=10
```

spine/builder 변경 후:

```bash
uv run pytest -q \
  tests/unit/test_spine_schema.py \
  tests/unit/test_spine_builder.py \
  tests/unit/test_agent_index.py \
  --maxfail=10
```

MCP schema 변경 후:

```bash
uv run pytest -q \
  tests/unit/test_mcp_server.py \
  tests/unit/test_mcp_tool_input_aliases.py \
  --maxfail=10
```

최종 targeted matrix:

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

정적 확인:

```bash
python3 -m py_compile \
  src/krw_ontology/agent_index/router.py \
  src/krw_ontology/agent_index/spine_router.py \
  src/krw_ontology/agent_index/spine_builder.py \
  src/krw_ontology/agent_index/spine_schema.py \
  src/krw_ontology/agent_index/spine_verify.py \
  src/krw_ontology/release.py \
  src/krw_ontology/quality/scanner.py \
  src/krw_ontology/mcp_server/tools.py \
  src/krw_ontology/cli/main.py

git diff --check
```

### 26.22 최종 handoff 문장

개발 handoff에는 아래 문장이 들어가야 한다.

```text
이 branch의 production normal path는 v3 release만 대상으로 한다.
runtime serving은 global spine + company shards만 사용한다.
agent_index.sqlite monolith는 production fallback이 아니다.
quality check는 release read-only 검사다.
MCP startup은 lightweight release trust check만 수행한다.
deep verification은 release/publish/preflight 단계에서 수행한다.
```

이 문장을 쓸 수 없으면 아직 최종형 전환이 끝난 것이 아니다.
