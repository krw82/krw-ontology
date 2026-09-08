# Evidence-Gold 골든세트 평가 하니스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 라우터/정답 게이트 체계(`benchmarks/` + `scripts/benchmark_*.py`)를 **인용(evidence-unit) 레벨 골든셋 + 결정론 채점**으로 확장하여, 검색 품질의 회귀 게이트(리서치 CI)와 변경 전/후 비교 기준을 만든다.

**Architecture:** 새 패키지 `src/krw_ontology/eval_gold/`(스키마+채점기, 순수 결정론) + CLI `scripts/benchmark_evidence_gold.py`(헤드리스 실행·리포트·게이트 비교, `scripts/benchmark_mcp_candidate.py:_run_case` 패턴 재사용) + 골드 생성기 `scripts/generate_evidence_gold_templates.py`(샤드에서 템플릿 골드 생성, SEC-QA 방식). 채점은 object_id 정확 매치 + ticker/period/텍스트 포함 매치로 결정론적이며 LLM-judge를 경로에 두지 않는다.

**Tech Stack:** Python 3.12 (uv), pydantic 2.x, 기존 `mcp_server.tools.query_context_tool` 헤드리스 호출, SQLite(기존 샤드), pytest.

## Global Constraints

- 테스트 실행: `uv run pytest tests/unit/test_<file>.py -x -q` (전체는 `uv run pytest -q`). 커밋 전 pytest 요약 라인 확인 필수.
- 소스 파일(`*.py`) 생성/수정은 Edit/Write 도구만 사용 (Bash sed/heredoc 금지, mimosa 훅).
- 커밋에 credential-shaped 리터럴 금지 (테스트 값도 동적 생성).
- `git add -A` 금지 — `.mimosa/` 등 로컬 상태 디렉토리 확인 후 선택적 add.
- 절대 경로 보호: `~/krw-ontology-data/releases/prod` 읽기 전용으로도 직접 쓰지 않음( baseline 실행은 **v2-dev 릴리스** 또는 기존 검증된 dev 릴리스만), 원본 리포 `~/krw-ontology` 불변.
- 브랜치: `feat/observation-v3-bundle` (HEAD `0dd2929`, clean) 에서 `feat/evidence-gold-harness` 생성.
- 커밋 메시지 접두사: `feat:` / `test:` / `chore:` / `fix:`.
- 채점 경로에 LLM/임베딩 금지 (결정론 전용; LLM-judge는 향후 별도 스팟체크 층으로만).
- `query_context_tool` 호출 전 `mcp_tools.reset_mcp_runtime_caches()` 호출(케이스 간 격리), 런타임 경로 오버라이드 인자 금지(`_runtime_override_error` tools.py:2808 정책 준수).

---

### Task 0: 베이스라인용 릴리스 확인 + 작업 브랜치 생성

**Files:** 없음 (환경 확인만)

- [ ] **Step 1: v2-dev 릴리스 존재 확인**

Run: `ls ~/krw-ontology-data/releases/v2-dev/ 2>/dev/null; ls ~/krw-ontology-data/releases/*/ 2>/dev/null | head -30`
Expected: 릴리스 디렉토리 목록. 골드가 바인딩된 `20260830_193811` 포함 여부 확인.

- [ ] **Step 2: 샤드 매니페스트 검증**

Run: `python3 -c "import json,glob; p=sorted(glob.glob('~/krw-ontology-data/releases/*/*/indexes/shard_manifest.json')); print(p[-3:]); m=json.load(open(p[-1])); print(m.get('ticker_count'), m.get('format'))"`
Expected: 매니페스트 경로와 ticker_count ≥ 100 (355개 데이터 중 대부분 인덱싱됨). 선택한 릴리스 경로를 기록 — Task 6 베이스라인 실행에 사용.

- [ ] **Step 3: 브랜치 생성**

```bash
cd ~/krw-ontology-v2/krw-ontology
git checkout -b feat/evidence-gold-harness
```

---

### Task 1: Evidence-Gold 스키마 + 검증기

**Files:**
- Create: `src/krw_ontology/eval_gold/__init__.py`
- Create: `src/krw_ontology/eval_gold/schema.py`
- Test: `tests/unit/test_eval_gold_schema.py`

