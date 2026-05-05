---
name: build-pipeline-stages
description: "코드 전용 파이프라인 스테이지 구현 스킬. checkpoint, orchestrator, resolve_ticker, discover_source, download, clean_md, extract_sections, build_spans, extract_xbrl, build_indexes 스테이지를 구현한다. 파이프라인 스테이지, 체크포인트, 오케스트레이터 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build Pipeline Stages Skill

code-stage-engineer 에이전트가 따라야 할 구현 가이드.

## 1. pipeline/checkpoint.py

`dev_spec_v1.md` Section 9를 정확히 구현:

```python
class CheckpointManager:
    def __init__(self, checkpoint_path: Path)
    def is_stage_complete(self, stage: str) -> bool
    def mark_complete(self, stage: str, metadata: dict = None)
    def save(self)  # atomic_write_json
    def load(self) -> dict  # 파일 없으면 기본값 반환
```

체크포인트 파일 위치: `{output_dir}/companies/{ticker}/ontology/{doc_type_key}/{period}/.checkpoint.json`

## 2. pipeline/orchestrator.py

`dev_spec_v1.md` Section 9.3을 따른다:

```python
PIPELINE_STAGES = [
    "resolve_ticker", "discover_source_document", "download_source_document",
    "clean_to_markdown", "extract_sections", "build_source_spans",
    "extract_xbrl_facts", "extract_evidence_quotes", "extract_research_claims",
    "extract_risks_drivers_headwinds", "extract_assumption_candidates",
    "generate_edges", "validate_ontology", "build_indexes", "build_graph_report"
]
```

- 체크포인트 기반 resume: `is_stage_complete` 확인 후 skip
- 실패 시 PipelineStageError raise
- 매 스테이지 완료 후 checkpoint save
- AI 스테이지(8~12)는 ExtractionWorker 호출 (ai-stage-engineer가 구현)
- validate_ontology는 validator 체인 호출 (validator-engineer가 구현)

## 3. 스테이지별 구현 가이드

각 스테이지는 `dev_spec_v1.md` Section 7의 계약을 따른다:

### resolve_ticker (Section 7.1)
- SEC company_tickers.json API 호출
- CIK zero-pad 10자리
- httpx + User-Agent 헤더

### discover_source (Section 7.2)
- SEC submissions JSON API
- form == "10-K" 찾기, "10-K/A" 스킵
- source_url 조합

### download_source (Section 7.3)
- httpx.get + 3회 retry with exponential backoff
- SHA-256 계산 + metadata.json 갱신

### clean_to_markdown (Section 7.4)
- BeautifulSoup → script/style/nav/footer 제거
- markdownify 변환 + 테이블 post-process

### extract_sections (Section 7.5)
- Section 16의 regex 패턴 사용
- TOC skip heuristic
- Item 1, 1A, 1C, 7, 7A, 8 타겟

### build_spans (Section 7.6)
- Section 15의 window 파라미터 사용
- 문장 경계 우선 분할
- 150자 overlap
- text_hash = sha256(normalize_newlines(text))

### extract_xbrl (Section 7.7)
- Inline XBRL 파싱 (us-gaap namespace)
- 실패 시 log + missing_or_failed 상태

### build_indexes (Section 7.14)
- artifact_index.json 생성 (Section 11 schema)
- company_artifact_index.json skeleton
- 모든 경로는 workspace-root relative

## 4. 구현 시 주의사항

- 모든 SEC API 호출에 `settings.sec_user_agent` 사용
- httpx timeout: 120초
- 파일 쓰기는 utils/io.py의 atomic_write 사용
- 로깅은 utils/logging.py의 StructuredFormatter 사용
- 각 스테이지는 독립 함수로 구현, orchestrator가 호출
