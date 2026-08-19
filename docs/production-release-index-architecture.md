# Production release transaction and agent index architecture

> 2026-06-12 historical-only notice: 이 문서는 v2 `monolith-and-shards` 설계
> 배경과 구현 이력만 보존한다. 현재 production 구현의 source of truth가 아니며,
> 새 코드나 운영 절차의 근거로 사용하지 않는다. 최종 기준은
> [`v3-final-production-master-development-guide.md`](v3-final-production-master-development-guide.md)
> 이다. v3는 production monolith fallback을 제거하고 `global_spine.sqlite`와
> company shards만으로 serving한다.

이 문서는 `krw-ontology`의 publish, release, agent index build, MCP serving 구조를 장기 최선 기준으로 재설계하기 위한 개발 문서다.

현재 구현 계약과 변경 절차의 source of truth는
[`v3-final-production-master-development-guide.md`](v3-final-production-master-development-guide.md)다.
이 문서는 v2 설계 배경, 상세 대안, 장기 판단 근거만 보존한다.

핵심 결론:

- mutable stable root 직접 수정 방식은 폐기한다.
- artifact publish와 index publish는 하나의 atomic release transaction으로 통합한다.
- 모든 publish 계열 CLI는 candidate release를 만들고, artifact를 반영하고, index를 빌드하고, verification을 통과한 뒤에만 current를 promote한다.
- 실패하면 기존 current는 절대 바뀌지 않는다.
- index build는 BuildPlan, content-addressed fragment cache, parallel artifact compile, single-writer SQLite merge, deterministic output, shard-aware release layout으로 전환한다.
- MCP serving은 release-aware connection pool을 사용한다. 새 요청은 새 current release를 열고, in-flight 요청은 pinned old release로 끝낸다.

이 문서의 구현 단계는 "임시 모드"가 아니다. 각 단계는 그 자체로 production invariant를 만족해야 한다.

## 0. 구현 현황

이 문서는 장기 목표와 구현 계획을 모두 담는다. 2026-06-11 기준으로 다음 항목은 1차 구현에 반영됐다.

- `build_agent_index`는 같은 디렉터리의 임시 SQLite 파일에 빌드한 뒤 verification을 통과해야 target index를 atomic replace한다.
- agent index verification은 SQLite integrity check, 필수 table, metadata schema version, 핵심 row count를 검사한다.
- index build는 `build_summary.json`을 남기고, progress log는 build 시작 시점에 새 run 기준으로 정리한다.
- local release verification은 agent index verification 결과를 포함한다.
- `release publish-dev`는 source root를 release root로 materialize한 뒤 release 내부 index를 빌드한다.
- `publish-ticker`, `update-ticker --publish`, `build-research-pipeline --publish-root`, `queue run` publish path는 stable root 직접 수정 대신 release transaction helper를 사용한다.
- queue worker는 job 성공 시 ticker를 pending release batch로 모으고, drain/stop/max-jobs 시점에 release root 생성, artifact overlay, index build, verification, current promotion을 한 번에 수행한다.
- `publish-root`가 releases root, env root, 또는 기존 prepared release root 형태여도 publish resolver가 releases root와 env를 추론해 nested release 생성을 피한다.
- `plan_agent_index` BuildPlan API를 추가했다. artifact input bytes, builder/schema versions 기반의 deterministic content hash/cache key를 만들고, cache hit/miss, dirty tickers, planned workers/layout을 `build_plan.json`과 `build_summary.json`에 기록한다.
- fragment cache hit는 단순 파일 존재가 아니라 SQLite `PRAGMA integrity_check`, `fragment_metadata` table, cache key/content hash/version matrix metadata 검증을 통과해야 인정한다.
- artifact fragment compiler와 single-writer merge primitive를 추가했다. 빌드는 artifact별 SQLite fragment를 compile/cache하고, 최종 index writer가 verified fragment의 base rows를 merge한 뒤 derived tables를 전역 rebuild한다.
- fragment compile은 `workers > 1`이고 artifact가 2개 이상이면 `ProcessPoolExecutor`로 병렬 실행한다. 최종 merge는 plan 순서대로 single writer가 수행한다.
- fragment worker submission은 estimated input bytes가 큰 artifact부터 시작하고 상대 경로로 tie-break한다. 완료 결과는 BuildPlan 순서로 되돌려 merge 결정성을 유지한다.
- `AGENT_INDEX_BUILDER_VERSION`은 cache key, fragment metadata, BuildPlan, final index build metadata에 기록되어 builder contract 변경 시 명시적인 cache invalidation을 제공한다.
- corrupt fragment cache는 release/build 실패로 전파하지 않고 source artifact에서 재compile해 atomic replace한다.
- `index plan --cache-root --workers --layout`으로 SQLite 산출물 없이 plan을 확인할 수 있다.
- `index plan`, `index build`, `index verify` CLI를 canonical index command surface로 사용한다.
- `index cache status`, `index cache gc` CLI를 추가했다. status는 fragment verification과 현재 build plan 기준 referenced/unreferenced 상태를 보여 주고, gc는 기본 dry-run이며 `--yes`가 있을 때만 cache root 내부의 invalid/stale SQLite fragment와 sidecar를 삭제한다.
- `build_agent_index(..., layout="monolith-and-shards")`는 monolith output과 함께 `indexes/global_catalog.sqlite`, `indexes/global_topics.sqlite`, `indexes/companies/<TICKER>.sqlite`, `indexes/shard_manifest.json`을 생성한다.
- company shard는 기존 agent index schema를 유지하므로 `OntologyStore`가 직접 열 수 있고, shard verification은 catalog count, shard metadata, shard file hash, shard 합계와 monolith count를 검증한다.
- global topics index는 `company_topic_index`, `company_topic_fts`, `company_topic_source_objects`를 monolith와 count-matched output으로 분리한다. verification은 global topics integrity, metadata role/version, monolith topic count 일치를 검사한다.
- release manifest는 shard layout 요약(`index_shards_present`, `global_catalog_path`, `global_topics_path`, `company_shards_dir`, `company_shard_count`)을 기록하고, release verification은 shard layout이 존재하면 `verify_index_shards`를 함께 실행한다.
- release transaction 성공 경로는 `verify/release_verify.json`을 자동 생성한다. report는 verification payload, file-level trace, file SHA-256, deterministic `reproducibility_hash`를 포함하며 `verify/` 자체는 hash input에서 제외해 report 재작성으로 hash가 흔들리지 않게 한다.
- `release verify --write-report`, `release promote`, `release rollback`, `release finalize-dev`, `release publish-dev`, ticker publish batch는 release verification report를 생성하거나 갱신한다.
- release verification은 serving SDK smoke gate를 포함할 수 있다. production publish/promote 경로는 `open_ontology_store`로 index/router를 열고 `index_context`, `list_companies`, `list_documents`, ticker-scoped compact query, topic discovery, trace sample, compare sample을 실행한다. schema/API 오류가 나면 promote하지 않는다.
- shard layout이 있으면 release smoke는 global topics router와 monolith topic discovery를 deterministic sample topic set으로 실행하고 top topic id overlap을 검증한다. overlap 결과는 `verify/smoke_queries.json`과 smoke baseline hash에 포함되어 global topic ranking regression을 promote 전에 잡는다.
- release verification은 cross-shard/global-topic ranking 품질을 `verify/ranking_quality.json`으로 별도 기록한다. 이 리포트는 route mode, fallback 여부, router/monolith top topic ids, overlap topic ids, overlap ratio, rank delta, 적용 threshold와 source(`argument`, `env:<VAR>`, `default`), deterministic `ranking_quality_hash`를 포함한다.
- `release verify`는 `--ranking-top-k`, `--ranking-min-overlap-ratio`, `--ranking-min-overlap-count`, `--ranking-max-rank-delta`, `--ranking-sample-limit` 옵션으로 global-topic ranking gate threshold를 명시적으로 주입할 수 있다. 명시 옵션은 env var보다 우선한다.
- `release calibrate-ranking-thresholds`는 여러 `verify/ranking_quality.json`을 읽어 관측된 overlap/rank delta floor 기반의 권장 ranking threshold와 deterministic `calibration_hash`를 생성한다.
- release verification은 MCP tool wrapper smoke도 실행한다. `index_context`, `catalog`는 빈 release에서도 실행하고, sample ticker가 있으면 `query`, `query_context`, `topic_map`, `quality`, `retrieve`, `trace`, 가능하면 `compare`까지 JSON tool response를 검증해 SDK 아래의 MCP shaping/alias/format layer 회귀도 promote 전에 잡는다.
- `release verify --update-smoke-baseline`은 `verify/smoke_queries.baseline.json`을 갱신하고, `release verify --smoke-baseline <path>`는 현재 `verify/smoke_queries.json`의 deterministic `smoke_hash`를 baseline과 비교한다. mismatch는 verification failure로 처리한다.
- `prod publish`는 source root를 바로 tar로 묶지 않고 로컬 temp prod candidate를 materialize한다. prod manifest, `verify/release_verify.json`, `verify/smoke_queries.json`, `verify/ranking_quality.json`을 생성하고 smoke/ranking verification을 통과한 candidate만 bundle로 업로드한다.
- remote activation script는 bundle 안의 manifest/index/verification/smoke/ranking artifact를 확인하고 `ranking_quality_hash` 일치까지 검증한 뒤 current를 전환한다. reload/health 실패 시 rollback하면서 release 외부 `activation_logs/<release_id>.log`에 실패 사유를 남긴다.
- `OntologyStoreRouter`와 `open_ontology_store`를 추가했다. shard manifest가 있으면 ticker-scoped query/context/trace/quality는 company shard로 route하고, unscoped/multi-ticker topic discovery는 global topics DB로 route하며, multi-ticker compare는 shard fan-out 후 기존 payload shape로 합친다.
- MCP persistent store pool은 `open_ontology_store`를 사용한다. `current` symlink가 새 release로 바뀌면 새 acquire는 새 resolved index signature로 회전하고, 기존 in-flight lease는 old release store로 끝까지 처리된다.
- MCP persistent store pool은 active bucket과 retired bucket을 구분한다. current 전환 후 old release in-flight lease가 남아 있으면 `retired_leased`, `retired_indexes`, `last_rotation`에 표시되고, lease가 반환되면 old store를 닫고 retired bucket을 제거한다. Lease는 signature뿐 아니라 generation으로도 pinning되어 A→B→A rollback/roll-forward에서도 old A lease가 new A bucket으로 섞이지 않는다.
- MCP health payload는 manifest 기반 shard layout 상태와 `mcp_store_hot_swap` 요약을 SQLite open 없이 노출한다.
- MCP `/metrics` endpoint는 release id/env label, manifest count, shard/topic count, store rotation/pending/retired lease metrics를 Prometheus text format으로 노출한다.
- `ops/observability/prometheus-krw-ontology-mcp-alerts.yml`, `ops/observability/alertmanager-krw-ontology-mcp.yml`, `ops/observability/grafana-krw-ontology-mcp-dashboard.json`은 `/metrics` 기반 external alert/dashboard 산출물이다.
- `krw-ontology observability render-prometheus-alerts`는 Prometheus alert rule template에 운영 threshold를 env var/옵션으로 주입해 최종 YAML을 atomic write한다.
- `krw-ontology observability render-alertmanager`는 Alertmanager template에 운영 receiver webhook URL을 env var/옵션으로 주입해 최종 YAML을 atomic write한다. CLI 출력에는 URL/secret을 노출하지 않는다.
- `krw-ontology observability doctor`는 렌더링된 Prometheus/Alertmanager 설정을 검증하고 prod에서 localhost/loopback receiver가 남아 있으면 실패한다. `--write-report`는 config path/SHA-256, 검증 결과, deterministic `audit_hash`를 남기며 webhook URL은 포함하지 않는다.
- remote prod activation script는 release 외부 `activation_logs/<release_id>.log`와 `activation_events/<release_id>.jsonl` structured event log를 쓰고, health check는 HTTP success뿐 아니라 JSON `ok: true`와 `release_id` 일치를 확인한다.
- release manifest writer와 verifier는 `manifest.json`/`krw-ontology-release/v2`만 지원하고, monolith, global catalog, global topics, company shards, shard manifest의 상대 경로, SHA-256, count를 모두 검증한다.
- `release preview`, `release deploy`, `release force`, `release status`, `release rollback`, `release history` 사용자용 CLI와 `release plan`, `release build`, `release publish`, `release import-current`, `release list`, `release inspect`, `release gc`, `index inspect`, `index explain-last-build` 운영/디버그 CLI를 추가했다.
- full-root와 ticker release publish는 verified current의 deterministic artifact signature가 동일하면 no-op하며, `--force-release`로 명시적으로 우회할 수 있다.
- local candidate build 실패는 `<env>/failed/<release_id>/failure.json`으로 격리되고, remote activation의 pre-switch 실패도 remote `failed/` namespace로 격리된다.
- local promote/rollback은 선택적 reload command와 JSON health gate를 실행하며, activation hook 실패 시 이전 current를 복구한다.
- public mutable-write CLI는 `current` symlink뿐 아니라 그 symlink가 가리키는 실제 active release와 하위 경로에 대한 직접 쓰기를 거부한다.
- source manifest-only discovery, build DAG trace, company shard cache, partial shard reuse, `prod publish --delta`를 immutable release 구조 위에 추가했다.