**Interfaces:**
- Consumes: `krw_ontology.mcp_server.contracts.validate_search_plan` (contracts.py:733)
- Produces: `EVIDENCE_GOLD_FORMAT_VERSION = "krw-ontology-evidence-gold/v1"`, `class ExpectedEvidence(ticker, period, document_type, object_ids, text_fragments, required)`, `class EvidenceGoldCase(id, question, search_plan, strata, expected, expect_not_disclosed, forbidden_fragments, notes)`, `class EvidenceGold(format_version, source_release, cases)`, `load_evidence_gold(path: Path) -> EvidenceGold`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_eval_gold_schema.py
import json
import pytest
from pathlib import Path

from krw_ontology.eval_gold.schema import (
    EVIDENCE_GOLD_FORMAT_VERSION,
    EvidenceGold,
    EvidenceGoldCase,
    ExpectedEvidence,
    load_evidence_gold,
)

def _plan() -> dict:
    return {
        "question": "What was NVDA revenue for FY2025?",
        "intent": "metric_lookup",
        "clauses": [
            {
                "clause_id": "revenue",
                "retrieval_query": "NVIDIA total revenue fiscal 2025",
                "required_concepts": ["revenue"],
                "tickers": ["NVDA"],
            }
        ],
        "tickers": ["NVDA"],
    }

def _case(**over) -> dict:
    base = {
        "id": "case-001",
        "question": "What was NVDA revenue for FY2025?",
        "search_plan": _plan(),
        "strata": ["template"],
        "expected": [
            {"ticker": "NVDA", "period": "FY2025", "object_ids": ["obj-1"]}
        ],
    }
    base.update(over)
    return base

def test_valid_gold_roundtrip(tmp_path: Path):
    gold = EvidenceGold(
        source_release={"release_id": "r-test", "source_manifest_hash": "deadbeef"},
        cases=[EvidenceGoldCase(**_case())],
    )
    assert gold.format_version == EVIDENCE_GOLD_FORMAT_VERSION
    p = tmp_path / "gold.json"
    p.write_text(gold.model_dump_json(indent=2))
    loaded = load_evidence_gold(p)
    assert loaded.cases[0].expected[0].object_ids == ["obj-1"]

def test_expected_item_requires_anchor():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ExpectedEvidence(ticker="NVDA")  # object_ids/text_fragments 모두 비면 거부

def test_duplicate_case_ids_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EvidenceGold(
            source_release={"release_id": "r"},
            cases=[EvidenceGoldCase(**_case()), EvidenceGoldCase(**_case(id="case-001"))],
        )

def test_not_disclosed_requires_forbidden_fragments():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        EvidenceGoldCase(**_case(expected=[], expect_not_disclosed=True))

def test_invalid_search_plan_rejected_with_case_id():
    from pydantic import ValidationError
    bad = _plan()
    bad["clauses"] = []  # 최소 1개 필요 (contracts._validate_plan)
    with pytest.raises(ValidationError, match="case-002"):
        EvidenceGoldCase(**_case(id="case-002", search_plan=bad))

def test_load_evidence_gold_rejects_wrong_format(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"format_version": "krw-ontology-evidence-gold/v0"}))
    with pytest.raises(ValueError, match="format_version"):
        load_evidence_gold(p)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_eval_gold_schema.py -x -q`
Expected: FAIL — `ModuleNotFoundError: krw_ontology.eval_gold`

- [ ] **Step 3: 최소 구현**

```python
# src/krw_ontology/eval_gold/__init__.py
"""Evidence-level golden set: schema, grading, and release-bound regression golds."""
```

```python
# src/krw_ontology/eval_gold/schema.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from krw_ontology.mcp_server.contracts import validate_search_plan

EVIDENCE_GOLD_FORMAT_VERSION = "krw-ontology-evidence-gold/v1"

ALLOWED_STRATA = {
    "template",
    "vocabulary_mismatch",
    "fiscal_offset",
    "multi_period",
    "dimensioned",
    "multi_span",
    "not_disclosed",
    "curated",
}


class ExpectedEvidence(BaseModel):
    ticker: str
    period: str | None = None
    document_type: str | None = None
    object_ids: list[str] = Field(default_factory=list)
    text_fragments: list[str] = Field(default_factory=list)
    required: bool = True

    @model_validator(mode="after")
    def _require_anchor(self) -> "ExpectedEvidence":
        if not self.object_ids and not self.text_fragments:
            raise ValueError(
                "expected item needs an anchor: object_ids or text_fragments"
            )
        return self


