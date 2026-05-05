---
name: build-scaffold
description: "프로젝트 기반 구조 생성 스킬. pyproject.toml, CLI skeleton, config, schema, utils, YAML 템플릿, 에러 계층, __init__.py 파일들을 생성한다. 새 프로젝트 초기 설정이나 프로젝트 skeleton 생성을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build Scaffold Skill

scaffold-engineer 에이전트가 따라야 할 구현 가이드.

## 1. pyproject.toml

`dev_spec_v1.md` Section 2를 정확히 복사하여 구현한다. 주요 의존성:
- typer>=0.12, pydantic>=2.0, httpx>=0.27, beautifulsoup4>=4.12
- markdownify>=0.12, lxml>=5.0, claude-agent-sdk, pyyaml>=6.0, rich>=13.0
- dev: pytest>=8.0, pytest-cov>=5.0, ruff>=0.5, mypy>=1.10
- console script: `krw-ontology = "krw_ontology.cli.main:app"`

## 2. CLI (cli/main.py)

`dev_spec_v1.md` Section 3을 따른다. 4개 명령어:
1. `init-workspace` — ontology/schema/ 디렉토리 + 7개 YAML 파일 생성
2. `build-evidence-ontology TICKER` — --document-type, --latest, --period, --force, --output-dir
3. `validate TICKER` — --document-type, --period
4. `build-report TICKER` — --document-type, --period

v1에서 document_type은 "10-K"만 허용. `ACCEPTED_DOC_TYPES = {"10-K"}`.

## 3. config/settings.py

`dev_spec_v1.md` Section 4.2를 따른다. PipelineConfig 클래스:
- model: 기본값 "claude-sonnet-4-20250514", env var `KRW_MODEL`로 오버라이드
- max_retries, retry_base_delay_seconds, sec_user_agent
- batch_sizes: quote_extraction=20, claim_extraction=15
- `~/.config/krw-ontology/config.yaml` 또는 `.krw-ontology.yaml`에서 로드

## 4. config/constants.py

`dev_spec_v1.md` Section 5.3을 따른다:
- DOCUMENT_TYPE_DISPLAY, DOCUMENT_TYPE_KEY, MAPPING dict
- SECTION_PATTERNS dict (Section 16)
- SPAN_TARGET_CHARS_MIN=500, SPAN_TARGET_CHARS_MAX=1200, SPAN_OVERLAP_CHARS=150, SPAN_HARD_MAX_CHARS=1500

## 5. schema/id_utils.py

`dev_spec_v1.md` Section 5.1~5.2를 정확히 구현:
- `generate_source_document_id(ticker, period, doc_type_key)`
- `generate_scoped_id(object_type, ticker, period, doc_type_key, local_id)`
- `generate_metric_id(canonical_name)`
- `normalize_doc_type(display)`, `denormalize_doc_type(key)`

local_id 규칙 (Section 5.2):
- SourceSpan: "{section_name}:{sequence:04d}"
- EvidenceQuote: "{section_name}:{sequence:04d}:{quote_seq:03d}"
- ResearchClaim: "{slug}"
- ResearchObject: "{slug}"
- XBRLFact: "{safe_taxonomy_tag}:{hash8}" (sha1(context_ref|unit|normalized_value)[:8])
- Edge: "{relation_id}:{hash10}" (sha1(from_id|relation_id|to_id)[:10])

## 6. schema/objects.py

`dev_spec_v1.md` Section 6.1의 12개 타입에 대한 Pydantic v2 모델:
- SourceDocument, SourceSpan, EvidenceQuote, LanguageSignal
- ResearchClaim, ResearchObject (RiskFactor/GrowthDriver/Headwind 공통)
- AssumptionCandidate, Metric, XBRLFact, Edge

각 모델은 objects.yaml의 fields를 반영. `type` 필드로 타입 판별.

## 7. utils/io.py

- `read_jsonl(path) -> list[dict]`
- `write_jsonl(path, objects)` — 한 줄에 하나의 JSON 객체
- `atomic_write(path, content)` — 임시 파일에 쓰기 후 rename
- `atomic_write_json(path, data)` — JSON pretty-print + atomic write
- `compute_sha256(content: bytes) -> str`

## 8. utils/logging.py

`dev_spec_v1.md` Section 14를 따른다:
- StructuredFormatter (JSON 로그)
- per-stage logging: stage, tokens, duration, cost_estimate_usd

## 9. 에러 계층

`dev_spec_v1.md` Section 13.1:
- KrwOntologyError (base)
- PipelineStageError
- ExtractionError
- ValidationError
- ConfigurationError

## 10. init-workspace 명령 구현

`dev_spec_v1.md` Section 19를 따른다. 7개 YAML 파일 생성:
- objects.yaml, relations.yaml, quote_types.yaml, language_signals.yaml
- claim_types.yaml, risk_categories.yaml, metric_dictionary.yaml

각 파일의 내용은 `dev_spec_v1.md` Section 6.1~6.7에서 가져온다.

## 구현 순서

1. pyproject.toml
2. src/krw_ontology/__init__.py + 모든 __init__.py
3. config/constants.py
4. config/settings.py
5. schema/id_utils.py
6. utils/io.py
7. utils/logging.py
8. schema/objects.py (Pydantic models)
9. 에러 계층 (errors.py 또는 __init__.py에)
10. cli/main.py (init-workspace만 먼저 구현, 나머지는 stub)
