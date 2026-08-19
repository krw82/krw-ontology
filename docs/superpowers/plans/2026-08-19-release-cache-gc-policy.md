# 릴리스 캐시 GC 정책 재정비 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 글로벌 스파인 캐시(빌드당 ~48GB)가 GC에 보이지 않아 무한 누적되는 버그를 고치고, promote 후 자동 GC(keep=1 정책)로 디스크를 세대 1개 수준으로 제어한다.

**Architecture:** 캐시 GC의 열거/참조 판정에 `v3/global_spines`를 추가하고(플랜 페이로드에 스파인 캐시 키 노출), release cache gc / release gc의 코어 로직을 CLI 커맨드에서 호출 가능한 헬퍼로 추출한 뒤, `_publish_root_as_local_release`의 promote 성공 직후 `locks/cache_gc.lock`(FileProcessLock) 하에 자동 실행한다. 유지 정책은 CLI config 키(`release-keep-releases=1`, `release-auto-gc=on`)로 제어한다. 스파인 fragment 캐시 키의 과잉 바인딩(`source_manifest_hash`)을 제거해 빌드마다 355개 전체가 무효화되는 낭비(19G/빌드)를 없앤다.

**Tech Stack:** Python 3.11+, typer CLI, pytest (CliRunner), SQLite, 기존 `FileProcessLock`(`src/krw_ontology/pipeline/queue.py:135`).

## Global Constraints

- 모든 gc는 dry-run 기본. `--yes`/자동 후크만 삭제. 자동 후크도 "유지 릴리스(current + 최신 keep N개)"는 절대 삭제하지 않는다.
- promote 이후 단계의 어떤 실패도 빌드/승격 결과를 실패로 만들지 않는다(예외 삼킴 + 로그).
- manifest.json 포맷 변경 금지(스파인 키는 플랜/build_summary에만 존재 — keep=1 정책에서 GC가 플랜 재계산으로 키를 얻으므로 manifest 필드 추가는 불필요, 원플랜의 Phase 2 union이 폐기됨에 따라 제외).
- 유지 정책 기본값: `release-keep-releases=1`(사용자 결정, 2026-08-19: "둘 다 1". 롤백 포기 전제 — 문서에 트레이드오프 기재). 나중에 config만 바꿔 2로 올릴 수 있어야 함.
- Phase 4(스파인 증분 채택)는 본 플랜에 포함하지 않는다. 별도 브랜치/별도 플랜으로 분리(원플랜 합의와 동일).
- 실행 브랜치: `codex/release-cache-gc-policy` (main.py 행 번호는 031f983 기준).
- 테스트 실행: `uv run pytest tests/unit/test_agent_index.py tests/unit/test_cli.py tests/unit/test_spine_builder.py -x -q`

---

### Task 1: 글로벌 스파인 캐시 — GC 가시성 + 참조 판정

**Files:**
- Modify: `src/krw_ontology/cli/main.py:11639-11675` (`_v3_cache_path_from_key`, `_iter_v3_cache_files`, `_index_cache_kind`)
- Modify: `src/krw_ontology/cli/main.py:11559-11572` 영역 뒤 (`_v3_index_cache_snapshot` 참조 집합), `:11446-11448` 출력 (status `referenced_by_kind`), `:11467-11479` (index gc 삭제 루프)
- Modify: `src/krw_ontology/cli/main.py:6226-6234` (`_sqlite_cache_file_family`)
- Modify: `src/krw_ontology/agent_index/spine_builder.py:1501-1528` 영역 뒤, `:1615-1622` (global_spine_merge 노드), `:1691-1740` (플랜 반환값)
- Test: `tests/unit/test_agent_index.py:916-954` (status), `:957-1049` (gc)

**Interfaces:**
- Consumes: `_global_spine_semantic_cache_key(fragment_results: Sequence[SpineFragmentResult], *, source_manifest_hash, generate_links) -> str` (spine_builder.py:266 — `.ticker`/`.cache_key` 속성만 읽음), `_verify_global_spine_cache(path, *, cache_key) -> tuple[str,...]` (spine_builder.py:302)
- Produces: 플랜 반환값에 `"global_spine_cache": {"hit": bool, "key": str|None, "path": str|None, "errors": list[str]}` (Task 4의 자동 gc가 `_v3_index_cache_snapshot`을 거쳐 이 값을 참조 집합으로 사용). kind 문자열 `"global_spines"`.

- [ ] **Step 1: 실패 테스트 작성 — status/gc에 글로벌 스파인 반영**

`tests/unit/test_agent_index.py`의 `test_index_cache_status_reports_referenced_v3_entries`(916) 수정:

```python
    assert result.exit_code == 0, result.output
    assert "V3 index cache: ok" in result.output
    assert "Entries: total=5 referenced=5 missing_referenced=0 unreferenced=0" in result.output
    assert "Artifact fragment cache: referenced=1" in result.output
    assert "Company cache: referenced=1" in result.output
    assert "Spine cache: referenced=1" in result.output
    assert "Global spine cache: referenced=1" in result.output
    assert "Router cache: referenced=1" in result.output
    assert "Tickers: VG" in result.output
```

같은 파일 `test_index_cache_gc_removes_unreferenced_v3_entries_only_with_yes`(957)에 stale 스파인 케이스 추가. `referenced_router = ...` 줄(977) 뒤에:

```python
    referenced_global_spine = next((cache_root / "v3" / "global_spines").rglob("*.sqlite"))
```

그리고 `stale_router_seal.write_text(...)`(991) 뒤에:

```python
    stale_global_spine = cache_root / "v3" / "global_spines" / "bb" / "stale-spine.sqlite"
    stale_global_spine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(referenced_global_spine, stale_global_spine)
    stale_spine_seal = stale_global_spine.with_name(stale_global_spine.name + ".verify.json")
    stale_spine_seal.write_text("{}", encoding="utf-8")
```

기존 assertion 갱신: `"Candidates: 4"` → `"Candidates: 5"`, `"Deleted: 4"` → `"Deleted: 5"`, dry-run 검사에 `assert stale_global_spine.exists()` / `assert stale_spine_seal.exists()` 추가, 삭제 검사에 `assert not stale_global_spine.exists()` / `assert not stale_spine_seal.exists()` / `assert referenced_global_spine.exists()` 추가.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_agent_index.py::test_index_cache_status_reports_referenced_v3_entries tests/unit/test_agent_index.py::test_index_cache_gc_removes_unreferenced_v3_entries_only_with_yes -q`
Expected: FAIL — status는 `total=5` 불일치 및 "Global spine cache" 라인 부재, gc는 `v3/global_spines` 밑 파일이 후보로 잡히지 않아 Candidates 수 불일치.

- [ ] **Step 3: 구현 — main.py 열거/종류/경로**

`_v3_cache_path_from_key`(11639) 디렉터리 맵에 추가:

```python
    directory = {
        "company_shard": "company_shards",
        "spine_fragment": "spine_fragments",
        "router_sidecar": "router_sidecars",
        "global_spine": "global_spines",
    }[kind]
