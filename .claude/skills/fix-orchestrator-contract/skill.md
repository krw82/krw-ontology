---
name: fix-orchestrator-contract
description: "Orchestrator checkpoint resume 수정, --period 옵션 수정, Edge ID 계약 불일치(dev_spec/init_workspace) 정렬. orchestrator 수정, checkpoint 수정, period 옵션, 계약 정렬 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix Orchestrator & Contract — Skill

3개 P2 버그를 수정한다: checkpoint resume, --period 옵션, Edge ID 계약 정렬.

## Bug 1: Checkpoint resume

**파일:** `src/krw_ontology/pipeline/orchestrator.py`

### 문제

`run_pipeline()`에서 `checkpoint_path`를 line 91에서 `None`으로 초기화한다. `_execute_stage("discover_source_document")` 내부에서 `ctx["checkpoint_path"]`를 설정하지만, 이 값이 외부 루프의 local `checkpoint_path` 변수에 할당되지 않는다.

결과: line 95의 skip 체크와 line 108의 mark_complete가 항상 `None`이므로 실행되지 않는다.

### 수정 방법

`_execute_stage()` 호출 직후, `ctx`에서 `checkpoint_path`를 동기화:

```python
for stage in PIPELINE_STAGES:
    # Checkpoint skip check
    if checkpoint_path and _is_code_stage(stage):
        checkpoint = CheckpointManager(checkpoint_path)
        if not force and checkpoint.is_stage_complete(stage):
            logger.info("Skipping completed stage: %s", stage, ...)
            continue

    _execute_stage(stage, ctx, config, base_dir, ticker, force=force)

    # Sync checkpoint_path from ctx after discover_source_document sets it
    checkpoint_path = ctx.get("checkpoint_path")

    if checkpoint_path:
        checkpoint = CheckpointManager(checkpoint_path)
        checkpoint.mark_complete(stage)
```

핵심: `_execute_stage()` 호출 직후에 `checkpoint_path = ctx.get("checkpoint_path")` 한 줄 추가.

## Bug 2: --period 옵션 무시

**파일:** `src/krw_ontology/pipeline/orchestrator.py`

### 문제

`run_pipeline()`이 `period` 파라미터를 받지만 ctx에 저장하지 않는다. `_execute_stage()`에서는 항상 `report_date`에서 period를 재계산한다.

### 수정 방법

**2-1. ctx에 period 저장**

```python
ctx: dict = {
    "ticker": ticker,
    "document_type": document_type,
    "doc_type_key": doc_type_key,
    "config": config,
    "force": force,
    "latest": latest,
    "base_dir": base_dir,
    "period": period,  # ADD THIS LINE
}
```

**2-2. _execute_stage()에서 ctx period 우선 사용**

현재 코드 (대략 line 141-146):
```python
# Derive period from report_date
if ctx.get("report_date"):
    year = ctx["report_date"][:4]
    ctx["period"] = f"FY{year}"
else:
    ctx["period"] = "FY" + ctx["filing_date"][:4]
```

수정:
```python
# Derive period: prefer explicit override, then report_date, then filing_date
if ctx.get("period") is not None:
    pass  # Use explicit period from CLI
elif ctx.get("report_date"):
    year = ctx["report_date"][:4]
    ctx["period"] = f"FY{year}"
elif ctx.get("filing_date"):
    ctx["period"] = "FY" + ctx["filing_date"][:4]
```

**주의:** `ctx["period"]`가 `None`일 수 있으므로 `is not None` 체크 사용. 빈 문자열도 체크하려면:
```python
if ctx.get("period"):
    pass  # truthy check: handles None and ""
```

## Bug 3: Edge ID 계약 정렬

### 문제

세 source가 서로 다른 Edge ID 패턴을 기술:
1. `ontology/schema/objects.yaml` line 170: `edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}` ← 구현과 일치
2. `dev_spec_v1.md` line 572: `edge:{relation_id}:{hash10}` ← 구현과 다름
3. `init_workspace.py` line 180: `edge:{relation_id}:{hash10}` ← 구현과 다름

### 수정 방법

구현이 더 구체적이므로, 문서/템플릿을 구현에 맞게 업데이트.

**3-1. `dev_spec_v1.md` 수정**

line 572 근처에서 Edge id_pattern을 찾아 변경:
```
# Before
id_pattern: "edge:{relation_id}:{hash10}"
# After
id_pattern: "edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}"
```

정확한 위치는 `dev_spec_v1.md`에서 `edge:` 문자열을 검색해서 찾을 것. Edge 객체 정의 섹션에 있음.

**3-2. `src/krw_ontology/cli/init_workspace.py` 수정**

line 180 근처에서 Edge id_pattern 템플릿을 찾아 변경. 이 파일은 사용자 workspace에 복사되는 YAML 템플릿을 포함하므로 정확히 일치해야 함.

```python
# Before (template string)
"id_pattern: \"edge:{relation_id}:{hash10}\""
# After
"id_pattern: \"edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}\""
```

정확한 문자열은 파일을 읽어서 확인. `init_workspace.py`에서 Edge 섹션의 `id_pattern`을 검색.

## 검증

1. 기존 129개 테스트 모두 통과
2. checkpoint resume 동작 확인 (테스트 또는 수동 검증)
3. `uv run ruff check .` — 0 에러

## 완료 조건

- [ ] `checkpoint_path = ctx.get("checkpoint_path")` 추가
- [ ] ctx에 `"period": period` 저장
- [ ] `_execute_stage()`에서 ctx period 우선 사용
- [ ] `dev_spec_v1.md` Edge ID 패턴 업데이트
- [ ] `init_workspace.py` Edge ID 패턴 업데이트
- [ ] 기존 테스트 모두 통과
- [ ] ruff lint 0 에러