아직 남은 운영 튜닝 항목:

- env-level build history와 장기 dashboard 집계.
- resource profile 자동 튜닝.
- content-addressed release id.
- remote content-addressed blob store와 hardlink 최적화.
- 운영 데이터 기반 ranking threshold 튜닝.
- 운영 환경별 실제 alert threshold 값과 receiver URL/secret 등록.

## 1. 현재 문제

이 절은 1차 구현 전의 문제 상태를 기록한다. 현재 코드에는 이미 release helper와 prod symlink publish가 일부 존재했다. 하지만 publish 경로가 완전히 통일되어 있지 않았고, 몇몇 CLI는 stable root를 직접 변경한 뒤 index를 다시 만들었다.

대표 문제:

```text
stable_root/companies/TICKER 교체
-> index build 시작
-> index build 실패
-> artifact는 새 상태
-> index는 이전 상태 또는 불완전 상태
-> serving 불일치
```

프로덕션 기준에서 이 상태는 허용할 수 없다.

현재 구조의 주요 불일치:

- `build-research-pipeline --publish-root`는 ticker마다 stable root에 publish하고 stable index를 즉시 rebuild한다.
- `update-ticker --publish`는 ticker publish와 stable index rebuild를 같은 mutable lock 안에서 수행한다.
- `publish-ticker --to-root`는 여러 ticker를 복사한 뒤 stable root index를 1회 rebuild하지만, 여전히 mutable root 직접 수정이다.
- `queue-run`은 기본적으로 index rebuild를 skip하고, `--rebuild-agent-index` 또는 `--publish-prod`일 때만 batch-end rebuild를 한다.
- `release publish-dev`는 immutable-ish release를 만들지만, 다른 publish CLI와 같은 primitive를 쓰지 않는다.
- legacy top-level `build-agent-index` command surface is removed; `index build` owns index writes.
- MCP persistent store는 index path signature 기준으로 store를 cache하지만, release promote event에 대한 first-class model이 없다.

목표는 이 모든 경로를 하나의 release transaction model로 합치는 것이다.

## 2. 확정 설계 원칙

### 2.1 Current는 immutable release pointer다

`current`는 실제 data root가 아니라 release directory를 가리키는 pointer다.

```text
<releases_root>/<env>/
  current -> releases/<release_id>
  releases/
    <release_id>/
      manifest.json
      companies/
      indexes/
      verify/
```

`current`가 가리키는 release directory는 수정하지 않는다.

금지:

```text
current/companies/AAPL 직접 교체
current/indexes/agent_index.sqlite 직접 rebuild
current/manifest.json 직접 수정
```

허용:

```text
candidate release 생성
candidate 안에서 artifact/index/manifest 생성
verification 통과
current symlink atomic replace
```

### 2.2 Publish는 release transaction이다

모든 publish 경로는 같은 primitive를 사용한다.

```text
create_release_plan
create_candidate_release
apply_artifact_changes
build_indexes
verify_release
write_manifest
promote_current
notify_serving
cleanup_or_retention
```

CLI가 달라도 내부 publish primitive는 하나여야 한다.

`cleanup_or_retention`은 promote 성공 직후 실행되는 자동 GC로 구현했다:
`release cache gc --keep current`(cache 열거 범위는 아래 Fragment cache 규칙)와
`release gc --keep N`(N은 CLI config `release-keep-releases`, 기본 1)을
`cache_gc.lock` 보호 아래 실행한다. 이 자동 GC는 config `release-auto-gc`
(기본 on, env `KRW_RELEASE_AUTO_GC`로도 끌 수 있음)로 제어하며, 실패해도
promote 결과에는 영향을 주지 않는다(non-fatal).

적용 대상:

- `build-research-pipeline --publish-root`
- `update-ticker --publish`
- `publish-ticker`
- `queue-run` publish batch
- `release publish-dev`
- `prod publish`

### 2.3 실패 시 current untouched

아래 실패는 모두 promote 금지다.

- artifact apply 실패
- index build 실패
- SQLite integrity check 실패
- required table 누락
- manifest 불일치
- smoke query regression 실패
- MCP reload/reopen 실패
- remote activation health check 실패

실패 처리:

```text
current는 그대로 유지
candidate는 failed/quarantine 상태로 이동하거나 보존
serving 영향 없음
```

### 2.4 Cache는 release 바깥에 둔다

release는 immutable 산출물이다. fragment cache는 build acceleration resource다.

따라서 release directory 안에 cache를 넣지 않는다.

기본 위치:

```text
<releases_root>/<env>/.index_fragment_cache/
```

환경 변수 override:

```text
KRW_INDEX_FRAGMENT_CACHE_ROOT=/path/to/shared/cache
```

cache가 없어도 release는 full rebuild로 재생성 가능해야 한다.

### 2.5 Builder output은 deterministic이어야 한다

동일 input과 동일 builder version으로 만든 index는 논리적으로 동일해야 한다.

필수 규칙:

- artifact discovery order 고정
- ticker order 고정
- document type/period order 고정
- fragment merge order 고정
- JSON serialization은 `sort_keys=True`가 필요한 곳에 사용
- generated timestamp는 manifest metadata에만 두고 cache correctness에는 사용하지 않음
- row insert order 고정
- smoke query result 비교 가능

### 2.6 Shard-aware release가 최종 target이다