```

`_iter_v3_cache_files`(11649) 순회 목록에 `cache_root / "v3" / "global_spines"` 추가 (fragments/company_shards/spine_fragments/router_sidecars와 동일 `*/*.sqlite` glob).

`_index_cache_kind`(11662)에 브랜치 추가:

```python
    if "global_spines" in relative.parts:
        return "global_spines"
```

- [ ] **Step 4: 구현 — spine_builder.py 플랜에 스파인 캐시 키 노출**

spine_builder.py, `router_cache_hit = not no_cache and not router_cache_errors`(1528) 뒤에:

```python
    global_spine_cache_key: str | None = None
    global_spine_cache_path_value: Path | None = None
    global_spine_cache_errors: tuple[str, ...] = ("no_cache",) if no_cache else ()
    if not no_cache:
        ordered_fragment_standins = [
            SimpleNamespace(ticker=str(row["ticker"]), cache_key=str(row["spine_fragment_cache_key"]))
            for row in companies
        ]
        global_spine_cache_key = _global_spine_semantic_cache_key(
            ordered_fragment_standins,
            source_manifest_hash=plan.source_manifest_hash,
            generate_links=generate_links,
        )
        global_spine_cache_path_value = _global_spine_cache_path(
            plan.cache_root, global_spine_cache_key
        )
        global_spine_cache_errors = _verify_global_spine_cache(
            global_spine_cache_path_value, cache_key=global_spine_cache_key
        )
    global_spine_cache_hit = not no_cache and not global_spine_cache_errors
```

파일 상단에 `from types import SimpleNamespace`가 없으면 추가. (주: `_global_spine_semantic_cache_key`는 355개 전체 `[ticker, cache_key]` 순서쌍 + `source_manifest_hash` + 버전 문자열의 `_stable_hash`라 데이터가 하나라도 바뀌면 새 키 — 플랜에서 계산만 하고 재사용 판단은 기존 빌드 경로 그대로.)

`global_spine_merge` 노드(1615-1622)에 캐시 필드 추가:

```python
        {
            "id": "global_spine_merge",
            "stage": "global_spine_merge",
            "depends_on": ["semantic_preflight"],
            "output": _path_label(resolved_root, global_spine_path),
            "cache_key": global_spine_cache_key,
            "cache_path": str(global_spine_cache_path_value)
            if global_spine_cache_path_value is not None
            else None,
            "cache_hit": global_spine_cache_hit,
            "cache_errors": list(global_spine_cache_errors),
            "status": "cached" if global_spine_cache_hit else "rebuild",
        },
```

반환 dict(1691)의 `"router_sidecar_cache": {...}`(1717-1722) 옆에:

```python
        "global_spine_cache": {
            "hit": global_spine_cache_hit,
            "key": global_spine_cache_key,
            "path": str(global_spine_cache_path_value)
            if global_spine_cache_path_value is not None
            else None,
            "errors": list(global_spine_cache_errors),
        },
```

- [ ] **Step 5: 구현 — snapshot 참조 집합 + status 출력 + 사이드카 삭제 통일**

main.py `_v3_index_cache_snapshot`: `router_cache` 블록(11559-11572) 뒤에:

```python
    global_spine_cache = plan.get("global_spine_cache")
    if isinstance(global_spine_cache, Mapping):
        global_spine_key = str(global_spine_cache.get("key") or "")
        if global_spine_key:
            referenced[
                _v3_cache_path_from_key(resolved_cache_root, "global_spine", global_spine_key)
            ] = {
                "kind": "global_spines",
                "ticker": None,
                "cache_key": global_spine_key,
            }
```

`referenced_by_kind` dict(11587-11600 부근)에 추가:

```python
        "global_spines": sum(
            1 for entry in entries if entry["kind"] == "global_spines" and entry["referenced"]
        ),
```

`index cache status` 출력(11400-11405 부근, "Router cache:" 줄 근처)에:

```python
    typer.echo(f"Global spine cache: referenced={snapshot['referenced_by_kind']['global_spines']}")
```

`_sqlite_cache_file_family`(6226) 확장 — seal/verify 사이드카까지 한 번에 삭제(기존: index gc가 router seal만 inline으로 지웠고, release gc는 seal을 전부 남기던 잠재 버그):

```python
def _sqlite_cache_file_family(path: Path) -> tuple[Path, ...]:
    if path.suffix == ".sqlite":
        return (
            path,
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
            Path(f"{path}-journal"),
            Path(f"{path.name}.seal.json"),
            Path(f"{path.name}.verify.json"),
        )
    return (path,)
```

(index gc의 삭제 루프 11469-11479는 router 특례 처리를 제거하고 `_sqlite_cache_file_family(path)` 순회 unlink로 교체 — 사이드카는 존재하지 않을 수 있으므로 `missing_ok=True`. `deleted_bytes`는 `.sqlite` 본체 크기 기준 유지.)

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_agent_index.py::test_index_cache_status_reports_referenced_v3_entries tests/unit/test_agent_index.py::test_index_cache_gc_removes_unreferenced_v3_entries_only_with_yes -q`
Expected: PASS (2 passed)

추가로 회귀: `uv run pytest tests/unit/test_agent_index.py tests/unit/test_spine_builder.py -q` — 스파인 캐시 관련 기존 테스트(`test_build_reuses_exact_global_spine_semantic_cache` 등)가 여전히 통과하는지 확인. `test_index_cache_status...`류에서 total 수가 하드코딩된 다른 테스트가 있으면 같은 방식으로 갱신.

- [ ] **Step 7: Commit**

```bash
git add src/krw_ontology/cli/main.py src/krw_ontology/agent_index/spine_builder.py tests/unit/test_agent_index.py
git commit -m "fix: make release index cache GC see global spine cache entries"
```

---

### Task 2: 유지 정책 config 키 (`release-keep-releases`, `release-auto-gc`)

**Files:**
- Modify: `src/krw_ontology/cli/config.py:15-47` (CONFIG_KEYS, CliConfig, from_dict)
- Modify: `src/krw_ontology/cli/main.py:2169-2180` (config show 출력), `:8725` 근처 (해석 헬퍼)
- Test: `tests/unit/test_cli.py` (config 테스트가 있는 클래스 근처에 추가)

**Interfaces:**
- Produces: `CliConfig.release_keep_releases: str|None`, `CliConfig.release_auto_gc: str|None`; main.py의 `_resolve_release_keep(keep_releases: int|None) -> int` (flag > config > 기본 1, 최소 1), `_release_auto_gc_enabled() -> bool` (env `KRW_RELEASE_AUTO_GC` in {"0","false","no","off"} → False, config `release-auto-gc`가 "0"/"false"면 False, 그 외 True). Task 4의 후크가 소비.

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cli.py`에 추가 (모듈 상위 임포트 그대로 사용):

```python
def test_config_supports_release_retention_keys(tmp_path: Path):
    config_path = tmp_path / "config.json"
    set_config_value("release-keep-releases", "2", path=config_path)
    set_config_value("release-auto-gc", "0", path=config_path)
    config = load_cli_config(config_path)
    assert config.release_keep_releases == "2"
    assert config.release_auto_gc == "0"


def test_release_retention_resolution_defaults_to_one(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    assert cli_main._resolve_release_keep(None) == 1
    assert cli_main._resolve_release_keep(3) == 3
    runner.invoke(app, ["config", "set", "release-keep-releases", "2"])
    assert cli_main._resolve_release_keep(None) == 2


def test_release_auto_gc_env_and_config_kill_switch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    assert cli_main._release_auto_gc_enabled() is True
    monkeypatch.setenv("KRW_RELEASE_AUTO_GC", "0")
    assert cli_main._release_auto_gc_enabled() is False
    monkeypatch.delenv("KRW_RELEASE_AUTO_GC")
    runner.invoke(app, ["config", "set", "release-auto-gc", "false"])
    assert cli_main._release_auto_gc_enabled() is False
```

(`from krw_ontology.cli.config import load_cli_config, set_config_value` 임포트가 test_cli.py에 없으면 상단에 추가.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_cli.py -k "release_retention or release_auto_gc or config_supports_release" -q`
Expected: FAIL — `KeyError: 'release-keep-releases'` 및 `_resolve_release_keep` 부재.

- [ ] **Step 3: 구현**

config.py: `CONFIG_KEYS`에 `"release-keep-releases", "release-auto-gc"` 추가; `CliConfig`에 `release_keep_releases: str | None = None`, `release_auto_gc: str | None = None`; `from_dict`에 두 필드 매핑 추가.

main.py config show(2169)에 두 줄 추가:

```python
    typer.echo(f"release-keep-releases: {config.release_keep_releases or '<unset>'}")
    typer.echo(f"release-auto-gc: {config.release_auto_gc or '<unset>'}")
```

main.py `_resolve_prod_settings`(8725) 근처에:

```python
def _resolve_release_keep(keep_releases: int | None) -> int:
    config = load_cli_config()
    keep_value = keep_releases
    if keep_value is None and config.release_keep_releases:
        try:
            keep_value = int(config.release_keep_releases)
        except ValueError as exc:
            raise ValueError("release-keep-releases must be an integer") from exc
    if keep_value is None:
        keep_value = 1
    if keep_value < 1:
        raise ValueError("release keep releases must be at least 1")
    return keep_value


def _release_auto_gc_enabled() -> bool:
    env_flag = os.environ.get("KRW_RELEASE_AUTO_GC", "").strip().lower()
    if env_flag in {"0", "false", "no", "off"}:
        return False
    config = load_cli_config()
    return (config.release_auto_gc or "1").strip().lower() not in {"0", "false", "no", "off"}
```

(`os`는 main.py에 이미 임포트됨.)

- [ ] **Step 4: 테스트 통과 확인 + Commit**

Run: `uv run pytest tests/unit/test_cli.py -k "release_retention or release_auto_gc or config_supports_release" -q`
Expected: PASS (3 passed)

```bash
git add src/krw_ontology/cli/config.py src/krw_ontology/cli/main.py tests/unit/test_cli.py
git commit -m "feat: add release-keep-releases and release-auto-gc config keys"
```

---

### Task 3: gc 코어 헬퍼 추출 + `cache_gc.lock`

**Files:**
- Modify: `src/krw_ontology/cli/main.py:5953-5993` (`release gc`), `:6045-6155` (`release cache gc`)
- Test: `tests/unit/test_cli.py` (신규 — 현재 release cache gc 테스트 coverage 0)

**Interfaces:**
- Consumes: `FileProcessLock`, `LockHeldError` (`from krw_ontology.pipeline.queue import FileProcessLock, LockHeldError` — main.py 기존 임포트에 추가), `_release_cache_target`(6156), `_delete_release_cache_candidate`(6209), `_prune_empty_release_cache_dirs`(6237), Task 2의 `_resolve_release_keep`
- Produces: 
  - `_execute_release_cache_gc(env_root: Path, *, keep_label: str = "current", include_unreferenced: bool = True, include_tmp: bool = True, tmp_minutes: int = 60, yes: bool) -> dict` — `{"env": str, "keep": str, "release_id": str, "cache_root": str, "entry_count": int, "unreferenced_count": int, "candidate_count": int, "candidate_bytes": int, "deleted_count": int, "deleted_bytes": int, "candidates": list[dict]}`
  - `_execute_release_gc(env_root: Path, *, keep: int, include_failed: bool = False, yes: bool) -> dict` — `{"keep": int, "protected": list[str], "deleted": list[str], "candidate_count": int}`
  - `_run_release_cache_gc_locked(env_root: Path, **kwargs) -> dict` — lock 획득 실패 시 `{"status": "skipped_lock_held", **}` 반환. Task 4가 소비.

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cli.py`에 추가. `_write_minimal_v3_release` 헬퍼(test_cli.py:132)로 가짜 릴리스를 만드는 패턴을 따름:

```python
def _write_release_cache_gc_fixture(releases_root: Path) -> Path:
    env_root = releases_root / "dev"
    _write_minimal_v3_release(env_root, "current-release")
    _write_minimal_v3_release(env_root, "old-release")
    os.utime(env_root / "old-release", (1_000_000_000, 1_000_000_000))
    (env_root / "current").symlink_to("current-release")
    (env_root / "locks").mkdir(parents=True, exist_ok=True)
    cache_root = env_root / ".index_fragment_cache"
    stale = cache_root / "v3" / "company_shards" / "ff" / "stale.sqlite"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"stale")
    stale.with_name(stale.name + ".verify.json").write_text("{}", encoding="utf-8")
    return cache_root


def test_execute_release_cache_gc_removes_unreferenced(tmp_path: Path):
    releases_root = tmp_path / "releases"
    cache_root = _write_release_cache_gc_fixture(releases_root)
    env_root = releases_root / "dev"

    dry = cli_main._execute_release_cache_gc(env_root, yes=False)
    assert dry["candidate_count"] == 1
    assert (cache_root / "v3" / "company_shards" / "ff" / "stale.sqlite").exists()

    deleted = cli_main._execute_release_cache_gc(env_root, yes=True)
    assert deleted["deleted_count"] == 1
    assert not (cache_root / "v3" / "company_shards" / "ff" / "stale.sqlite").exists()
    assert not (cache_root / "v3" / "company_shards" / "ff" / "stale.sqlite.verify.json").exists()
    assert (env_root / "current").is_symlink()


def test_execute_release_gc_keeps_current_and_newest(tmp_path: Path):
    releases_root = tmp_path / "releases"
    _write_release_cache_gc_fixture(releases_root)
    env_root = releases_root / "dev"

    result = cli_main._execute_release_gc(env_root, keep=1, yes=True)
    assert "old-release" in result["deleted"]
    assert not (env_root / "old-release").exists()
    assert (env_root / "current-release").exists()
    assert (env_root / "current").is_symlink()


def test_run_release_cache_gc_skips_when_lock_held(tmp_path: Path):
    releases_root = tmp_path / "releases"
    _write_release_cache_gc_fixture(releases_root)
    lock_path = releases_root / "dev" / "locks" / "cache_gc.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "created_at": "now"}), encoding="utf-8")

    result = cli_main._run_release_cache_gc_locked(releases_root / "dev", yes=True)

    assert result["status"] == "skipped_lock_held"
    assert (releases_root / "dev" / ".index_fragment_cache").exists()
```

(`os`, `json` 임포트는 test_cli.py 상단에 이미 있음 — 없으면 추가. `_write_minimal_v3_release(env_root, name)`의 실제 시그니처가 다르면(예: release_root 인자) 132행 정의를 확인해 맞춘다 — 내부에서 manifest/current 구조만 만들면 됨.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_cli.py -k "release_cache_gc or execute_release_gc" -q`
Expected: FAIL — `AttributeError: module 'krw_ontology.cli.main' has no attribute '_execute_release_cache_gc'`

- [ ] **Step 3: 구현 — 코어 추출**

`release_cache_gc_cmd`(6045)의 본문 중 target/snapshot/candidates/삭제 블록(6081-6131)을 그대로 옮겨:

```python
def _execute_release_cache_gc(
    env_root: Path,
    *,
    keep_label: str = "current",
    include_unreferenced: bool = True,
    include_tmp: bool = True,
    tmp_minutes: int = 60,
    yes: bool,
    workers: int | None = None,
) -> dict[str, Any]:
    env_root = env_root.expanduser().resolve()
    releases_root = env_root.parent.parent  # <releases_root>/<env> 구조
    target = _release_cache_target(
        releases_root=releases_root, env=env_root.name, keep=keep_label
    )
    snapshot = _v3_index_cache_snapshot(
        target["release_root"], target["cache_root"], workers=workers, source_manifest_path=None
    )
    candidates: list[dict[str, Any]] = []
    if include_unreferenced:
        candidates.extend(
            {**entry, "reason": "unreferenced"}
            for entry in snapshot["entries"]
            if entry.get("referenced") is False and not entry.get("missing")
        )
    if include_tmp:
        now = time.time()
        minimum_age_seconds = tmp_minutes * 60
        for path in _iter_release_cache_tmp_files(Path(str(target["cache_root"]))):
            try:
                stat = path.stat()
            except OSError:
                continue
            if max(0, int(now - stat.st_mtime)) < minimum_age_seconds:
                continue
            candidates.append(
                {
                    "kind": "tmp",
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "reason": f"tmp_older_than_{tmp_minutes}m",
                }
            )
    deleted_count = 0
    deleted_bytes = 0
    if yes:
        for entry in candidates:
            removed = _delete_release_cache_candidate(
                Path(str(entry["path"])), Path(str(target["cache_root"]))
            )
            deleted_count += 1 if removed >= 0 else 0
            deleted_bytes += max(0, removed)
        _prune_empty_release_cache_dirs(Path(str(target["cache_root"])))
    return {
        "env": target["env"],
        "keep": target["keep"],
        "release_id": target["release_id"],
        "cache_root": str(target["cache_root"]),
        "entry_count": snapshot["entry_count"],
        "missing_referenced_count": snapshot["missing_referenced_count"],
        "unreferenced_count": snapshot["unreferenced_count"],
        "candidate_count": len(candidates),
        "candidate_bytes": sum(int(entry.get("size_bytes") or 0) for entry in candidates),
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "candidates": candidates,
    }
```

`release_cache_gc_cmd`는 이 헬퍼를 `_run_release_cache_gc_locked` 경유로 호출하고 출력은 기존 포맷 그대로(`candidates: N bytes=...` 등) dict에서 렌더. `release_gc_cmd`(5953) 본문도 동일하게 `_execute_release_gc`로 추출(current 보호 로직 5968-5974, dry-run 분기 유지):

```python
def _execute_release_gc(
    env_root: Path, *, keep: int, include_failed: bool = False, yes: bool
) -> dict[str, Any]:
    env_root = env_root.expanduser().resolve()
    current_id = current_release_id(env_root)
    releases = list_release_ids(env_root)
    protected = set(releases[:keep])
    if current_id:
        protected.add(current_id)
    candidates = [env_root / release_id for release_id in releases if release_id not in protected]
    if include_failed and (env_root / FAILED_RELEASE_DIRNAME).is_dir():
        candidates.extend(path for path in (env_root / FAILED_RELEASE_DIRNAME).iterdir() if path.is_dir())
    deleted: list[str] = []
    if yes:
        for path in candidates:
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError(f"Refusing to delete non-directory release candidate: {path}")
            shutil.rmtree(path)
            deleted.append(path.name)
    return {
        "keep": keep,
        "current": current_id,
        "protected": sorted(protected),
        "candidates": [str(path) for path in candidates],
        "deleted": deleted,
    }
```

lock 래퍼:

```python
def _run_release_cache_gc_locked(env_root: Path, **kwargs: Any) -> dict[str, Any]:
    lock = FileProcessLock(env_root.expanduser().resolve() / "locks" / "cache_gc.lock")
    try:
        lock.acquire()
    except LockHeldError:
        return {"status": "skipped_lock_held", "env_root": str(env_root)}
    try:
        result = _execute_release_cache_gc(env_root, **kwargs)
    finally:
        lock.release()
    result["status"] = "ok"
    return result
```

두 CLI gc 커맨드(`release cache gc`, `release gc`)도 같은 `cache_gc.lock`을 잡고 실행하도록 래핑(수동 gc와 자동 gc가 동시에 도는 것 방지). CLI 출력 포맷은 기존 그대로 유지해 기존 사용 경험 유지.

- [ ] **Step 4: 테스트 통과 확인 + 기존 release gc 테스트 회귀**

Run: `uv run pytest tests/unit/test_cli.py -k "release_cache_gc or execute_release_gc or release_list_inspect_and_gc_protect_current" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/krw_ontology/cli/main.py tests/unit/test_cli.py
git commit -m "refactor: extract release gc core helpers guarded by cache_gc.lock"
```

---

### Task 4: promote 후 자동 GC 후크

**Files:**
- Modify: `src/krw_ontology/cli/main.py:3208` 뒤 (`_publish_root_as_local_release`), `:2963` 뒤 (`_publish_tickers_as_release`)
- Test: `tests/unit/test_cli.py` (5189 패턴 확장)

**Interfaces:**
- Consumes: Task 2 `_resolve_release_keep`/`_release_auto_gc_enabled`, Task 3 `_run_release_cache_gc_locked`/`_execute_release_gc`, `_append_release_progress_event`
- Produces: `_run_post_promote_gc(*, releases_root: Path, env: str, release_id: str, env_root: Path, progress_path: Path, release_root: Path, started_at: float) -> dict` — 후크 본문. 항상 dict 반환, 절대 예외 전파 없음.

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_cli.py`의 publish-dev 격리 패턴(5189)을 확장:

```python
def test_release_publish_dev_runs_post_promote_gc(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260819_gc")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])
    calls: list[dict] = []

    def fake_cache_gc(env_root, **kwargs):
        calls.append({"kind": "cache", "env_root": str(env_root)})
        return {"status": "ok", "deleted_count": 0, "deleted_bytes": 0}

    def fake_release_gc(env_root, **kwargs):
        calls.append({"kind": "release", "keep": kwargs.get("keep")})
        return {"keep": kwargs.get("keep"), "protected": [], "deleted": [], "candidates": []}

    monkeypatch.setattr(cli_main, "_run_release_cache_gc_locked", fake_cache_gc)
    monkeypatch.setattr(cli_main, "_execute_release_gc", fake_release_gc)

    result = runner.invoke(
        app, ["release", "publish-dev", "--foreground", "--releases-root", str(releases_root)]
    )

    assert result.exit_code == 0, result.output
    assert {"kind": "cache", "env_root": str((releases_root / "dev").resolve())} in calls
    assert any(call["kind"] == "release" and call["keep"] == 1 for call in calls)


def test_release_publish_dev_survives_post_promote_gc_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260819_gcfail")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])

    def boom(env_root, **kwargs):
        raise RuntimeError("gc boom")

    monkeypatch.setattr(cli_main, "_run_release_cache_gc_locked", boom)
    monkeypatch.setattr(cli_main, "_execute_release_gc", boom)

    result = runner.invoke(
        app, ["release", "publish-dev", "--foreground", "--releases-root", str(releases_root)]
    )

    assert result.exit_code == 0, result.output
    assert (releases_root / "dev" / "current").is_symlink()


def test_release_publish_dev_skips_post_promote_gc_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KRW_ONTOLOGY_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("KRW_RELEASE_AUTO_GC", "0")
    monkeypatch.setattr(cli_main, "_default_release_id", lambda: "20260819_gcoff")
    running_root = tmp_path / "running"
    releases_root = tmp_path / "releases"
    _write_minimal_source_artifact(running_root, "CVX")
    runner.invoke(app, ["config", "set", "running-root", str(running_root)])
    calls: list[str] = []
    monkeypatch.setattr(
        cli_main, "_run_release_cache_gc_locked",
        lambda env_root, **kwargs: calls.append("cache") or {"status": "ok"},
    )

    result = runner.invoke(
        app, ["release", "publish-dev", "--foreground", "--releases-root", str(releases_root)]
    )

    assert result.exit_code == 0, result.output
    assert calls == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_cli.py -k "post_promote_gc" -q`
Expected: FAIL — gc 호출 안 됨(calls 비어 `assert ... in calls` 실패)

- [ ] **Step 3: 구현 — 후크 함수 + 삽입**

main.py (Task 3 헬퍼들 근처):

```python
def _run_post_promote_gc(
    *,
    releases_root: Path,
    env: str,
    release_id: str,
    env_root: Path,
    progress_path: Path,
    release_root: Path,
    started_at: float,
) -> dict[str, Any]:
    """Run non-fatal cache/release GC immediately after a successful promote."""
    summary: dict[str, Any] = {"status": "skipped_disabled"}
    try:
        if _release_auto_gc_enabled():
            keep = _resolve_release_keep(None)
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="started",
                output=env_root / ".index_fragment_cache",
                details={"keep": keep},
            )
            cache_result = _run_release_cache_gc_locked(env_root, yes=True)
            release_result = _execute_release_gc(env_root, keep=keep, yes=True)
            summary = {
                "status": cache_result.get("status", "ok"),
                "cache_deleted_bytes": cache_result.get("deleted_bytes", 0),
                "release_deleted": release_result.get("deleted", []),
                "keep": keep,
            }
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="complete",
                output=env_root / ".index_fragment_cache",
                details={"summary": summary},
                started_at=started_at,
            )
    except Exception as exc:  # gc 절대 빌드 실패로 전파 금지
        summary = {"status": "failed", "error": str(exc)}
        try:
            _append_release_progress_event(
                progress_path,
                release_root=release_root,
                release_id=release_id,
                node_id="post_promote_gc",
                stage="gc",
                status="failed",
                output=env_root / ".index_fragment_cache",
                details={"error": str(exc)},
                started_at=started_at,
            )
        except Exception:
            pass
    return summary
```

삽입 지점 1 — `_publish_root_as_local_release`의 promote-complete 이벤트(3198-3208) 바로 뒤, `except`(3209) 앞:

```python
                if promoted:
                    _run_post_promote_gc(
                        releases_root=resolved_releases_root,
                        env=resolved_env,
                        release_id=release_id,
                        env_root=env_root,
                        progress_path=progress_path,
                        release_root=release_root,
                        started_at=promote_started_at,
                    )
```

삽입 지점 2 — `_publish_tickers_as_release`의 동일 위치(2953-2963 뒤)에 같은 호출. 두 사이트 모두 `_run_post_promote_gc` 자체가 예외를 삼키므로 외부 try 블록 시맨틱 불변(promoted=True 이후 예외가 빌드를 실패시키지 않음).

- [ ] **Step 4: 테스트 통과 확인 + immutability 회귀**

Run: `uv run pytest tests/unit/test_cli.py -k "post_promote_gc or CurrentReleaseImmutability or publish_dev_quarantines" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/krw_ontology/cli/main.py tests/unit/test_cli.py
git commit -m "feat: run non-fatal release cache GC automatically after promote"
```

---

### Task 5: 스파인 fragment 캐시 키 과잉 바인딩 제거

**Files:**
- Modify: `src/krw_ontology/agent_index/spine_builder.py:3120-3138` (`_spine_fragment_cache_key`)
- Test: `tests/unit/test_spine_builder.py`

**Interfaces:**
- Consumes: `metric_dictionary_binding()` (spine_builder에 이미 임포트됨 — 1516 참조)
- Produces: 키 입력 변경. `_spine_fragment_cache_key(*, ticker, company_cache_key, source_manifest_hash)` 시그니처 유지(호출부 2곳: 1463, 1938 — `source_manifest_hash` 인자는 받지만 해시 입력에서 제거하여 호출부 변경 최소화).

- [ ] **Step 1: 실패 테스트 작성**

`tests/unit/test_spine_builder.py`에 추가:

```python
def test_spine_fragment_cache_key_ignores_source_manifest_hash():
    key_a = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-1", source_manifest_hash="manifest-a"
    )
    key_b = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-1", source_manifest_hash="manifest-b"
    )
    key_c = spine_builder._spine_fragment_cache_key(
        ticker="VG", company_cache_key="company-key-2", source_manifest_hash="manifest-a"
    )
    assert key_a == key_b
    assert key_a != key_c
```

(`import krw_ontology.agent_index.spine_builder as spine_builder` 형태의 임포트가 없으면 파일 상단 임포트 방식에 맞게 조정.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_spine_builder.py::test_spine_fragment_cache_key_ignores_source_manifest_hash -q`
Expected: FAIL — `key_a != key_b`

- [ ] **Step 3: 구현**

`_spine_fragment_cache_key`(3120)의 해시 dict에서 `"source_manifest_hash": source_manifest_hash` 제거하고, 회사 콘텐츠와 무관한 전역 바인딩은 이미 버전 문자열이 커버. 메트릭 사전이 fragment 행에 영향을 줄 수 있으므로 안전장치로 추가:

```python
def _spine_fragment_cache_key(
    *,
    ticker: str,
    company_cache_key: str | None,
    source_manifest_hash: str | None,
) -> str:
    return _stable_hash(
        {
            "spine_fragment_cache_format_version": SPINE_FRAGMENT_CACHE_FORMAT_VERSION,
            "spine_fragment_schema_version": SPINE_FRAGMENT_SCHEMA_VERSION,
            "spine_projection_version": SPINE_PROJECTION_VERSION,
            "semantic_identity_policy_version": SEMANTIC_IDENTITY_POLICY_VERSION,
            "global_spine_schema_version": GLOBAL_SPINE_SCHEMA_VERSION,
            "global_spine_builder_version": GLOBAL_SPINE_BUILDER_VERSION,
            "ticker": ticker,
            "company_cache_key": company_cache_key,
            "metric_dictionary": metric_dictionary_binding(),
        }
    )
```

(키가 바뀌므로 기존 캐시는 1회 전체 미스 후 재생성 — 이전 세대는 unreferenced가 되어 Task 1의 gc가 정리. `_verify_spine_fragment_cache`는 저장된 키와의 정합 검사라 코드 변경 불필요.)

- [ ] **Step 4: 스파인 빌더 전체 회귀**

Run: `uv run pytest tests/unit/test_spine_builder.py tests/unit/test_agent_index.py -q`
Expected: PASS (기존 캐시 키 안정성 테스트가 manifest hash 의존을 assert하고 있으면 해당 기대치를 본 테스트 의도에 맞게 갱신)

- [ ] **Step 5: Commit**

```bash
git add src/krw_ontology/agent_index/spine_builder.py tests/unit/test_spine_builder.py
git commit -m "fix: stop invalidating all spine fragment caches on any source change"
```

---

### Task 6: 문서 갱신

**Files:**
- Modify: `docs/production-release-index-architecture.md:572-573` (GC 삭제 범위), `:292-298` (locks)
- Modify: `docs/global-spine-company-shards-v3-development-guide.md:1047-1050` (reachability 범위)
- Modify: `docs/v3-production-development-guide.md` §23 (`:1056-1067`, retention 미정 항목)

- [ ] **Step 1: architecture doc 572행 GC 범위 문구 교체**

"GC 삭제 범위는 `<cache-root>/fragments/**/*.sqlite`와 해당 `-wal`, `-shm` sidecar로 제한한다"를 다음으로:

> GC 삭제 범위는 `<cache-root>/fragments/**`, `v3/company_shards/**`, `v3/spine_fragments/**`, `v3/router_sidecars/**`, `v3/global_spines/**`의 `.sqlite`와 그 `-wal`/`-shm`/`-journal`, `.seal.json`, `.verify.json` sidecar로 제한한다. 글로벌 스파인 캐시(빌드당 1개, 콘텐츠 해시 키)는 현재 빌드 플랜이 참조하는 세대만 유지한다.

298행 `cache_gc.lock` 항목에 "구현됨: `FileProcessLock` 기반, release cache gc/release gc/promote 후 자동 gc가 공유" 주석 추가. §"pipeline"의 `cleanup_or_retention`(169행)이 promote 후 자동 gc로 구현되었음을 명시.

- [ ] **Step 2: guide reachability 문구 갱신**

1047-1050의 "artifact fragment, company shard, spine fragment 세 계층" 문구를 "artifact fragment, company shard, spine fragment, router sidecar, global spine 다섯 계층"으로 갱신하고 자동 gc(config `release-keep-releases`, `release-auto-gc`, env `KRW_RELEASE_AUTO_GC`) 설명 추가.

- [ ] **Step 3: dev guide §23 retention 확정**

"release와 failed candidate retention 기간" 미정 항목을 확정: `release-keep-releases` 기본 1(2026-08-19 운영 결정, 디스크 최우선 — 롤백 불가 트레이드오프 명시, 필요 시 `krw-ontology config set release-keep-releases 2`로 즉시 복원), 자동 gc는 `release-auto-gc`로 제어.

- [ ] **Step 4: Commit**

```bash
git add docs/production-release-index-architecture.md docs/global-spine-company-shards-v3-development-guide.md docs/v3-production-development-guide.md
git commit -m "docs: document global spine cache GC scope and keep=1 retention policy"
```

---

### Task 7: 전체 검증 + 실제 dev 환경 정리 (운영 단계)

- [ ] **Step 1: 전체 유닛 테스트**

Run: `uv run pytest tests/unit/test_agent_index.py tests/unit/test_cli.py tests/unit/test_spine_builder.py -q`
Expected: 전체 PASS. 이후 `uv run pytest tests/unit -q`로 광역 회귀(시간 허용 시).

- [ ] **Step 2: dev dry-run 검증 (read-only)**

Run: `uv run krw-ontology release cache gc --env dev` (dry-run)
Expected: `v3/global_spines` 과거 세대(~9개, 수백 GB)가 unreferenced 후보로 표시됨. `uv run krw-ontology release gc --env dev --keep 1`도 dry-run으로 후보 확인(구 릴리스 20260813_121609).

- [ ] **Step 3: 실제 정리 (사용자 확인 후 실행)**

Run: `uv run krw-ontology release cache gc --env dev --yes` 후 `du -sh ~/krw-ontology-data/releases/dev/.index_fragment_cache`로 약 380G 감소 확인. 이후 다음 빌드부터 promote 직후 자동 gc가 keep=1을 유지한다.

- [ ] **Step 4: 최종 커밋/리포트**

남은 변경분 커밋, 요약 리포트(정리 전후 용량, 이후 빌드에서 자동 gc 동작 확인 방법) 작성.

---

## Self-Review 결과

- **범위**: 원플랜의 Phase 0(수동 rm)은 Task 7 Step 3의 `release cache gc --yes`로 대체(코드 우선, 더 안전). Phase 2(union 참조)는 keep=1 결정으로 불필요 — 제거. manifest 필드 기록은 동일 이유로 제거. Phase 4(증분 채택)는 별도 플랜으로 분리(원플랜 합의와 동일).
- **타입 일관성**: `_execute_release_cache_gc`/`_execute_release_gc`/`_run_release_cache_gc_locked`/`_run_post_promote_gc` 시그니처가 Task 3↔4 간 일치. kind 문자열은 snapshot 참조(`global_spines`)와 `_index_cache_kind` 반환값 통일.
- **리스크**: Task 1이 기존 status 테스트의 total 수(4→5)를 바꾸는 유일한 호환성 변경점. Task 5는 1회 전체 스파인 fragment 재빌드 유발(빌드 1회 비용, 정합성 무영향).
