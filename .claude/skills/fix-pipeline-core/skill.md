---
name: fix-pipeline-core
description: "파이프라인 핵심 버그 4개(P1) 수정: extract_sections ATX heading 처리, orchestrator AI/validation 스테이지 연결, numeric_guard lookup 수정, generate_edges 경로 계산 수정. 파이프라인 수정, 버그 수정, 코드 수정 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix Pipeline Core — Skill

파이프라인 핵심 경로가 실제로 동작하도록 4개 P1 버그를 수정한다.

## Bug 1: extract_sections ATX heading 처리

**파일:** `src/krw_ontology/pipeline/stages/extract_sections.py`

**문제:** `clean_to_markdown()`이 markdownify로 `### Item 1. Business` 같은 ATX heading을 만들지만, `extract_sections()`는 `stripped`에서 `###`를 제거하지 않고 `SECTION_PATTERNS` regex(`^item`)을 바로 적용. 결과: `sections []`.

**SECTION_PATTERNS** (`config/constants.py`):
```python
"item1": r"(?i)^item\s+1\.\s|^business\b",
"item1a": r"(?i)^item\s+1A\.\s|^risk\s+factor",
```

**수정 방법:** `extract_sections.py`의 `stripped` 처리 전에 ATX heading 마커(`^#{1,6}\s*`)를 제거:

```python
stripped = line.strip()
# Remove ATX heading markers for matching
heading_text = re.sub(r'^#{1,6}\s*', '', stripped)
if not heading_text:
    continue
for section_name, pattern in SECTION_PATTERNS.items():
    if re.match(pattern, heading_text):
        matches.append((line_idx, section_name, heading_text))
        break
```

**주의:** `matches`에 저장하는 세 번째 요소도 `heading_text`로 변경 (원래 `stripped`였음).

## Bug 2: orchestrator AI/validation 스테이지 연결

**파일:** `src/krw_ontology/pipeline/orchestrator.py`

**문제:** `_execute_stage()`에서 AI extraction 5개 스테이지와 validate_ontology가 로그만 찍고 건너뜀.

**수정 방법:**

### 2-1. 최상단 import 추가

```python
import asyncio
from krw_ontology.pipeline.stages.extract_evidence_quotes import extract_evidence_quotes
from krw_ontology.pipeline.stages.extract_research_claims import extract_research_claims
from krw_ontology.pipeline.stages.extract_risks_drivers_headwinds import extract_risks_drivers_headwinds
from krw_ontology.pipeline.stages.extract_assumption_candidates import extract_assumption_candidates
from krw_ontology.pipeline.stages.generate_edges import generate_edges
from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology
from krw_ontology.extraction.worker import ExtractionWorker
```

### 2-2. `_execute_stage()`의 elif 블록 수정

```python
elif stage == "extract_evidence_quotes":
    worker = ExtractionWorker(
        model=config.model,
        cwd=base_dir,
    )
    quotes = asyncio.run(extract_evidence_quotes(
        worker=worker,
        ontology_dir=ctx["ontology_dir"],
        ticker=ticker,
        period=ctx["period"],
        doc_type=ctx["document_type"],
    ))
    ctx["quotes"] = quotes

elif stage == "extract_research_claims":
    worker = ExtractionWorker(
        model=config.model,
        cwd=base_dir,
    )
    claims = asyncio.run(extract_research_claims(
        worker=worker,
        ontology_dir=ctx["ontology_dir"],
        ticker=ticker,
        period=ctx["period"],
        doc_type=ctx["document_type"],
        quotes=ctx.get("quotes"),
    ))
    ctx["claims"] = claims

elif stage == "extract_risks_drivers_headwinds":
    worker = ExtractionWorker(
        model=config.model,
        cwd=base_dir,
    )
    result = asyncio.run(extract_risks_drivers_headwinds(
        worker=worker,
        ontology_dir=ctx["ontology_dir"],
        ticker=ticker,
        period=ctx["period"],
        doc_type=ctx["document_type"],
        claims=ctx.get("claims"),
        quotes=ctx.get("quotes"),
    ))
    ctx["risks"] = result.get("risks", [])
    ctx["growth_drivers"] = result.get("growth_drivers", [])
    ctx["headwinds"] = result.get("headwinds", [])

elif stage == "extract_assumption_candidates":
    worker = ExtractionWorker(
        model=config.model,
        cwd=base_dir,
    )
    assumptions = asyncio.run(extract_assumption_candidates(
        worker=worker,
        ontology_dir=ctx["ontology_dir"],
        ticker=ticker,
        period=ctx["period"],
        doc_type=ctx["document_type"],
        claims=ctx.get("claims"),
        quotes=ctx.get("quotes"),
    ))
    ctx["assumptions"] = assumptions

elif stage == "generate_edges":
    worker = ExtractionWorker(
        model=config.model,
        cwd=base_dir,
    )
    edges = asyncio.run(generate_edges(
        worker=worker,
        ontology_dir=ctx["ontology_dir"],
        ticker=ticker,
        period=ctx["period"],
        doc_type=ctx["document_type"],
        risks=ctx.get("risks"),
        growth_drivers=ctx.get("growth_drivers"),
        headwinds=ctx.get("headwinds"),
        assumptions=ctx.get("assumptions"),
        claims=ctx.get("claims"),
        quotes=ctx.get("quotes"),
    ))
    ctx["edges"] = edges

elif stage == "validate_ontology":
    result = run_validate_ontology(ctx["ontology_dir"])
    ctx["validation_result"] = result
```