최종 target:

```text
release/
  indexes/
    agent_index.sqlite
    global_catalog.sqlite
    global_topics.sqlite
    companies/
      AAPL.sqlite
      MSFT.sqlite
      NVDA.sqlite
```

`agent_index.sqlite`는 전체 범위 질의와 cross-check 검증을 위한 monolith output이다. 최종 serving은 shard router가 ticker-scoped query를 company shard로 route한다.

MCP tool contract는 유지한다. monolith/shard 차이는 serving router가 숨긴다.

## 3. Target directory layout

권장 layout:

```text
<releases_root>/
  dev/
    current -> releases/20260611T031500Z_abcd1234
    releases/
      20260611T031500Z_abcd1234/
        manifest.json
        companies/
          AAPL/
          MSFT/
        indexes/
          agent_index.sqlite
          global_catalog.sqlite
          global_topics.sqlite
          companies/
            AAPL.sqlite
            MSFT.sqlite
          build_progress.jsonl
          build_summary.json
        verify/
          release_verify.json
          smoke_queries.json
          ranking_quality.json
          smoke_queries.md
        logs/
          release_transaction.log
    candidates/
      20260611T041700Z_ef567890.building/
      20260611T042000Z_bad99999.failed/
    .index_fragment_cache/
      fragments/
      v3/
        company_shards/
        spine_fragments/
        router_sidecars/
        global_spines/
      manifests/
      locks/
    locks/
      release_transaction.lock
      cache_gc.lock
  prod/
    current -> releases/20260611T031500Z_abcd1234
    releases/
    candidates/
    .index_fragment_cache/
```

Notes:

- `companies/` remains at release root as canonical source artifact payload.
- `indexes/agent_index.sqlite` remains present as the monolith output for full-scope reads and verification.
- shard outputs are declared in `manifest.json`; serving router decides which index file to open.
- candidate roots are never served.
- failed candidates are not promoted and can be inspected.
- `locks/cache_gc.lock` — 구현됨: `FileProcessLock` 기반이며 `release cache gc`,
  `release gc`, promote 후 자동 gc가 이 lock을 공유한다.

## 4. Release transaction lifecycle

### 4.1 Acquire transaction lock

One release transaction per environment.

Lock path:

```text
<releases_root>/<env>/locks/release_transaction.lock
```

Lock scope:

- candidate creation
- artifact materialization
- index build
- verification
- promote
- serving notification

The lock prevents concurrent promote races. It does not need to block independent read-only MCP serving.

### 4.2 Create release plan

Input:

- env
- current release id
- source running root, if publishing from pipeline output
- changed tickers
- explicit source ticker trees, if using `publish-ticker`
- builder versions
- index layout target
- verification policy
- promote policy

Plan output:

```json
{
  "format": "krw-ontology-release-plan/v1",
  "env": "dev",
  "base_release_id": "20260611T031500Z_abcd1234",
  "candidate_release_id": "20260611T041700Z_ef567890",
  "source_roots": [
    "/Users/.../krw-ontology-data-running"
  ],
  "changed_tickers": ["AAPL", "MSFT"],
  "index_layout": {
    "monolith": true,
    "company_shards": true,
    "global_catalog": true,
    "global_topics": true
  },
  "verification_policy": "production",
  "promote": true
}
```

Plan invariants:

- If there is no current release, transaction is an initial import/full release.
- If there is a current release, candidate starts from current release content plus changed ticker overlays.
- No mutation of current release occurs while planning.
- If changed ticker list is empty, transaction may no-op unless `--force` is set.

### 4.3 Materialize candidate release

Candidate root:

```text
<releases_root>/<env>/candidates/<release_id>.building/
```

Materialization rules:

1. Create empty candidate directory.
2. Copy or hardlink base release artifacts into candidate.
3. Overlay changed ticker trees from source root.
4. Remove stale ticker data when a ticker is intentionally deleted.
5. Write candidate build state.
6. Never write `manifest.json` as final until index and verification pass.

Implementation options:

- Local filesystem: copytree or hardlink farm. Copy is simpler. Hardlink can save disk but must avoid mutating linked files.
- Remote prod: upload candidate bundle to remote incoming, extract into `releases/<id>.tmp`, verify, then rename to `releases/<id>`.

Candidate state file:

```text
candidate_state.json
```

Example:

```json
{
  "format": "krw-ontology-candidate-state/v1",
  "status": "building",
  "release_id": "20260611T041700Z_ef567890",
  "env": "dev",
  "started_at": "2026-06-11T04:17:00Z",
  "base_release_id": "20260611T031500Z_abcd1234",
  "changed_tickers": ["AAPL", "MSFT"]
}
```

### 4.4 Build artifact manifest

Release-level artifact manifest records all artifact indexes and their content fingerprints.

Path:

```text
release/indexes/artifact_manifest.json
```

Example:

```json
{
  "format": "krw-ontology-artifact-manifest/v1",
  "root": "/.../releases/20260611T041700Z_ef567890",
  "artifacts": [
    {
      "ticker": "AAPL",
      "document_type": "10-K",
      "doc_type_key": "10K",
      "period": "FY2025",
      "artifact_index_path": "companies/AAPL/ontology/10-K/FY2025/artifact_index.json",
      "content_hash": "sha256:...",
      "referenced_files": [
        {
          "path": "companies/AAPL/ontology/10-K/FY2025/claims.jsonl",
          "hash": "sha256:...",
          "size": 12345
        }
      ]
    }
  ]
}
```

Important: artifact hash must include every file read by the index builder, not just `artifact_index.json`.

The current builder reads:

- files referenced by `artifact_index.json`
- object JSONL files
- edges JSONL
- quality events JSONL
- rejected objects JSONL
- batch failures JSONL
- `section_quality.json`
- company context artifact files

Therefore the fingerprint function must include:

```text
artifact_index.json bytes
all referenced JSONL bytes
section_quality.json bytes if present
builder-visible auxiliary files
normalized relative paths
file sizes
file hashes
```

Timestamp-like values are not cache keys unless they affect indexed rows.

### 4.5 Build index plan

Index plan uses artifact manifest and fragment cache metadata.

Path:

```text
release/indexes/index_plan.json
```

Example:

```json
{
  "format": "krw-ontology-index-plan/v1",
  "root": "/.../releases/20260611T041700Z_ef567890",
  "index_schema_version": "1.0.0-alpha.3",
  "retrieval_text_builder_version": "...",
  "company_topic_builder_version": "...",
  "typed_projection_builder_version": "...",
  "artifacts": 240,
  "dirty_artifacts": 3,
  "cached_artifacts": 237,
  "dirty_tickers": ["AAPL"],
  "compile_workers": 8,
  "merge_order": [
    "companies/AAPL/context/artifact_index.json",
    "companies/AAPL/ontology/10-K/FY2025/artifact_index.json"
  ],
  "outputs": {
    "monolith": "indexes/agent_index.sqlite",
    "global_catalog": "indexes/global_catalog.sqlite",
    "global_topics": "indexes/global_topics.sqlite",
    "company_shards_dir": "indexes/companies"
  }
}
```

Cache key:

```text
sha256(
  artifact_content_hash
  index_schema_version
  ontology_schema_version
  retrieval_text_builder_version
  company_topic_builder_version
  typed_projection_builder_version
  fragment_schema_version
  builder_code_version
)
```

`builder_code_version`은 `AGENT_INDEX_BUILDER_VERSION` 명시 상수로 cache key,
fragment metadata, BuildPlan, final index build metadata에 기록한다. 관련 builder
계약이 바뀌면 반드시 이 값을 bump한다.

### 4.6 Compile fragments

Dirty artifacts compile in parallel. Clean artifacts reuse cached SQLite fragments.

Wrong:

```text
worker 1 -> final SQLite INSERT
worker 2 -> final SQLite INSERT
worker 3 -> final SQLite INSERT
```

Correct:

```text
worker 1 -> fragment A.sqlite
worker 2 -> fragment B.sqlite
worker 3 -> fragment C.sqlite
single writer -> merge fragments into final SQLite
```

Fragment cache 운영 규칙:

- Cache root는 release directory 밖에 둔다.
- Cache hit는 `verify_index_fragment`가 통과할 때만 인정한다.
- Corrupt cache는 build 실패가 아니라 해당 artifact fragment 재compile로 복구한다.
- `krw-ontology index cache status --root <release>`는 현재 build plan이 참조하는 fragment와 cache에 남은 unreferenced fragment를 함께 보여 준다.
- `krw-ontology index cache gc --root <release>`는 dry-run이다.
- 실제 삭제는 `--yes`가 명시된 실행(`index cache gc --yes`, `release cache gc --yes`)과 promote 후 자동 gc에서만 수행한다.
- GC 삭제 범위는 `<cache-root>/fragments/**`, `v3/company_shards/**`, `v3/spine_fragments/**`, `v3/router_sidecars/**`, `v3/global_spines/**`의 `.sqlite`와 그 `-wal`/`-shm`/`-journal`, `.seal.json`, `.verify.json` sidecar로 제한한다. 글로벌 스파인 캐시(빌드당 1개, 콘텐츠 해시 키)는 현재 빌드 플랜이 참조하는 세대만 유지한다.
- GC는 release candidate, current release, final index output을 삭제하지 않는다.

Worker scheduling:

- Sort largest artifacts first by estimated file size or object count.
- Use `ProcessPoolExecutor`, not ThreadPool, for JSON/text-heavy compile.
- Default workers: `min(max(os.cpu_count() - 1, 1), 8)`.
- Allow override:

```text
KRW_INDEX_COMPILE_WORKERS=12
krw-ontology index build --workers 12
```

Fragment DB layout:

```sql
CREATE TABLE fragment_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE fragment_documents (...);
CREATE TABLE fragment_objects (...);
CREATE TABLE fragment_edges (...);
CREATE TABLE fragment_quality_events (...);
CREATE TABLE fragment_object_text (...);
CREATE TABLE fragment_object_search_text (...);
CREATE TABLE fragment_object_fts_rows (...);
CREATE TABLE fragment_stats (...);
```

Fragment metadata includes:

- artifact path
- artifact content hash
- cache key
- builder versions
- ticker
- document type
- period
- object count
- edge count
- quality event count
- created_at

Fragments are build artifacts, not serving artifacts. They can be garbage-collected by content address and last-used time.

### 4.7 Merge indexes

Final SQLite files are built in temp paths first.

Example:

```text
indexes/.build/agent_index.sqlite.tmp
indexes/.build/global_catalog.sqlite.tmp
indexes/.build/global_topics.sqlite.tmp
indexes/.build/companies/AAPL.sqlite.tmp
```

Monolith merge:

```sql
ATTACH '/cache/fragments/<cache_key>.sqlite' AS frag;
INSERT INTO documents SELECT * FROM frag.fragment_documents;
INSERT INTO objects SELECT * FROM frag.fragment_objects;
INSERT INTO edges SELECT * FROM frag.fragment_edges;
INSERT INTO quality_events SELECT * FROM frag.fragment_quality_events;
INSERT INTO object_text SELECT * FROM frag.fragment_object_text;
INSERT INTO object_search_text SELECT * FROM frag.fragment_object_search_text;
INSERT INTO object_fts(...) SELECT ... FROM frag.fragment_object_fts_rows;
DETACH frag;
```

Then build derived serving tables:

- object traceability
- metric lookup
- metric dimension lookup
- company dimension catalog
- typed projection lookups
- company topic index
- company topic FTS
- serving secondary indexes
- metadata

For the first production v2 builder, final DB can still rebuild all derived tables from merged base rows. This is production safe because output is built in a temp file and promoted only after verification.

Later optimization:

- company-level projection fragments
- dirty ticker projection rebuild
- shard-only build
- no-op release skip

### 4.8 Atomic index publish inside candidate

When each temp SQLite output passes local index verification:

```text
os.replace(indexes/.build/agent_index.sqlite.tmp, indexes/agent_index.sqlite)
os.replace(indexes/.build/global_catalog.sqlite.tmp, indexes/global_catalog.sqlite)
os.replace(indexes/.build/global_topics.sqlite.tmp, indexes/global_topics.sqlite)
```

For company shards:

```text
build indexes/.build/companies/AAPL.sqlite.tmp
verify
os.replace(..., indexes/companies/AAPL.sqlite)
```

No serving process points at candidate paths, so these swaps are only candidate-internal. The real serving cutover is the `current` pointer promote.

### 4.9 Verify release

Verification writes:

```text
verify/release_verify.json
verify/smoke_queries.json
verify/ranking_quality.json
verify/smoke_queries.md
```

Verification must run before final manifest status is marked `ready`.

Gate categories:

1. Filesystem
2. Manifest
3. SQLite
4. Index contents
5. FTS
6. Shard consistency
7. Query smoke
8. MCP smoke
9. Remote activation health, for prod

Detailed gates are in section 10.

### 4.10 Write final manifest

Only after verification passes, write `manifest.json`.

Manifest writer와 reader/verifier는 `krw-ontology-release/v2`만 지원한다.

```text
krw-ontology-release/v2
```

Example:

```json
{
  "format": "krw-ontology-release/v2",
  "release_id": "20260611T041700Z_ef567890",
  "env": "prod",
  "status": "ready",
  "created_at": "2026-06-11T04:17:00Z",
  "source": {
    "base_release_id": "20260611T031500Z_abcd1234",
    "changed_tickers": ["AAPL", "MSFT"],
    "source_roots": ["/.../running"]
  },
  "artifact_manifest": "indexes/artifact_manifest.json",
  "index_layout_version": "krw-ontology-index-layout/v2",
  "indexes": {
    "monolith": {
      "path": "indexes/agent_index.sqlite",
      "schema_version": "1.0.0-alpha.3",
      "sha256": "..."
    },
    "global_catalog": {
      "path": "indexes/global_catalog.sqlite",
      "sha256": "..."
    },
    "global_topics": {
      "path": "indexes/global_topics.sqlite",
      "sha256": "..."
    },
    "company_shards": {
      "dir": "indexes/companies",
      "tickers": {
        "AAPL": {
          "path": "indexes/companies/AAPL.sqlite",
          "sha256": "..."
        }
      }
    }
  },
  "build": {
    "builder_version": "agent-index-builder/v2",
    "fragment_schema_version": "agent-index-fragment/v1",
    "compile_workers": 8,
    "cache_hits": 237,
    "cache_misses": 3,
    "total_elapsed_sec": 284.2
  },
  "counts": {
    "documents": 240,
    "objects": 123456,
    "edges": 54321,
    "quality_events": 100
  },
  "verification": {
    "status": "passed",
    "path": "verify/release_verify.json",
    "smoke_queries": "verify/smoke_queries.json",
    "ranking_quality": "verify/ranking_quality.json"
  }
}
```

Canonical-only policy:

- Release root에는 `manifest.json`만 쓴다.
- `release_manifest.json` fallback과 v1 manifest accept는 production 경로에서 제거한다.
- Manifest는 기본 `index_path`로 `indexes/agent_index.sqlite`를 계속 노출한다.

### 4.11 Promote current

Local promote:

```text
ln -s releases/<release_id> current.next
os.replace(current.next, current)
```

Remote promote:

```text
extract bundle to releases/<release_id>.tmp
verify tmp
mv releases/<release_id>.tmp releases/<release_id>
ln -sfn releases/<release_id> current.next
mv -Tf current.next current
run reload hook
run health check
```

Promote is allowed only if:

- candidate status is `ready`
- manifest status is `ready`
- verification status is `passed`
- index files exist and match manifest digests

### 4.12 Notify serving

After local promote:

- write release_changed event file
- optionally call MCP reload endpoint or command
- release-aware MCP pool detects current target changed
- new requests open the new release
- old in-flight requests keep old release until done

After remote promote:

- run configured reload command
- call health URL
- fail the transaction if health check fails
- if reload/health fails after current switch, trigger rollback policy

Rollback policy for post-switch failure:

```text
if reload or health fails after current switch:
  switch current back to previous release
  run reload hook again
  run health check
  mark attempted release as failed_activation
```

## 5. Index builder v2

### 5.1 Builder API

Keep `build_agent_index()` as the public API, but make it call the production builder internally.

Proposed Python API:

```python
def plan_agent_index(
    root: Path,
    *,
    index_path: Path | None = None,
    cache_root: Path | None = None,
    layout: IndexLayout = IndexLayout.MONOLITH_AND_SHARDS,
    workers: int | None = None,
    source_manifest_path: Path | None = None,
    force: bool = False,
) -> IndexBuildPlan:
    ...

def build_agent_index_from_plan(plan: IndexBuildPlan) -> IndexBuildResult:
    ...

def build_agent_index(
    root: Path,
    *,
    index_path: Path | None = None,
    force: bool = True,
    cache_root: Path | None = None,
    workers: int | None = None,
    atomic: bool = True,
    layout: str = "monolith-and-shards",
    source_manifest_path: Path | None = None,
) -> dict[str, Any]:
    ...
```

Public API contract:

- Callers get `{"index_path", "root", "artifact_indexes", "totals", "build_settings"}`.
- `force=True` means rebuild outputs from plan, not delete serving index before build.
- `force=False` can skip if content manifest and builder versions match.
- Direct target deletion before successful replacement is forbidden.

### 5.2 BuildPlan data model

Minimum fields:

```python
@dataclass(frozen=True)
class ArtifactPlanItem:
    artifact_index_path: Path
    relative_path: str
    ticker: str
    document_type: str
    doc_type_key: str
    period: str
    content_hash: str
    cache_key: str
    fragment_path: Path
    cache_hit: bool
    estimated_bytes: int
    estimated_rows: int | None

@dataclass(frozen=True)
class IndexBuildPlan:
    root: Path
    index_path: Path
    cache_root: Path
    artifact_manifest_path: Path
    items: tuple[ArtifactPlanItem, ...]
    dirty_items: tuple[ArtifactPlanItem, ...]
    cached_items: tuple[ArtifactPlanItem, ...]
    dirty_tickers: tuple[str, ...]
    workers: int
    layout: IndexLayout
    build_settings: Mapping[str, Any]
```

The plan is serializable to JSON for debugging and repeatability.

### 5.3 Fragment compile function

Fragment compile은 artifact별 SQLite fragment를 만들고 final DB merge는 별도
single-writer 단계가 수행한다. `_index_artifact()`는 fragment compiler 내부의
row materialization primitive로만 사용한다.

Target split:

```text
read artifact files
build document rows
build object rows
build FTS rows
build edge rows
build quality rows
write fragment SQLite
```

