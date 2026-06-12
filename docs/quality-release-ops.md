# KRW Ontology 품질 repair 및 dev release 운영 문서

이 문서는 `krw-ontology`의 품질 repair, 일반 queue, dev index publish 흐름을 운영자 기준으로 정리한다.

Release/index 구현 계약과 개발 절차는
[`v3-final-production-master-development-guide.md`](v3-final-production-master-development-guide.md)를
source of truth로 사용한다. 일반 운영자의 최종 권장 명령은 `release force`다.

핵심 원칙:

- ontology artifact의 근거와 신뢰를 우선한다.
- 자동 repair는 claim 문장, 숫자 값, support reference를 추측 수정하지 않는다.
- 장시간 작업은 기본적으로 background worker로 실행한다.
- 상태 확인은 `watch`, 로그 확인은 `log` 또는 `*-watch` 명령으로 한다.

## 1. 전체 운영 흐름

일반적인 순서:

```bash
krw-ontology quality check --env dev
krw-ontology quality repair plan --env dev
krw-ontology quality repair run
krw-ontology quality repair watch
krw-ontology quality repair log --follow
krw-ontology queue status --compact
krw-ontology release force
krw-ontology release watch
```

중요한 구분:

```text
quality repair = running artifact 또는 quality queue 상태를 보완
general queue = docs_missing 같은 티커 재수집/full_refresh 처리
release force = running-root를 새 immutable v3 release로 만들고 검증한 뒤 dev/current로 promote
```

## 2. Quality repair

### 2.1 Plan 생성

```bash
krw-ontology quality repair plan --env dev
```

출력 예:

```text
Repair plan created: qr_20260602_134346
Release: dev/current
Queue: /Users/.../krw-ontology-data-running/.krw_pipeline/quality
Jobs: 797
```

plan은 그 시점의 release SQL index를 기준으로 만든 스냅샷이다.

코드나 artifact가 크게 바뀌면 새 plan을 다시 만드는 것이 맞다.

### 2.2 기본 실행

이제 기본 실행은 이 명령 하나다.

```bash
krw-ontology quality repair run
```

기본 의미:

```text
latest repair plan 자동 선택
all executable pending jobs 선택
실행 확정
background worker 시작
```

즉 내부적으로는 다음과 같은 의미다.

```text
krw-ontology quality repair run --plan <latest> --all --yes --background
```

출력 예:

```text
Started quality repair worker pid=12345
plan: qr_20260602_134346
selected_jobs: 794
log: /Users/.../.krw_pipeline/quality/logs/worker.log
watch: krw-ontology quality repair watch --plan qr_20260602_134346
```

### 2.3 Preview

실행하지 않고 선택될 job만 보려면:

```bash
krw-ontology quality repair run --preview
```

특정 plan:

```bash
krw-ontology quality repair run --plan qr_20260602_134346 --preview
```

특정 kind:

```bash
krw-ontology quality repair run --kind normalize_numeric --preview
```

### 2.4 Foreground 실행

디버깅 목적이면 foreground로 실행할 수 있다.

```bash
krw-ontology quality repair run --foreground
```

일반 운영에서는 background 기본값을 사용한다.

### 2.5 상태 확인

```bash
krw-ontology quality repair watch
```

특정 plan:

```bash
krw-ontology quality repair watch --plan qr_20260602_134346
```

한 번만 보기:

```bash
krw-ontology quality repair watch --once
```

출력 해석:

```text
Worker: running pid=...
Jobs: pending=... running=... succeeded=... failed=... cancelled=...
Kinds: batch_failure=..., docs_missing=..., normalize_numeric=...
```

`Worker: stopped`, `running=0`이면 quality repair worker는 끝난 상태다.

`pending`이 남아 있어도 executor가 없는 kind라면 정상일 수 있다.

### 2.6 로그 확인

```bash
krw-ontology quality repair log
```

follow:

```bash
krw-ontology quality repair log --follow
```

로그 파일 위치:

```text
<running-root>/.krw_pipeline/quality/logs/worker.log
```

### 2.7 Job 목록

```bash
krw-ontology quality repair list
```

필터:

```bash
krw-ontology quality repair list --status pending
krw-ontology quality repair list --kind repair_reference
krw-ontology quality repair list --kind normalize_numeric --status succeeded
```