### 2-3. `_is_code_stage()` 업데이트

모든 스테이지를 코드 스테이지로 표시 (AI 스테이지도 checkpoint로 건너뛸 수 있게):

```python
def _is_code_stage(stage: str) -> bool:
    return stage in {
        "resolve_ticker", "discover_source_document", "download_source_document",
        "clean_to_markdown", "extract_sections", "build_source_spans",
        "extract_xbrl_facts", "extract_evidence_quotes", "extract_research_claims",
        "extract_risks_drivers_headwinds", "extract_assumption_candidates",
        "generate_edges", "validate_ontology", "build_indexes", "build_graph_report",
    }
```

또는 더 간단히: `return True` (모든 스테이지에 checkpoint 적용).

## Bug 3: numeric_guard lookup 수정

**파일 1:** `src/krw_ontology/pipeline/stages/validate_ontology.py` (호출자)

**문제:** `all_lookup = dict(accepted)`를 만들지만 `validate_numeric(obj, quotes, xbrl_facts)`에 `quotes`만 전달. `numeric_guard` 내부에서 claim ID로 lookup하면 claim을 못 찾음.

**수정:** `validate_numeric(obj, all_lookup, xbrl_facts)`로 변경.

```python
# Stage 6: Numeric guard
quotes = _get_quotes(accepted)
xbrl_facts = _get_xbrl_facts(accepted)
all_lookup = dict(accepted)  # Full lookup including claims
failed_ids = set()
for obj_id, obj in list(accepted.items()):
    is_valid, reason = validate_numeric(obj, all_lookup, xbrl_facts)
```

**파일 2:** `src/krw_ontology/validators/numeric_guard.py` — 수정 불필요. `all_quotes` 파라미터가 이미 범용 dict으로 사용 중. 호출자에서 full dict을 전달하면 정상 동작.

## Bug 4: generate_edges 경로 계산 수정

**파일:** `src/krw_ontology/pipeline/stages/generate_edges.py`

**문제:** `ontology_dir`이 `companies/{ticker}/ontology/10K/FY2025`일 때:
- `ontology_dir.parent.parent.parent` = `companies/{ticker}` (workspace root가 아님)
- 동일한 패턴이 `extract_research_claims.py`, `extract_risks_drivers_headwinds.py`, `extract_assumption_candidates.py`에도 있음

**수정:** `pyproject.toml`을 찾아 프로젝트 루트를 결정하는 유틸리티 함수 작성 후 모든 `_load_*` 함수에서 사용.

`src/krw_ontology/utils/io.py`에 추가:

```python
def find_project_root(start: Path | None = None) -> Path:
    """Find project root by walking up to find pyproject.toml."""
    current = start or Path.cwd()
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()
```

그 다음 각 파일의 `_load_*` 함수에서:
```python
from krw_ontology.utils.io import find_project_root
project_root = find_project_root(ontology_dir)
```

**적용 파일:**
- `src/krw_ontology/pipeline/stages/generate_edges.py` — `_load_relations_whitelist()`, `_load_metric_ids()`
- `src/krw_ontology/pipeline/stages/extract_research_claims.py` — `_load_metrics_list()`
- `src/krw_ontology/pipeline/stages/extract_risks_drivers_headwinds.py` — `_load_metrics_list()`, `_load_risk_categories()`
- `src/krw_ontology/pipeline/stages/extract_assumption_candidates.py` — `_load_metrics_list()`

## 완료 조건

- [ ] `extract_sections`가 `### Item 1. Business` 형태의 heading을 정상 감지
- [ ] `orchestrator._execute_stage()`가 AI 스테이지를 실제 호출
- [ ] `validate_ontology`가 full dict을 `validate_numeric`에 전달
- [ ] `_load_*` 함수들이 pyproject.toml 기반으로 올바른 경로 계산
- [ ] 기존 단위 테스트가 여전히 통과