New functions:

```python
def compile_artifact_fragment(item: ArtifactPlanItem, root: Path, cache_root: Path) -> FragmentCompileResult:
    ...

def write_fragment_sqlite(fragment_path: Path, rows: CompiledArtifactRows) -> None:
    ...

def merge_fragments(conn: sqlite3.Connection, fragments: Sequence[Path]) -> MergeStats:
    ...
```

### 5.4 Cache correctness

Fragment cache hit is valid only if all of these match:

- artifact content hash
- ontology schema version
- agent index schema version
- retrieval text builder version
- company topic builder version, if topic seed rows are in fragment
- typed projection builder version, if projection seed rows are in fragment
- fragment schema version
- builder code version

Cache miss triggers fragment compile.

Cache corruption handling:

- verify fragment metadata before use
- run `PRAGMA integrity_check` on fragment
- verify required fragment tables
- if corrupted, delete fragment and rebuild
- never fail release solely because a cache file is corrupt if source artifacts can rebuild it

### 5.5 Derived tables

Initial v2 production behavior:

- Merge all base rows from fragments into temp monolith.
- Rebuild derived tables globally.
- Verify output.
- Promote atomically.

This is production-safe and deterministic.

Later production optimization:

- Generate company projection fragments.
- For shard indexes, rebuild derived tables per company.
- For monolith, either merge company projection fragments or rebuild globally.

Do not implement in-place mutation of serving index as the first path. In-place incremental update is harder to verify and can leave stale rows.

### 5.6 SQLite build profile

Because DBs are built in candidate temp paths, fast settings are acceptable if verification gates are strong.

Suggested profiles:

`prod-safe`:

```text
journal_mode=WAL
synchronous=NORMAL
temp_store=MEMORY
wal_autocheckpoint=1000
```

`prod-fast-build`:

```text
journal_mode=WAL
synchronous=OFF
temp_store=MEMORY
wal_autocheckpoint=0
checkpoint_every_artifacts=25
final PRAGMA integrity_check
final WAL checkpoint truncate
```

`disposable-temp-build`:

```text
journal_mode=OFF or WAL
synchronous=OFF
locking_mode=EXCLUSIVE
temp_store=MEMORY
only for candidate temp DB
never for current serving DB
```

Production default should be explicit:

```text
KRW_BUILD_RESOURCE_PROFILE=prod-fast-build
```

### 5.7 No-op detection

If candidate artifact manifest equals current artifact manifest and builder/index versions match:

```text
skip index build
skip release promote unless --force-release
return no-op result
```

No-op is a production feature, not a shortcut. It prevents duplicate releases with identical content.

## 6. Shard architecture

### 6.1 Index layout

Final release layout:

```text
indexes/
  agent_index.sqlite
  global_catalog.sqlite
  global_topics.sqlite
  companies/
    AAPL.sqlite
    MSFT.sqlite
```

`agent_index.sqlite`:

- monolith output
- full existing schema
- used for full-scope reads and cross-check verification

`companies/<ticker>.sqlite`:

- documents for ticker
- objects for ticker
- edges for ticker
- quality events for ticker
- object FTS for ticker
- metric lookup for ticker
- typed projections for ticker
- company topic index for ticker
- local metadata

`global_catalog.sqlite`:

- ticker list
- document list
- company metadata
- index artifact map
- shard map
- release metadata

`global_topics.sqlite`:

- cross-company topic discovery
- global topic FTS
- optional aggregated company topic rows

### 6.2 Serving router

MCP/store contract remains stable. Internally:

```text
query has one ticker:
  open company shard

query has multiple tickers:
  open each company shard
  fan out
  merge/rank

query has no ticker:
  use global_topics/global_catalog
  fallback to monolith if needed

trace object id:
  resolve object id -> ticker via global catalog or object id prefix
  open relevant shard

compare:
  open shards for selected tickers
  run per-ticker query_context
  merge compact compare output
```

Fallback:

- If shard missing or verification disabled shard use, route to monolith.
- Fallback should be logged and exposed in diagnostics.

### 6.3 Shard verification

Required:

- each ticker in global catalog has shard file
- each shard manifest hash matches release manifest
- document/object counts per shard sum to monolith counts
- selected smoke queries produce compatible top results against monolith
- compare over two tickers works through shard router

## 7. MCP release-aware serving

### 7.1 Current issue

Changing `current` symlink does not automatically force existing SQLite connections to reopen. Persistent stores can keep reading old files.

### 7.2 Target model

MCP store pool is keyed by release identity, not only user-provided index path.

Release signature:

```text
root path
current symlink target
manifest release_id
manifest mtime
index layout version
index file digest or mtime
```

Request lifecycle:

```text
request starts
resolve current release
pin release_id
acquire store for release_id
execute
release lease
```

Promote behavior:

```text
current changes
new request resolves new release_id
old in-flight request continues old release_id
old pool closes after refcount zero or TTL
```

### 7.3 Reload mechanisms

Support both:

1. Passive detection on each request.
2. Explicit reload notification after promote.

Explicit local event:

```text
<releases_root>/<env>/release_events.jsonl
<releases_root>/<env>/events/<release_id>.jsonl
```

Example:

```jsonl
{"action":"promote","changed_at":"2026-06-11T04:25:00+00:00","env":"prod","event":"release_changed","format":"krw-ontology-release-event/v1","previous_release_id":"20260611T031500Z_abcd1234","release_id":"20260611T041700Z_ef567890","verification_ok":true}
```

Optional hooks:

```text
KRW_MCP_RELOAD_COMMAND
KRW_MCP_RELOAD_URL
```

### 7.4 MCP health

Health endpoint should report:

- env
- release id
- manifest path
- index layout version
- active store mode: monolith or shard
- index path or shard root
- document/object counts
- current symlink target
- persistent pool current release id
- persistent pool hot-swap counters: rotations, active stores, retired stores, leased, retired leased
- rotation pending flag and oldest retired lease age
- last rotation paths: previous resolved index path and new resolved index path
- retired indexes while old in-flight requests are still pinned

`/metrics` exposes the same release and hot-swap state as Prometheus-style text metrics:

- `krw_ontology_mcp_health_ok`
- `krw_ontology_mcp_release_documents`
- `krw_ontology_mcp_release_objects`
- `krw_ontology_mcp_company_shards`
- `krw_ontology_mcp_global_topics`
- `krw_ontology_mcp_store_rotation_pending`
- `krw_ontology_mcp_store_retired_leased`
- `krw_ontology_mcp_store_retired_oldest_age_seconds`
- `krw_ontology_mcp_store_rotations_total`

External observability assets:

```text
ops/observability/prometheus-krw-ontology-mcp-alerts.yml
ops/observability/alertmanager-krw-ontology-mcp.yml
ops/observability/grafana-krw-ontology-mcp-dashboard.json
ops/observability/README.md
```

Default alerts cover:

- MCP health failure
- missing configured index
- hot-swap stuck for more than 5 minutes
- retired release leases still pinned after 15 minutes
- excessive store rotations
- empty production release content

Prometheus alert threshold injection:

```bash
export KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS=600
export KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR=10m
export KRW_PROMETHEUS_ROTATION_WINDOW=30m
export KRW_PROMETHEUS_ROTATION_COUNT=5

krw-ontology observability render-prometheus-alerts \
  --template ops/observability/prometheus-krw-ontology-mcp-alerts.yml \
  --output /etc/prometheus/rules/krw-ontology-mcp-alerts.yml
```

The renderer validates positive integer thresholds and Prometheus duration syntax, updates known alert expressions/`for` durations, writes atomically, and prints threshold values plus their source names.

Observability deployment check:

```bash
krw-ontology observability doctor \
  --prometheus-alerts /etc/prometheus/rules/krw-ontology-mcp-alerts.yml \
  --alertmanager /etc/alertmanager/krw-ontology-mcp.yml \
  --env prod \
  --write-report /var/log/krw-ontology/observability-doctor.json
```

For `prod`, the doctor rejects localhost/loopback receivers, invalid Prometheus durations, missing required alert rules/metrics, disabled `send_resolved`, and missing critical/warning routing.
The report records config hashes and validation results without receiver URLs, so it can be stored alongside deployment evidence.

Default Alertmanager routing covers:

- critical vs warning severity split
- grouping by alert name, env, release id, and service
- resolved notifications
- warning inhibition while a critical alert for the same release is firing

Alertmanager receiver injection:

```bash
export KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL="https://alerts.example/default"
export KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL="https://alerts.example/critical"
export KRW_ALERTMANAGER_WARNING_WEBHOOK_URL="https://alerts.example/warning"

krw-ontology observability render-alertmanager \
  --template ops/observability/alertmanager-krw-ontology-mcp.yml \
  --output /etc/alertmanager/krw-ontology-mcp.yml
```

The renderer validates that all receiver URLs are present and HTTP(S), writes the output atomically, and prints only the output path plus receiver source names. It does not print webhook URLs or embedded tokens.

Status as of 2026-06-11: MCP startup and health both require configured `prod`
serving to use the `current` symlink. Runtime env/index paths preserve the
symlink path, so persistent stores can detect atomic `current` switches and
rotate without serving a mutable release directory directly.

## 8. CLI design

### 8.1 New top-level command groups

`index` group:

```text
krw-ontology index plan
krw-ontology index build
krw-ontology index verify
```

`release` group:

```text
krw-ontology release plan
krw-ontology release build
krw-ontology release verify --write-report --smoke
krw-ontology release verify --ranking-min-overlap-ratio 0.5 --ranking-sample-limit 5
krw-ontology release calibrate-ranking-thresholds verify/ranking_quality.json
krw-ontology release verify --update-smoke-baseline
krw-ontology release verify --smoke-baseline verify/smoke_queries.baseline.json
krw-ontology release promote
krw-ontology release publish
krw-ontology release rollback
krw-ontology release status
krw-ontology release list
krw-ontology release import-current
krw-ontology release gc
```

`observability` group:

```text
krw-ontology observability render-prometheus-alerts
krw-ontology observability render-alertmanager
krw-ontology observability doctor
```

### 8.2 `index plan`

Purpose: produce index build plan without building.

Example:

```bash
krw-ontology index plan \
  --root /data/releases/dev/candidates/20260611T041700Z.building \
  --cache-root /data/releases/dev/.index_fragment_cache \
  --layout monolith-and-shards
```

Output:

```text
Artifacts: 240
Dirty artifacts: 3
Cached fragments: 237
Dirty tickers: AAPL, MSFT
Workers: 8
Outputs:
  monolith: indexes/agent_index.sqlite
  shards: indexes/companies
  global catalog: indexes/global_catalog.sqlite
```

### 8.3 `index build`

Purpose: build index outputs inside a candidate/release root.

Default:

- atomic temp output
- cache enabled
- fragment verification enabled
- production verification enabled
- no target deletion before successful build

Example:

```bash
krw-ontology index build \
  --root /data/releases/dev/candidates/20260611T041700Z.building \
  --cache-root /data/releases/dev/.index_fragment_cache \
  --layout monolith-and-shards \
  --workers 8
```

Options:

```text
--workers N
--cache-root PATH
--force/--no-force
--layout monolith
--layout monolith-and-shards
--layout shards
```

Release transaction은 index build와 verification을 생략하는 옵션을 제공하지 않는다.

### 8.4 `release publish`

Purpose: one command for production-safe publish.

Examples:

```bash
krw-ontology release publish \
  --env dev \
  --from-root /data/running \
  --releases-root /data/releases \
  --promote
```

```bash
krw-ontology publish-ticker AAPL MSFT \
  --from-root /data/running \
  --to-root /data/releases \
  --env dev
```

Behavior:

```text
plan
candidate create
apply changed tickers
index build
verify
manifest write
promote
notify/reload
```

Default is production-safe:

- promote only after verification
- current untouched on failure
- failed candidate preserved

### 8.5 `release build`

Purpose: build candidate but do not promote.

Example:

```bash
krw-ontology release build \
  --env dev \
  --from-root /data/running \
  --tickers AAPL
```

This still performs full production verification. It only stops before current pointer switch.

### 8.6 `release promote`

Purpose: promote a ready candidate/release.

Example:

```bash
krw-ontology release promote 20260611T041700Z_ef567890 \
  --releases-root /data/releases \
  --env dev
```

Rules:

- candidate/release must have ready manifest
- verification must have passed
- promote writes release_changed event
- MCP reload hook runs if configured

Status as of 2026-06-11: local promote and rollback append
`release_changed` JSONL events to both `<env>/release_events.jsonl` and the
release-external `<env>/events/<release_id>.jsonl`, including action,
previous release id, verification status, manifest path, and index path.
Event log files are prepared before the `current` symlink switch, so an
unwritable event destination blocks promotion before serving state changes.
If an append fails after the atomic switch despite the preflight, the command
reports the event-log warning without misreporting the successful current
transition as a failed promotion.
The shared local promote helper writes `verify/release_verify.json`,
`verify/smoke_queries.json`, and `verify/ranking_quality.json` only while
finalizing a never-activated candidate before its first `current` switch.
For a previously activated release, promote/rollback validates the existing
reports without rewriting release files. Missing or invalid existing reports
block rollback before serving state changes.
`release promote` and `release rollback` also accept local `--reload-command`
and `--health-url` hooks. After the atomic switch, reload failure, HTTP
failure, `ok != true`, or release id mismatch causes the CLI to switch
`current` back to the previous release when one existed, rerun the same local
hooks for the restored release, and return a failed command rather than
reporting a partial activation as successful.

### 8.7 `release rollback`

Purpose: switch current back to previous or explicit release.

Example:

```bash
krw-ontology release rollback --env prod
krw-ontology release rollback 20260610T230000Z_old12345 --env prod
```

Rollback is a transaction:

```text
verify target release
switch current
notify/reload
health check
if health fails, report critical and preserve rollback logs
```

Status as of 2026-06-11: remote prod rollback verifies the target manifest,
prod env/release id, serving SQLite integrity/quick checks, release verify
report, smoke report, ranking quality report, and ranking quality hash before
switching `current`. After the switch, reload and health failures restore the
previous `current` symlink when one existed, and health must report JSON
`ok: true` plus the rollback target `release_id`.

### 8.8 Canonical command surface

Index writes go through `index build`; read-only planning goes through `index plan`.
Legacy top-level index build wrappers are not kept after the canonical transition.

`release publish-dev`:

```text
wrapper for release publish --env dev --from-root <running-root> --promote
```

`publish-ticker`:

```text
wrapper for release publish --tickers ... --from-root ... --env <env> --promote
```

`update-ticker --publish`:

```text
run pipeline into running root
build company context
call release publish for changed ticker
```

`build-research-pipeline --publish-root`:

```text
run all requested ticker pipelines into running root
build company contexts
call one release publish for changed tickers
```

`queue-run`:

```text
process jobs into running root
collect changed tickers
when batch boundary reached, call one release publish transaction
```

### 8.9 Unsafe mutable commands

Mutable direct copy can exist only as hidden/internal command.

Name should make risk explicit:

```text
krw-ontology internal unsafe-copy-ticker-tree
```

No public/default command should mutate current or stable root directly.

## 9. Remote prod transaction

Current prod publish already bundles a stable root and activates remote symlink. The target design makes it release-native.

Remote publish flow:

```text
local candidate release is ready
bundle release root
scp to remote incoming
ssh remote:
  mkdir releases/<id>.tmp
  extract bundle
  verify manifest
  verify index files
  run SQLite integrity checks
  rename tmp -> releases/<id>
  switch current.next -> current
  run reload command
  run health check
  cleanup old releases
```

Status as of 2026-06-11: remote activation verifies manifest/release id, required
verify reports, ranking quality hashes, and every serving SQLite file before
switching `current`. The remote host runs `PRAGMA integrity_check` and
`PRAGMA quick_check` for the monolith, optional global catalog/topics, and
company shards, and checks the monolith required table set before activation.
Remote activation and rollback also parse the release manifest with Python and
verify the relative `index_path`/`artifact_manifest_path` plus
`index_sha256`/`artifact_manifest_sha256` before switching `current`.
`prod doctor` validates the same remote prerequisite by requiring `python3` or
`python` with the standard `sqlite3` module before publish or rollback.

If remote verification fails before current switch:

```text
current unchanged
tmp release removed or quarantined
publish fails
```

If reload/health fails after current switch:

```text
rollback current to previous release
run reload command
run health check
mark new release failed_activation
write activation_events/<release_id>.jsonl rollback event
publish fails loudly
```

Remote root:

```text
<remote_root>/
  current -> releases/<release_id>
  releases/
  incoming/
  failed/
  activation_logs/
  activation_events/
```

## 10. Verification gates

### 10.1 Filesystem gates

Required:

- release root exists
- `companies/` exists
- `indexes/` exists
- no candidate `.building` status for promoted release
- no index temp files left in final release
- no broken symlink in release
- manifest exists

### 10.2 Manifest gates

Required:

- `format` supported
- `release_id` matches directory
- `env` matches command
- `status == ready`
- index paths are relative and resolve under release root
- index digests match files
- artifact manifest exists
- verification path exists
- build versions present

Status as of 2026-06-11: `verify_release_root` enforces the core filesystem
and manifest gates before smoke checks: release root/indexes/companies
presence, no broken symlinks, no stale `.tmp`/`.building`/`.build` artifacts,
`krw-ontology-release/v2` manifest format, `status == ready`, env match, release id directory
match for `<env>/<release_id>` roots, and relative `index_path` confinement
under the release root. Release manifests now record `index_sha256`; verify
rejects missing or mismatched index digests and missing
`agent_index_schema_version`. `build_agent_index` writes
`indexes/artifact_manifest.json`; release manifests record its path and
SHA-256, and verify rejects missing or changed artifact manifests before
promotion.

### 10.3 SQLite gates

For every serving SQLite file:

```sql
PRAGMA integrity_check;
PRAGMA quick_check;
```

Required tables for monolith:

- metadata
- documents
- objects
- edges
- quality_events
- object_fts
- object_text
- object_search_text
- object_traceability
- metric_lookup
- metric_dimension_lookup
- company_dimension_catalog
- exposure_lookup
- agreement_lookup
- event_lookup
- factor_lookup
- company_topic_index
- company_topic_fts
- company_topic_source_objects

Required sanity:

- document count > 0
- object count > 0
- object FTS count > 0 unless release has no searchable objects
- company topic count > 0 for non-empty release
- no duplicate primary keys
- metadata build payload exists
- metadata schema version matches manifest

### 10.4 Shard gates