## 3. Repair kind별 동작

### 3.1 docs_missing

`docs_missing`은 quality repair가 직접 문서를 생성하지 않는다.

대신 일반 queue에 `full_refresh` job을 넣는다.

```text
quality queue docs_missing
-> general queue full_refresh years=3
```

확인:

```bash
krw-ontology queue status --compact
```

일반 queue worker가 멈춰 있으면:

```bash
krw-ontology queue recover-stale
krw-ontology queue start
krw-ontology queue watch
```

주의:

```text
docs_missing succeeded = 일반 queue에 등록 성공
문서 재수집 완료 아님
```

### 3.2 batch_failure

Agent SDK stage를 재시도한다.

예:

```text
extract_evidence_quotes
extract_research_claims
extract_assumption_candidates
```

Claude/API safety filter로 특정 batch가 실패할 수 있다.

```text
extract_assumption_candidates batch 7 failed; continuing
```

이 메시지는 전체 worker가 죽었다는 뜻은 아니다.

### 3.3 section_fail / section_warn

해당 문서의 section extraction을 다시 수행한다.

문서 구조 산출물은 바뀔 수 있다.

### 3.4 normalize_numeric

근거 보존형 재검증/재계산만 수행한다.

하는 일:

```text
numeric_evidence.jsonl 재생성
numeric rejected 후보 재검증
report 생성
```

하지 않는 일:

```text
claim 문장 수정
숫자 값 수정
rejected object 자동 accepted 승격
```

### 3.5 repair_reference

파생 reference tail을 재계산하고 unresolved report를 만든다.

하는 일:

```text
support_links.jsonl 재생성
edges.jsonl 재생성
reference/relation unresolved report 생성
```

하지 않는 일:

```text
없는 quote를 비슷한 quote로 자동 대체
supported_by_quotes 추측 수정
claim/assumption 의미 수정
```

### 3.6 coverage_gap

현재는 자동 executor가 없다.

이유:

```text
무엇을 더 추출할지 판단이 필요함
근거를 추측해서 채우면 ontology 품질이 나빠질 수 있음
```

## 4. Repair report

report 위치:

```text
<running-root>/.krw_pipeline/quality/reports/<job-id>.json
```

job payload에는 report 경로가 들어간다.

```json
{
  "policy": "evidence_preserving_revalidation",
  "report_path": "/Users/.../.krw_pipeline/quality/reports/<job-id>.json",
  "auto_promoted_objects": 0,
  "auto_modified_claims": 0,
  "unresolved_count": 3
}
```

중요한 invariant:

```text
auto_promoted_objects = 0
auto_modified_claims = 0
```

이 값은 자동 repair가 근거/의미를 임의로 고치지 않았다는 안전장치다.

## 5. General queue

일반 queue는 이미 background worker 구조다.

시작:

```bash
krw-ontology queue start
```

상태:

```bash
krw-ontology queue status --compact
```

로그:

```bash
krw-ontology queue watch
```

stale running 정리:

```bash
krw-ontology queue recover-stale
```

주의:

```text
Stop requested: yes
```

이면 worker가 새 job을 잡지 않는다. 필요하면 stale 정리 후 다시 시작한다.

## 6. Dev v3 release 생성

### 6.1 기본 실행

```bash
krw-ontology release force
```

기본 동작:

```text
background worker 시작
configured running-root를 읽음
running-root artifact를 새 dev candidate release로 materialize
candidate 안에서 company shard와 spine fragment 생성 또는 cache 재사용
global_spine.sqlite와 shard_manifest.json 생성
manifest v3와 verification report 작성
검증 성공 후 dev/current atomic promote
실패 candidate는 dev/failed/로 격리
```

중요:

```text
running-root는 mutable source이고 dev/current는 immutable serving pointer다.
release force는 current를 직접 rebuild하지 않는다.
force는 새 release 생성을 강제하지만 valid cache는 사용할 수 있다.
cache를 전부 우회하려면 명시적으로 --no-cache를 사용한다.
```

내부 의미:

```text
materialize running-root -> releases/dev/<release-id>
build/reuse indexes/companies/<TICKER>.sqlite
build/reuse indexes/fragments/spine/<TICKER>.sqlite
merge indexes/global_spine.sqlite
write manifest v3
verify release + topology + smoke
promote releases/dev/current -> <release-id>
```

출력 예:

```text
Started release worker pid=12345
release_id: 20260603_231500
env: dev
source_root: /Users/.../krw-ontology-data-running
release_root: /Users/.../krw-ontology-data/releases/dev/20260603_231500
global_spine: /Users/.../releases/dev/20260603_231500/indexes/global_spine.sqlite
log: /Users/.../releases/dev/20260603_231500/logs/release-build.log
progress: /Users/.../releases/dev/20260603_231500/indexes/build_progress.jsonl
watch: krw-ontology release watch 20260603_231500
```

### 6.2 Foreground 실행

```bash
krw-ontology release force --foreground
```

디버깅용이다. 일반 운영에서는 기본 background를 사용한다.

### 6.3 실행 전 plan

```bash
krw-ontology release plan
```

이 명령은 source와 cache를 읽어 dirty/cached ticker와 build DAG를 보여주지만
release output은 쓰지 않는다.

### 6.4 상태 확인

현재 dev current와 latest worker:

```bash
krw-ontology release status
```

특정 release:

```bash
krw-ontology release inspect 20260603_231500
```

### 6.5 로그 확인

latest:

```bash
krw-ontology release watch
```

특정 release:

```bash
krw-ontology release watch 20260603_231500
```

progress file:

```text
<dev-release-root>/indexes/build_progress.jsonl
```

worker log:

```text
<dev-release-root>/logs/release-build.log
```

## 7. force와 no-cache의 차이

`release force`:

```text
항상 새 immutable release를 만든다.
변경되지 않은 company shard와 spine fragment cache는 재사용할 수 있다.
```

`release force --no-cache`:

```text
항상 새 immutable release를 만든다.
company shard와 spine fragment cache read를 우회하고 source에서 다시 계산한다.
```

일반 운영에서는 보통 이 명령을 쓴다.

```bash
krw-ontology release force
```

`--no-cache`는 cache 손상 조사, schema invalidation 검증, cold build baseline에서만
사용한다.

## 8. Prod 반영

dev 확인:

```bash
krw-ontology release startup-check --env dev
krw-ontology quality check --env dev
```

prod 서버 publish는 configured dev/current를 기본으로 사용하고 delta upload를 기본으로
한다.

```bash
krw-ontology prod publish-dev
```

quality check는 자동 prod gate가 아니다. 사용자가 원할 때 실행한다.

## 9. 자주 보는 문제

### 9.1 `documents=0 objects=0`

원인:

```text
빈 release root를 대상으로 index를 만들었거나
input root에 companies artifact가 없음
```

권장:

```bash
krw-ontology release plan
krw-ontology release force
```

이 명령들은 configured running-root를 input으로 사용한다.

### 9.2 `Worker: stopped`인데 pending이 남음

quality repair:

```text
executor 없는 kind가 pending으로 남을 수 있음
coverage_gap 등
```

general queue:

```text
worker가 멈춰 있으면 pending은 처리되지 않음
queue recover-stale 후 queue start 필요
```

### 9.3 Claude API 400 safety filter

예:

```text
API Error: 400 [1301] System detected potentially unsafe or sensitive content
```

의미:

```text
특정 extraction batch가 safety filter에 걸림
worker 전체가 죽었다는 뜻은 아님
```

로그와 quality events를 확인한다.

```bash
krw-ontology quality repair log --follow
krw-ontology quality events --ticker <TICKER> --env dev
```

## 10. 권장 일상 명령 세트

품질 확인:

```bash
krw-ontology quality check --env dev
krw-ontology quality tickers --env dev --severity high
```

repair:

```bash
krw-ontology quality repair plan --env dev
krw-ontology quality repair run
krw-ontology quality repair watch
```

repair 로그:

```bash
krw-ontology quality repair log --follow
```

일반 queue:

```bash
krw-ontology queue status --compact
krw-ontology queue start
krw-ontology queue watch
```

dev index publish:

```bash
krw-ontology release plan
krw-ontology release force
krw-ontology release watch
krw-ontology release status
```
