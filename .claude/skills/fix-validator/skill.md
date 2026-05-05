---
name: fix-validator
description: "Metric edge가 reference_validator에서 rejected되지 않도록 validate_ontology.py를 수정. metric validation, reference validator, edge validation 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix Validator — Skill

Metric edge가 `reference_validator`에서 dangling reference로 rejected되지 않도록 validator 체인을 수정한다.

## 문제

`generate_edges()`는 `metric:revenue` 같은 Metric ID를 valid target으로 허용하지만, `validate_ontology()`는 metric dictionary를 객체 registry(`all_objects`)에 넣지 않는다. `reference_validator`는 `Edge.to_id`가 `all_objects`에 없으면 dangling으로 판단하여 rejected 처리한다.

## 수정 방법

### 수정 파일: `src/krw_ontology/pipeline/stages/validate_ontology.py`

**1-1. metric ID 로딩 헬퍼 함수 추가**

모듈 상단 import에 추가:
```python
import yaml
```

함수 정의 영역에 추가 (다른 `_get_*` 함수 근처):

```python
def _load_metric_ids(ontology_dir: Path) -> set[str]:
    """Load canonical metric IDs from metric_dictionary.yaml."""
    from krw_ontology.utils.io import find_project_root
    project_root = find_project_root(ontology_dir)
    metric_path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
    if not metric_path.exists():
        return set()
    with open(metric_path) as f:
        data = yaml.safe_load(f) or {}
    return {f"metric:{m['name']}" for m in data.get("metrics", [])}
```

`generate_edges.py`의 `_load_metric_ids`도 `generate_metric_id(m["name"])`을 사용하니, 만약 `generate_metric_id`가 `f"metric:{name}"` 형태면 동일. `generate_metric_id`가 존재하면 그것을 import해서 사용:

```python
def _load_metric_ids(ontology_dir: Path) -> set[str]:
    from krw_ontology.utils.io import find_project_root
    from krw_ontology.schema.id_utils import generate_metric_id
    project_root = find_project_root(ontology_dir)
    metric_path = project_root / "ontology" / "schema" / "metric_dictionary.yaml"
    if not metric_path.exists():
        return set()
    with open(metric_path) as f:
        data = yaml.safe_load(f) or {}
    return {generate_metric_id(m["name"]) for m in data.get("metrics", [])}
```

`generate_metric_id`가 `id_utils.py`에 있는지 먼저 확인하고, 있으면 사용. 없으면 `f"metric:{name}"` 직접 생성.

**1-2. `all_objects`에 metric ID 추가**

reference validation 단계(Schema → ExactMatch → Reference → ...)에서 Reference 단계 직전에 metric ID를 `all_objects`에 추가.

현재 코드 흐름 (대략):

```python
# Stage 1: Schema validation
# Stage 2: Exact match
# Stage 3: Reference validation ← 이 전에 metric 추가 필요
```

Reference validation 직전에:
```python
# Add virtual metric objects for edge reference validation
for mid in _load_metric_ids(ontology_dir):
    all_objects[mid] = {"id": mid, "type": "Metric"}
```

**주의:** `all_objects`가 어디에 정의되어 있는지 확인. `validate_ontology()` 함수 내에서 `accepted` dict을 기반으로 `all_objects`가 구성된다. accepted와 all_objects가 같은 dict이면 accepted에 추가.

정확한 위치는 코드를 읽고 판단. 핵심은 reference validation이 실행되기 전에 metric ID가 lookup 가능한 dict에 들어 있어야 한다는 것.

## 검증

수정 후 다음 테스트 시나리오를 확인:

1. 기존 129개 테스트 모두 통과
2. `RiskFactor -> metric:revenue` edge가 reference validation에서 rejected되지 않는지 확인하는 새 테스트 추가
3. `uv run ruff check .` — 0 에러

## 완료 조건

- [ ] metric ID가 `all_objects`에 추가됨
- [ ] `reference_validator.py`는 수정하지 않음
- [ ] 기존 테스트 모두 통과
- [ ] ruff lint 0 에러