Required:

- global catalog lists all tickers
- each listed ticker has shard file
- shard metadata ticker matches file name
- shard document/object counts match global catalog
- sum of shard object counts equals monolith object count, if monolith exists
- shard smoke query works for sample tickers

### 10.5 Search smoke gates

Smoke queries should be deterministic enough to catch empty/broken index.

Minimum generated smoke set:

- one query per changed ticker
- one query for each of: metric, risk/factor, company topic, trace
- one compare query for two tickers if release has at least two tickers

Static examples:

```text
AAPL revenue margin China demand
NVDA data center supply constraint capex
MU HBM DRAM NAND gross margin
GOOGL AI capex cloud margin
compare AAPL MSFT revenue margin
```

Gate result:

- query does not error
- result count > 0 for known ticker query
- top result has object id or topic id
- citation/trace availability is not worse than configured threshold
- shard and monolith top-k overlap above threshold when both are present

Implemented release smoke gate:

- if `shard_manifest.json` and `global_topics.sqlite` exist, sample real `company_topic_index` topics up to the configured sample limit
- run each sampled question through `discover_company_topics` on the shard router and the monolith store
- fail verification if the router does not use the `global_topics` route
- fail verification if global topics discovery falls back to object search
- fail verification if monolith has top topic ids but global topics returns none
- fail verification if top-k overlap is below the configured release-smoke threshold
- fail verification if overlapping topics move beyond the allowed rank delta
- record route mode, FTS strategy, fallback flags, top topic ids, overlap ratio, rank delta, thresholds, sample count, and per-sample details in deterministic smoke baseline input

Runtime threshold overrides:

```text
KRW_RELEASE_SMOKE_GLOBAL_TOPIC_TOP_K=5
KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MIN_OVERLAP_RATIO=0.5
KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MIN_OVERLAP_COUNT=1
KRW_RELEASE_SMOKE_GLOBAL_TOPIC_MAX_RANK_DELTA=3
KRW_RELEASE_SMOKE_GLOBAL_TOPIC_SAMPLE_LIMIT=5
```

Equivalent `release verify` options:

```text
--ranking-top-k 5
--ranking-min-overlap-ratio 0.5
--ranking-min-overlap-count 1
--ranking-max-rank-delta 3
--ranking-sample-limit 5
```

Explicit CLI options win over environment variables and are printed in the `release verify` output together with source metadata.

These values and their sources are recorded in `verify/smoke_queries.json`, so threshold/source changes intentionally alter the smoke baseline hash.

Ranking quality report:

- `release verify --write-report` writes `verify/ranking_quality.json` whenever smoke verification is available
- the report is deterministic except for `generated_at`
- `ranking_quality_hash` is computed from the normalized ranking checks, summary, errors, and warnings
- summary fields include status counts, sample counts, failed sample counts, observed route modes, fallback counts, minimum observed overlap ratio, maximum observed rank delta, applied thresholds, and threshold sources
- release verification report stores `ranking_quality_path` and `ranking_quality_hash` beside the smoke query report fields
- skipped reports are still emitted when shard/global-topic layout is absent, so operators can distinguish "not applicable" from "report missing"

Ranking threshold calibration:

```bash
krw-ontology release calibrate-ranking-thresholds \
  /data/releases/prod/20260601/verify/ranking_quality.json \
  /data/releases/prod/20260608/verify/ranking_quality.json \
  --overlap-margin 0.05 \
  --rank-delta-margin 1 \
  --output /data/releases/prod/ranking_threshold_calibration.json
```

The calibration report has format `krw-ontology-ranking-threshold-calibration/v1`.
It records input report paths and SHA-256 hashes, observation counts, observed
minimum overlap ratio/count, observed maximum rank delta, warnings for mixed
`top_k`, recommended `release verify --ranking-*` values, and a deterministic
`calibration_hash`. It does not mutate release thresholds automatically.

### 10.6 MCP smoke gates

Release verification runs MCP tool-wrapper smoke locally before promote. This is separate from the SDK/store smoke checks.

Always run:

- `krw_ontology_index_context`
- `krw_ontology_catalog`

When a sample ticker exists, also run:

- `krw_ontology_query`
- `krw_ontology_query_context`
- `krw_ontology_topic_map`
- `krw_ontology_quality`
- `krw_ontology_retrieve`
- `krw_ontology_trace` when a sample object id exists
- `krw_ontology_compare` when at least two tickers exist

Gate result:

- tool function does not error
- JSON response parses to an object
- required top-level keys for that tool are present
- response shape summary is included in `verify/smoke_queries.json`
- elapsed time is excluded from deterministic smoke hash

For remote prod:

- configured health URL returns success after reload
- health release id matches promoted release id

## 11. Observability

### 11.1 Build progress

Keep JSONL progress but add summary.

Files:

```text
indexes/build_progress.jsonl
indexes/build_summary.json
logs/release_transaction.log
verify/release_verify.json
verify/smoke_queries.json
verify/ranking_quality.json
verify/smoke_queries.baseline.json
```

`verify/release_verify.json` is an audit artifact, not a serving input. It records:

- full verification payload
- serving SDK smoke checks when enabled
- `verify/smoke_queries.json` path and deterministic `smoke_hash`
- `verify/ranking_quality.json` path and deterministic `ranking_quality_hash`
- optional smoke baseline comparison/update result
- file-level trace for release files outside `verify/`
- SHA-256 for each traced file
- deterministic `reproducibility_hash` over release id, env, paths, roles, sizes, and file hashes
- non-deterministic mtime/generated-at fields for operations debugging only

Build summary fields:

- total elapsed seconds
- artifact count
- dirty artifact count
- fragment cache hits
- fragment cache misses
- compile workers
- compile elapsed
- merge elapsed
- derived table elapsed
- verification elapsed
- DB sizes
- WAL sizes
- slowest artifacts
- slowest phases

### 11.2 Release history

Each env should have release metadata.

Possible path:

```text
<releases_root>/<env>/release_history.jsonl
```

Events:

- plan_created
- candidate_created
- artifact_overlay_done
- index_build_started
- index_build_done
- verification_passed
- verification_failed
- promoted
- rolled_back
- remote_uploaded
- remote_activated
- mcp_reloaded
- activation_rollback

### 11.3 Diagnostics

Commands:

```text
krw-ontology release status --env prod
krw-ontology release inspect <release_id>
krw-ontology index explain-last-build --release <release_id>
krw-ontology index cache status
krw-ontology index cache gc
```

Diagnostics should expose:

- current release id
- previous release id
- candidate builds
- failed candidates
- cache hit rate
- active MCP release id
- shard fallback count

## 12. Config and environment variables

Recommended new config keys:

```text
releases-root
release-env
index-fragment-cache-root
index-layout
index-compile-workers
release-retention
mcp-reload-command
mcp-reload-url
```

Recommended env vars:

```text
KRW_ONTOLOGY_ENV
KRW_ONTOLOGY_RELEASE_ROOT
KRW_ONTOLOGY_MANIFEST_PATH
KRW_INDEX_FRAGMENT_CACHE_ROOT
KRW_INDEX_COMPILE_WORKERS
KRW_INDEX_LAYOUT
KRW_RELEASE_VERIFICATION_POLICY
KRW_MCP_RELOAD_COMMAND
KRW_MCP_RELOAD_URL
```

Existing prod config should map into release transaction config:

- `prod-host`
- `prod-root`
- `prod-reload-command`
- `prod-health-url`
- `prod-keep-releases`

## 13. Migration plan

### 13.1 Import existing mutable stable root

Command:

```bash
krw-ontology release import-current \
  --env dev \
  --from-root /path/to/old-stable-root \
  --releases-root /path/to/releases
```

Behavior:

```text
create release id
copy old stable root into releases/<id>
build or verify index
write v2 manifest
promote current
```

After import:

- config `publish-root` should point to release env root or be deprecated.
- queue jobs should target release publish semantics, not mutable root.

### 13.2 Retarget queue jobs

Current queued jobs contain release publish intent through `publish_root`.

Migration:

```text
publish_root becomes release env/root intent
jobs publish through release transaction
```

Retarget command can remain:

```bash
krw-ontology queue retarget-publish --env dev --releases-root /path/to/releases
```

### 13.3 Canonical transition period

Transition support is about command names and existing MCP contracts, not unsafe
release formats or mutable serving state.

Allowed:

- old command names call new release transaction
- `indexes/agent_index.sqlite` remains present

Not allowed by default:

- direct mutation of current
- direct deletion of serving index
- artifact-only publish
- `release_manifest.json` fallback
- `krw-ontology-release/v1` manifest accept
- artifact identity inferred from path instead of canonical metadata
- index-later publish

## 14. Implementation map

### 14.1 New modules

Suggested files:

```text
src/krw_ontology/release_transaction.py
src/krw_ontology/release_manifest.py
src/krw_ontology/index_plan.py
src/krw_ontology/agent_index/fragments.py
src/krw_ontology/agent_index/merge.py
src/krw_ontology/agent_index/verify.py
src/krw_ontology/agent_index/shards.py
src/krw_ontology/agent_index/router.py
src/krw_ontology/mcp_server/release_pool.py
```

Existing modules to refactor:

```text
src/krw_ontology/agent_index/builder.py
src/krw_ontology/release.py
src/krw_ontology/cli/main.py
src/krw_ontology/mcp_server/tools.py
src/krw_ontology/mcp_server/http_server.py
src/krw_ontology/mcp_server/server.py
src/krw_ontology/config/paths.py
```