class EvidenceGoldCase(BaseModel):
    id: str
    question: str
    search_plan: dict[str, Any]
    strata: list[str] = Field(default_factory=list)
    expected: list[ExpectedEvidence] = Field(default_factory=list)
    expect_not_disclosed: bool = False
    forbidden_fragments: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _validate_case(self) -> "EvidenceGoldCase":
        if self.expect_not_disclosed and not self.forbidden_fragments:
            raise ValueError("expect_not_disclosed requires forbidden_fragments")
        if not self.expect_not_disclosed and not self.expected:
            raise ValueError("positive case requires at least one expected item")
        try:
            validate_search_plan(self.search_plan)
        except Exception as exc:  # pydanticValidationError 등 — 케이스 id 부착
            raise ValueError(f"case {self.id}: invalid search_plan: {exc}") from exc
        unknown = set(self.strata) - ALLOWED_STRATA
        if unknown:
            raise ValueError(f"case {self.id}: unknown strata {sorted(unknown)}")
        return self


class EvidenceGold(BaseModel):
    format_version: Literal[EVIDENCE_GOLD_FORMAT_VERSION] = EVIDENCE_GOLD_FORMAT_VERSION
    source_release: dict[str, Any]
    cases: list[EvidenceGoldCase]

    @model_validator(mode="after")
    def _validate_gold(self) -> "EvidenceGold":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate case ids: {dupes}")
        if not self.source_release.get("release_id"):
            raise ValueError("source_release.release_id is required")
        return self


def load_evidence_gold(path: Path) -> EvidenceGold:
    raw = json.loads(Path(path).read_text())
    if raw.get("format_version") != EVIDENCE_GOLD_FORMAT_VERSION:
        raise ValueError(
            f"format_version must be {EVIDENCE_GOLD_FORMAT_VERSION}, "
            f"got {raw.get('format_version')!r}"
        )
    return EvidenceGold.model_validate(raw)