### 14.2 Release transaction service

Core class:

```python
class ReleaseTransaction:
    def plan(self) -> ReleasePlan: ...
    def create_candidate(self) -> CandidateRelease: ...
    def apply_changes(self) -> None: ...
    def build_indexes(self) -> IndexBuildResult: ...
    def verify(self) -> VerificationResult: ...
    def write_manifest(self) -> dict: ...
    def promote(self) -> PromoteResult: ...
    def notify(self) -> None: ...
    def run(self) -> ReleaseTransactionResult: ...
```

Important:

- Each method writes structured event logs.
- `run()` catches failures, marks candidate failed, and preserves current.
- `promote()` is the only method allowed to switch current.

### 14.3 CLI integration points

Refactor these functions first:

- `_release_publish_dev`
- `release_publish_dev_cmd`
- `build_research_pipeline_cmd`
- `update_ticker_cmd`
- `publish_ticker_cmd`
- `_queue_rebuild_pending_indexes`
- `_queue_publish_and_defer_index`
- `_publish_prod_root`
The goal is to remove duplicate publish/index logic from CLI and move it into release/index service modules.

### 14.4 Builder split

Implemented builder decomposition:

```text
_read_artifact_inputs
_compile_document_rows
_compile_object_rows
_compile_edge_rows
_compile_quality_rows
_write_fragment
_merge_fragment
```

Keep old tests passing by preserving behavior.

## 15. Implementation order

This is an order of production-valid increments, not a temporary architecture.

### Step 1: Atomic index build

Deliverables:

- `build_agent_index()` builds into temp DB.
- target index is replaced only after build and verification.
- direct target deletion removed.
- progress summary added.
- existing CLI behavior still works, but safer.

Acceptance:

- existing tests pass
- forced build failure leaves old index intact
- `PRAGMA integrity_check` runs

### Step 2: Release transaction primitive

Status as of 2026-06-11: candidate lifecycle, manifest v2, local verification,
atomic current promote, rollback, no-op detection, failed candidate quarantine가
구현되어 있다. 구현은 현재 CLI orchestration helper와 `release.py` primitive로
구성되며, 별도 service class 도입은 실제 복잡도 감소가 있을 때만 진행한다.

Deliverables:

- `ReleaseTransaction` service
- candidate directory lifecycle
- final manifest v2
- current promote
- failed candidate quarantine
- local verification gates

Acceptance:

- import existing root into release
- publish changed ticker via transaction
- failure leaves current unchanged

### Step 3: CLI unification

Deliverables:

- `release publish`
- `release build`
- `release promote`
- `release rollback`
- `index plan/build/verify`
- wrappers updated

Acceptance:

- `build-research-pipeline --publish-root` performs one release transaction after batch
- `update-ticker --publish` performs release transaction
- `publish-ticker` performs release transaction
- queue publish defaults to production-safe transaction

### Step 4: Fragment cache and parallel compile

Deliverables:

- artifact manifest
- content hash including referenced files
- SQLite fragment schema
- cache hit/miss logic
- process pool compile
- single-writer merge

Acceptance:

- no-op release skips build when content unchanged
- changed single ticker recompiles only dirty fragments
- full output row counts match old builder
- smoke queries pass

### Step 5: Shard-aware release outputs

Status as of 2026-06-11: company shard builder, global catalog, global topics index, shard manifest, shard verification, and release manifest/verify integration are implemented for `layout="monolith-and-shards"`. Serving router fan-out is implemented for ticker-scoped reads, topic discovery, and compare. Release smoke verifies multiple deterministic global-topic samples through router vs monolith top topic id overlap, route mode, object-fallback absence, and rank delta, then records the aggregate and per-sample result in the deterministic smoke baseline and `verify/ranking_quality.json`. `release calibrate-ranking-thresholds` can turn accepted ranking quality reports into an auditable recommended threshold set. Remaining ranking work is operational: choose production calibration inputs and apply approved thresholds.

Deliverables:

- company shard builder
- global catalog
- global topics
- manifest index layout v2
- shard verification
- monolith remains full-scope verification output

Acceptance:

- shard counts match monolith
- ticker query works on shard
- compare query works over shards
- fallback to monolith works and is diagnostic

### Step 6: MCP release-aware pool

Status as of 2026-06-11: MCP store creation uses `open_ontology_store`; persistent pool rotation is keyed by resolved index signature, so a `current` symlink switch opens the new release for new leases while old leases keep their previous store until release. Health reports shard layout from manifest without opening SQLite, `/metrics` exports release/hot-swap gauges and counters, and `ops/observability/` contains Prometheus alert rules, Alertmanager routing, plus a Grafana dashboard.

Deliverables:

- release-aware store pool
- request release pinning
- current change detection
- old release pool TTL/refcount close
- health reports current release

Acceptance:

- promote while server is running
- new request sees new release
- old in-flight request does not fail
- health release id matches current

### Step 7: Remote prod transaction

Deliverables:

- remote candidate extract/verify
- remote current switch
- reload/health gate
- rollback on failed activation
- retention cleanup

Acceptance:

- remote current untouched on pre-switch failure
- remote rollback succeeds
- health mismatch fails publish

## 16. Testing strategy

### 16.1 Unit tests

Add tests for:

- artifact fingerprint includes referenced files
- cache key changes when builder version changes
- cache key does not change from irrelevant mtime
- fragment compile writes expected rows
- corrupted fragment rebuilds
- merge order deterministic
- atomic build preserves old index on failure
- v2 manifest validation
- release transaction failure leaves current unchanged
- rollback switches current and writes event

### 16.2 Integration tests

Add tests for:

- `release import-current`
- `release publish` with changed ticker
- `build-research-pipeline --publish-root` batch publishes once
- `update-ticker --publish` release transaction
- `publish-ticker` release transaction
- queue batch publish transaction
- no-op publish
- shard and monolith output consistency

### 16.3 MCP tests

Add tests for:

- health reports release id
- persistent store reopens after release change
- old store lease can finish while new current is active
- shard router handles ticker query
- compare fans out to shards
- fallback to monolith is reported

### 16.4 Regression tests

Smoke query regression fields:

- query
- ticker/tickers
- mode
- result count
- top object ids
- top topic ids
- citation availability
- route used: monolith/shard/global
- elapsed ms is diagnostic-only and excluded from deterministic smoke hashes

Regression gate should tolerate ranking improvements only when explicit baseline update is accepted.

## 17. Acceptance criteria

The migration is complete when:

- no public CLI mutates current release directly
- every publish path uses release transaction
- build failure leaves current and old index intact
- verification failure blocks promote
- release manifest v2 records all index outputs
- fragment cache is content-addressed and release-external
- fragment cache has operator-visible status and conservative dry-run GC
- index build uses parallel artifact compile and single-writer merge
- shard outputs are built and verified
- MCP serving is release-aware
- remote prod publish verifies before switch and health-checks after switch
- rollback is a first-class transaction
- existing tool contracts remain usable

## 18. Operational examples

Initial import:

```bash
krw-ontology release import-current \
  --env dev \
  --from-root /Users/.../krw-ontology-data \
  --releases-root /Users/.../krw-ontology-releases
```

Update ticker and publish:

```bash
krw-ontology update-ticker AAPL \
  --document-type 10-Q \
  --period FY2026Q1 \
  --publish \
  --publish-root /Users/.../krw-ontology-releases \
  --publish-env dev
```

Batch research publish:

```bash
krw-ontology build-research-pipeline AAPL MSFT NVDA \
  --root /Users/.../krw-ontology-data-running \
  --publish-root /Users/.../krw-ontology-releases \
  --publish-env dev
```

Queue worker:

```bash
krw-ontology config set publish-root /Users/.../krw-ontology-releases
krw-ontology queue start \
  --root /Users/.../krw-ontology-data-running
```

Configured force release:

```bash
krw-ontology config set running-root /Users/.../krw-ontology-data-running
krw-ontology config set publish-root /Users/.../krw-ontology-releases
export KRW_ONTOLOGY_ENV=dev
krw-ontology release force
krw-ontology quality check
krw-ontology quality gate
```

Quality read commands inspect `<publish-root>/<env>/current` by default. Quality
repair commands write repair state and mutable artifacts under the configured
running root; after repair, run `release force` again to create a new verified
dev current.

Promote to prod:

```bash
krw-ontology prod doctor
krw-ontology prod publish --delta
```

When `publish-root` points at a releases root, `prod publish` resolves the local
upload source to `<publish-root>/<env>/current` by default. Use `--from-env` only
when uploading a non-default local env.

Rollback:

```bash
krw-ontology release rollback \
  --releases-root /Users/.../krw-ontology-releases \
  --env prod
```

Inspect:

```bash
krw-ontology release status --releases-root /Users/.../krw-ontology-releases --env prod
krw-ontology index explain-last-build --root /Users/.../krw-ontology-releases/prod/current
```

## 19. Non-negotiable invariants

These invariants must be treated as tests:

```text
current is never mutated directly
candidate is never served
failed candidate is never promoted
serving index is never deleted before replacement is ready
artifact and index are promoted together
verification failure blocks promote
cache failure falls back to rebuild
MCP old request can finish on old release
new request sees new current release
rollback is atomic
```