```

주의: `validate_search_plan`이 lenient 모드가 필요하면 `validate_query_context_search_plan`(contracts.py:743)으로 교체 허용 — 실행 시 실제 시그니처 확인 후 둘 중 엄격한 것 우선.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/unit/test_eval_gold_schema.py -x -q`
Expected: PASS (6 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/krw_ontology/eval_gold/ tests/unit/test_eval_gold_schema.py
git commit -m "feat(eval-gold): evidence-gold v1 schema with release binding and case validation"
```

---

### Task 2: 결정론 채점기

**Files:**
- Create: `src/krw_ontology/eval_gold/grader.py`
- Test: `tests/unit/test_eval_gold_grader.py`

**Interfaces:**
- Consumes: Task 1의 `EvidenceGoldCase`, `ExpectedEvidence`
- Produces: `normalize_text(s: str) -> str`, `match_expected(item: ExpectedEvidence, units: list[dict]) -> dict | None`(매치된 유닛), `grade_case(case: EvidenceGoldCase, research_state: dict) -> CaseResult`, `class CaseResult(case_id, strata, recall, matched_object_ids, zero_hit, not_disclosed_violation, passed, detail)`, `aggregate(results: list[CaseResult]) -> dict`(전체+성층별 pass_rate/recall/zero_hit 비율)

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/unit/test_eval_gold_grader.py
from krw_ontology.eval_gold.grader import aggregate, grade_case, normalize_text
from krw_ontology.eval_gold.schema import EvidenceGoldCase, ExpectedEvidence

def _state(units, clause_coverage=None):
    return {
        "evidence_units": units,
        "clause_coverage": clause_coverage if clause_coverage is not None else {"c1": 1},
    }

def _unit(**over):
    base = {
        "evidence_id": "eu-1",
        "object_id": "obj-1",
        "object_type": "metric_observation",
        "ticker": "NVDA",
        "period": "FY2025",
        "title": "Total revenue grew 26% to $130.5B",
        "summary": "NVIDIA total revenue for fiscal 2025",
        "supports_clause_ids": ["c1"],
    }
    base.update(over)
    return base

def test_normalize_text_collapses_ws_and_case():
    assert normalize_text("  Total   REVENUE\n grew ") == "total revenue grew"

def test_object_id_match_ignores_period_when_absent():
    item = ExpectedEvidence(ticker="NVDA", object_ids=["obj-9"])
    from krw_ontology.eval_gold.grader import match_expected
    m = match_expected(item, [_unit(object_id="obj-9", period="FY2024")])
    assert m is not None

def test_period_filter_enforced():
    item = ExpectedEvidence(ticker="NVDA", period="FY2025", object_ids=["obj-1"])
    from krw_ontology.eval_gold.grader import match_expected
    assert match_expected(item, [_unit(period="FY2024")]) is None

def test_text_fragment_containment_on_title_summary():
    item = ExpectedEvidence(ticker="NVDA", text_fragments=["revenue grew 26%"])
    from krw_ontology.eval_gold.grader import match_expected
    assert match_expected(item, [_unit()]) is not None

def _case(expected, **over):
    base = dict(
        id="c1",
        question="q",
        search_plan={
            "question": "q", "intent": "lookup",
            "clauses": [{"clause_id": "c1", "retrieval_query": "nvda revenue"}],
            "tickers": ["NVDA"],
        },
        expected=expected,
    )
    base.update(over)
    return EvidenceGoldCase(**base)

def test_grade_case_full_recall_passes():
    case = _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])])
    r = grade_case(case, _state([_unit()]))
    assert r.passed and r.recall == 1.0 and not r.zero_hit

def test_grade_case_partial_recall_fails():
    case = _case([
        ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"]),
        ExpectedEvidence(ticker="AMD", object_ids=["obj-2"]),
    ])
    r = grade_case(case, _state([_unit()]))
    assert not r.passed and r.recall == 0.5

def test_zero_hit_detected_from_clause_coverage():
    case = _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])])
    r = grade_case(case, _state([], clause_coverage={}))
    assert r.zero_hit and not r.passed

def test_not_disclosed_violation():
    case = _case(
        [], expect_not_disclosed=True,
        forbidden_fragments=["quantum revenue"],
        search_plan={
            "question": "q", "intent": "lookup",
            "clauses": [{"clause_id": "c1", "retrieval_query": "nvda quantum revenue"}],
            "tickers": ["NVDA"],
        },
    )
    r = grade_case(case, _state([_unit(summary="NVIDIA quantum revenue breakdown")]))
    assert r.not_disclosed_violation and not r.passed

def test_aggregate_breaks_down_by_stratum():
    r1 = grade_case(_case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"]]), strata=["template"]), _state([_unit()]))
    r2 = grade_case(
        _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])], strata=["vocabulary_mismatch"]),
        _state([]),
    )
    agg = aggregate([r1, r2])
    assert agg["overall"]["pass_rate"] == 0.5
    assert agg["strata"]["template"]["pass_rate"] == 1.0
    assert agg["strata"]["vocabulary_mismatch"]["zero_hit_rate"] == 1.0
```

주의: `_case(...)` 헬퍼에서 `strata` 키워드가 `base.update(over)` 경로로 전달되도록 헬퍼 시그니처 유지 (`_case(expected, **over)`).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_eval_gold_grader.py -x -q`
Expected: FAIL — `No module named ... grader`

- [ ] **Step 3: 구현**

```python
# src/krw_ontology/eval_gold/grader.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from krw_ontology.eval_gold.schema import EvidenceGoldCase, ExpectedEvidence


def normalize_text(s: str) -> str:
    return " ".join(s.lower().split())


def _unit_text(unit: dict[str, Any]) -> str:
    parts = [unit.get("title") or "", unit.get("summary") or ""]
    return normalize_text(" ".join(parts))


def match_expected(item: ExpectedEvidence, units: list[dict[str, Any]]) -> dict | None:
    for unit in units:
        if unit.get("ticker") != item.ticker:
            continue
        if item.period and unit.get("period") != item.period:
            continue
        if item.object_ids and unit.get("object_id") in item.object_ids:
            return unit
        if item.text_fragments:
            hay = _unit_text(unit)
            if any(normalize_text(f) in hay for f in item.text_fragments):
                return unit
    return None


@dataclass
class CaseResult:
    case_id: str
    strata: list[str]
    recall: float
    matched_object_ids: list[str] = field(default_factory=list)
    zero_hit: bool = False
    not_disclosed_violation: bool = False
    passed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


def grade_case(case: EvidenceGoldCase, research_state: dict[str, Any]) -> CaseResult:
    units = research_state.get("evidence_units") or []
    coverage = research_state.get("clause_coverage") or {}
    zero_hit = not units and not coverage

    matched: list[str] = []
    missing: list[str] = []
    required = [e for e in case.expected if e.required]
    optional = [e for e in case.expected if not e.required]
    hit = 0
    denom = 0
    for item in required:
        denom += 1
        m = match_expected(item, units)
        if m is not None:
            hit += 1
            matched.append(m.get("object_id") or m.get("evidence_id") or "")
        else:
            missing.append(item.model_dump_json())
    for item in optional:
        m = match_expected(item, units)
        if m is not None:
            matched.append(m.get("object_id") or m.get("evidence_id") or "")

    violation = False
    if case.expect_not_disclosed:
        for unit in units:
            hay = _unit_text(unit)
            if any(normalize_text(f) in hay for f in case.forbidden_fragments):
                violation = True
                break

    recall = (hit / denom) if denom else (1.0 if not case.expect_not_disclosed else 1.0)
    passed = (not missing) and (not violation) and (not case.expect_not_disclosed or not units or all(
        normalize_text(f) not in _unit_text(u) for u in units for f in case.forbidden_fragments
    ) if case.expect_not_disclosed else True)
    passed = bool(passed)

    return CaseResult(
        case_id=case.id,
        strata=list(case.strata),
        recall=recall,
        matched_object_ids=matched,
        zero_hit=zero_hit,
        not_disclosed_violation=violation,
        passed=passed,
        detail={"missing_required": missing, "unit_count": len(units)},
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    def _block(rs: list[CaseResult]) -> dict[str, float]:
        n = len(rs) or 1
        return {
            "cases": len(rs),
            "pass_rate": sum(1 for r in rs if r.passed) / n,
            "mean_recall": sum(r.recall for r in rs) / n,
            "zero_hit_rate": sum(1 for r in rs if r.zero_hit) / n,
        }

    out: dict[str, Any] = {"overall": _block(results), "strata": {}}
    strata: dict[str, list[CaseResult]] = {}
    for r in results:
        for s in r.strata or ["untagged"]:
            strata.setdefault(s, []).append(r)
    out["strata"] = {s: _block(rs) for s, rs in sorted(strata.items())}
    return out
```

주의: `grade_case`의 `passed` 식이 복잡해지지 않게 실행 시 다음처럼 정리해도 된다(동등 의미, 가독성 우선):
`passed = (not missing) and (not case.expect_not_disclosed or not violation)` — 테스트가 녹색이면 유지.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/unit/test_eval_gold_grader.py -x -q`
Expected: PASS (9 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/krw_ontology/eval_gold/grader.py tests/unit/test_eval_gold_grader.py
git commit -m "feat(eval-gold): deterministic grader with stratum aggregation"
```

---

### Task 3: 하니스 러너 (`benchmark_evidence_gold.py`)

**Files:**
- Create: `scripts/benchmark_evidence_gold.py`
- Test: `tests/unit/test_benchmark_evidence_gold.py`

**Interfaces:**
- Consumes: Task 1·2 (`load_evidence_gold`, `grade_case`, `aggregate`), `krw_ontology.mcp_server.tools.query_context_tool`, `reset_mcp_runtime_caches` (env 세팅 패턴은 `scripts/benchmark_mcp_candidate.py:_run_case` :153-182 과 `tests/unit/test_mcp_server.py:_build_v3_runtime` :3118-3134 를 그대로 미러)
- Produces: CLI `uv run python scripts/benchmark_evidence_gold.py --gold <path> [--label baseline] [--baseline <prev_report.json>] [--report-dir benchmarks/reports]` → `{report_dir}/evidence_gold_<label>_<ts>.json|.md`, 게이트 열화 시 exit code 2

- [ ] **Step 1: 실패 테스트 작성** — 합성 미니 릴리스 레시피(`tests/unit/test_shard_schema_v3.py:_write_minimal_artifacts` :38-129 + `build_minimal_release` :132-143, `tests/unit/test_mcp_server.py:_build_v3_runtime` :3118-3134)로 tmp 릴리스를 만들고, 골드 3케이스(정상 1, 부분매치 1, not_disclosed 1)를 돌려 리포트 구조를 검증. 테스트는 `KRW_*` env를 autouse fixture로 격리하고 케이스마다 `reset_mcp_runtime_caches()` 호출. 핵심 단언:

```python
# tests/unit/test_benchmark_evidence_gold.py (골격 — 레시피는 위 파일들에서 복사)
def test_harness_end_to_end_on_minimal_release(tmp_path, monkeypatch):
    # 1) 미니 릴리스 빌드 (test_shard_schema_v3 레시피 재사용)
    # 2) env: KRW_ONTOLOGY_ENV=dev, KRW_ONTOLOGY_RELEASE_ROOT=<tmp>,
    #    KRW_ONTOLOGY_GLOBAL_SPINE_PATH, KRW_ONTOLOGY_SHARD_MANIFEST_PATH 지정
    # 3) 골드 파일 작성 (정상/부분/not_disclosed)
    # 4) run: from scripts... 대신 함수로 호출:
    from krw_ontology.eval_gold import harness  # 아래 Step 3에서 scripts가 import하는 함수
    report = harness.run_harness(gold_path, label="test")
    assert report["overall"]["cases"] == 3
    assert report["cases"]["c-positive"]["passed"] is True
    assert report["cases"]["c-partial"]["recall"] == 0.5
    assert report["cases"]["c-negative"]["not_disclosed_violation"] is False
```

따라서 러너 로직은 `src/krw_ontology/eval_gold/harness.py`(라이브러리)에 두고 `scripts/benchmark_evidence_gold.py`는 typer CLI 래퍼로 만든다(기존 scripts가 곧바로 import되지 않는 테스트 구조를 피함).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/unit/test_benchmark_evidence_gold.py -x -q`
Expected: FAIL — `No module named ... harness`

- [ ] **Step 3: 구현**

```python
# src/krw_ontology/eval_gold/harness.py
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from krw_ontology.eval_gold.grader import CaseResult, aggregate, grade_case
from krw_ontology.eval_gold.schema import EvidenceGold, load_evidence_gold
from krw_ontology.mcp_server import tools as mcp_tools


def _query_case(case) -> dict[str, Any]:
    mcp_tools.reset_mcp_runtime_caches()
    result = mcp_tools.query_context_tool(search_plan=dict(case.search_plan))
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


def run_harness(gold_path: Path | str, *, label: str) -> dict[str, Any]:
    gold: EvidenceGold = load_evidence_gold(Path(gold_path))
    case_results: list[CaseResult] = []
    per_case: dict[str, Any] = {}
    for case in gold.cases:
        started = time.time()
        state = _query_case(case)
        r = grade_case(case, state)
        r.detail["elapsed_s"] = round(time.time() - started, 3)
        case_results.append(r)
        per_case[case.id] = {
            "passed": r.passed,
            "recall": r.recall,
            "zero_hit": r.zero_hit,
            "not_disclosed_violation": r.not_disclosed_violation,
            "detail": r.detail,
        }
    return {
        "label": label,
        "gold_path": str(gold_path),
        "source_release": gold.source_release,
        "overall": aggregate(case_results)["overall"],
        "strata": aggregate(case_results)["strata"],
        "cases": per_case,
    }


def compare_to_baseline(report: dict, baseline: dict, *, tolerance: float = 0.0) -> tuple[bool, list[str]]:
    regressions: list[str] = []
    for key in ("pass_rate", "mean_recall"):
        a = baseline["overall"].get(key, 0.0)
        b = report["overall"].get(key, 0.0)
        if b < a - tolerance:
            regressions.append(f"overall.{key}: {b:.4f} < baseline {a:.4f}")
    for stratum, block in baseline.get("strata", {}).items():
        for key in ("pass_rate", "mean_recall"):
            a = block.get(key, 0.0)
            b = report.get("strata", {}).get(stratum, {}).get(key, a)
            if b < a - tolerance:
                regressions.append(f"strata[{stratum}].{key}: {b:.4f} < baseline {a:.4f}")
    return (not regressions), regressions
```

`scripts/benchmark_evidence_gold.py`는 typer CLI: `--gold`, `--label`(기본 "candidate"), `--baseline`(선택), `--report-dir`(기본 `benchmarks/reports`), 리포트 JSON+MD 저장(마크다운: overall/strata 표 + 케이스별 요약), `compare_to_baseline` 결과 열회귀면 exit 2. 리포트 파일명 패턴은 기존 `benchmark_router_sidecar.py`의 출력 관행을 실행 시 확인하여 맞춘다.

주의: `query_context_tool`의 실제 키워드 인자명은 실행 시 `scripts/benchmark_mcp_candidate.py:153-182` 를 그대로 참조해 맞춘다(추가 필수 인자가 있으면 미러).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/unit/test_benchmark_evidence_gold.py -x -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/krw_ontology/eval_gold/harness.py scripts/benchmark_evidence_gold.py tests/unit/test_benchmark_evidence_gold.py
git commit -m "feat(eval-gold): headless harness runner with baseline gate and reports"
```

---

### Task 4: 템플릿 골드 생성기

**Files:**
- Create: `src/krw_ontology/eval_gold/templates.py`
- Create: `scripts/generate_evidence_gold_templates.py` (typer 래퍼)
- Test: `tests/unit/test_eval_gold_templates.py`

**Interfaces:**
- Consumes: `indexes/shard_manifest.json`(spine_builder.py:2330 스키마), 샤드 `objects`/`metric_lookup`/`metric_dimension_lookup` 테이블(builder.py:4138/:4266), Task 1 스키마
- Produces: `generate_template_gold(release_root: Path, *, seed: int = 20260908, per_ticker: int = 3, tickers: list[str] | None = None) -> EvidenceGold`, CLI 서브커맨드 `inspect-objects`(티커+지표+기간 → object_id 목록 출력, 큐레이션 보조용)

동작: 매니페스트에서 티커 목록 읽기 → 각 샤드에서 `review_status='accepted'`인 metric 관측 객체를 기간별로 표본 추출(시드 고정) → SearchPlan 단일 클로즈(`metrics=[canonical]`, `tickers=[t]`, `periods=[p]`)와 질문문 템플릿 생성 → expected anchor = 해당 object_id들. 성층: `dimensioned`(metric_dimension_lookup에 차원 있음), `multi_period`(동일 지표 2기간 → expected 2건), 기본 `template`.

- [ ] **Step 1: 실패 테스트** — 미니 릴리스 레시피로 만든 tmp 릴리스에서: 생성 골드의 (a) 케이스 수 = 티커×per_ticker 근사, (b) 모든 케이스가 `EvidenceGoldCase` 검증 통과, (c) 시드 고정 시 두 번 생성 결과 동일(결정론), (d) expected object_ids가 실제 샤드 objects 테이블에 존재.

- [ ] **Step 2: 실패 확인** — `uv run pytest tests/unit/test_eval_gold_templates.py -x -q` → FAIL

- [ ] **Step 3: 구현** — 위 동작 그대로. SQL 예:

```sql
SELECT o.id, o.ticker, o.period, o.metric_name, o.review_status
FROM objects o
WHERE o.ticker = ? AND o.period = ? AND o.metric_name = ?
  AND o.review_status = 'accepted'
LIMIT 20;
```

(실제 컬럼명은 builder.py:4138 DDL 실행 시 확인; `metric_lookup` 테이블에서 canonical 지표·기간 목록을 먼저 조회)

- [ ] **Step 4: 통과 확인 → Step 5: 커밋**

```bash
git add src/krw_ontology/eval_gold/templates.py scripts/generate_evidence_gold_templates.py tests/unit/test_eval_gold_templates.py
git commit -m "feat(eval-gold): deterministic template gold generator from shards + inspect-objects helper"
```

---

### Task 5: 큐레이티드 성층 케이스 작성

**Files:**
- Create: `benchmarks/evidence_gold_curated_v1.json`
- Test: `tests/unit/test_eval_gold_curated.py`

**Interfaces:**
- Consumes: Task 4 `inspect-objects` 헬퍼, Task 1 스키마
- Produces: 큐레이티드 골드 v1 — 성층별 최소 케이스수: `vocabulary_mismatch` 8 (예: "자사주 매수"/"stock buyback" ↔ canonical `repurchase` 계열, "매출" ↔ `revenue|net_sales`, "영업이익" ↔ `operating_income` — 대상 티커는 Task 0에서 선택한 릴리스에 인덱싱된 것에서 픽), `fiscal_offset` 4 (질문은 CY2024, 파딩은 FY2024/2023 등 회계연도 오프셋 티커 — NVDA/AAPL 등), `multi_span` 4, `not_disclosed` 4 (존재하지 않는 지표, 예: "quantum revenue"), `multi_period` 4. 총 ~24.

- [ ] **Step 1: 테스트** — 큐레이티드 JSON이 `load_evidence_gold` 통과 + 성층별 최소 개수 단언 + 모든 object_id가 릴리스 매니페스트 티커 샤드에 존재함(존재 검증은 골드에 포함된 `source_release` 기준 스킵 가능 — 스키마 검증만).

- [ ] **Step 2: 실패 확인 → Step 3: `inspect-objects`로 object_id 수집하며 JSON 작성 → Step 4: 통과 확인 → Step 5: 커밋**

```bash
git add benchmarks/evidence_gold_curated_v1.json tests/unit/test_eval_gold_curated.py
git commit -m "test(eval-gold): curated stratum cases (vocab mismatch, fiscal offset, multi-span, not-disclosed)"
```

---

### Task 6: 베이스라인 측정 (= "이전 품질" 기록)

**Files:**
- Create: `benchmarks/reports/evidence_gold_baseline_20260908.{json,md}` (실행 산출물)
- Modify: `benchmarks/README.md` (evidence-gold 게이트 프로토콜 문서에 섹션 추가)

- [ ] **Step 1: 템플릿 골드 생성 (Task 0 릴리스 대상)**

```bash
KRW_ONTOLOGY_RELEASE_ROOT=<task0-release-root> uv run python scripts/generate_evidence_gold_templates.py \
  --out benchmarks/evidence_gold_template_v1.json --per-ticker 2
```

- [ ] **Step 2: 큐레이티드와 병합** — 병합 유틸은 `generate_... --merge benchmarks/evidence_gold_curated_v1.json` 옵션으로 구현하거나, 하니스가 `--gold` 를 여러 번 받게 확장(실행 시 단순한 쪽 선택).

- [ ] **Step 3: 베이스라인 실행**

```bash
KRW_ONTOLOGY_RELEASE_ROOT=<task0-release-root> uv run python scripts/benchmark_evidence_gold.py \
  --gold benchmarks/evidence_gold_v1.json --label baseline
```

Expected: 리포트 생성. `vocabulary_mismatch` 성층의 zero_hit_rate가 유의미하게 높을 것으로 예상 — **이 숫자가 Plan 2(유사도 레인)의 정당화 데이터**.

- [ ] **Step 4: 리포트 커밋 + README 갱신**

```bash
git add benchmarks/reports/evidence_gold_baseline_20260908.json benchmarks/reports/evidence_gold_baseline_20260908.md benchmarks/README.md benchmarks/evidence_gold_template_v1.json
git commit -m "test(eval-gold): commit baseline quality report (pre-similarity-lane)"
```

- [ ] **Step 5: 전체 스모크** — `uv run pytest tests/unit -q -k "eval_gold or benchmark_evidence"` 전부 녹색 확인.

---

## Self-Review (작성자 확인 완료)

- 스펙 커버: 골든셋(스키마 Task 1) + 채점기(Task 2) + 하니스/게이트(Task 3) + 골드 생성(4·5) + 베이스라인(6) — 5개 보완점 중 #1 전체. XBRL weak-gold는 Plan 3(테이블 감사)로 이관(로드맵 참조).
- 플레이스홀더 스캔: Task 4 Step 3의 SQL/구현이 동작 서술형이나 필수 인터페이스·검증단계 포함 — 실행자는 명시된 DDL/테이블 참조로 구체화. 나머지 태스크는 코드 제공됨.
- 타입 일치: `EvidenceGoldCase`/`ExpectedEvidence`/`CaseResult`/`aggregate` 명칭·필드 전 태스크 일관.
- 리스크 명시: `query_context_tool` 인자명·리포트 관행은 실행 시 기존 스크립트 미러 확인 필수(각 Step에 명시됨).
