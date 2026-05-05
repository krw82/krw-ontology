---
name: build-ai-extraction
description: "AI 추출 스테이지 구현 스킬. Claude Agent SDK 기반 ExtractionWorker, quote/claim/object/edge 추출 프롬프트, AI 출력 검증 Pydantic 모델을 구현한다. AI 추출, Claude Agent SDK, 프롬프트 템플릿, 배치 처리 관련 작업을 요청받으면 반드시 이 스킬을 사용할 것."
---

# Build AI Extraction Skill

ai-stage-engineer 에이전트가 따라야 할 구현 가이드.

## 1. extraction/worker.py

`dev_spec_v1.md` Section 10.1의 ExtractionWorker 프로토콜 구현:

```python
class ExtractionWorker:
    def __init__(self, model: str, cwd: Path, max_retries: int = 3)

    async def extract(
        self, prompt_template: str, input_data: dict,
        output_schema: dict, stage_name: str
    ) -> list[dict]

    async def _call_with_retry(self, messages: list, stage_name: str) -> str
```

### 핵심 구현 규칙:
- `claude-agent-sdk`의 `query()` 함수 사용
- `ClaudeAgentOptions(model=..., cwd=..., system_prompt=...)`
- Exponential backoff: 5s → 10s → 20s, 최대 120s
- 3회 실패 후 ExtractionError raise

## 2. extraction/schemas.py

AI 출력 검증용 Pydantic 모델:
- `QuoteExtractionOutput` — EvidenceQuote + LanguageSignal
- `ClaimExtractionOutput` — ResearchClaim
- `ObjectExtractionOutput` — RiskFactor/GrowthDriver/Headwind
- `AssumptionExtractionOutput` — AssumptionCandidate
- `EdgeGenerationOutput` — Edge

`dev_spec_v1.md` Section 18.2의 JSON schema를 Pydantic으로 변환.

## 3. 프롬프트 템플릿

### quote_extraction.py (Section 18.1)
- 시스템 프롬프트: 재무 문서 분석가 역할
- quote_text는 반드시 EXACT, VERBATIM
- quote_type 분류, language_signals 추출
- 출력: JSON array

### claim_extraction.py
- 입력: spans + quotes
- claim_type 분류 (factual, forward_looking, risk_assessment, strategic, assumption)
- supported_by_quotes 필수 (min 1)
- 출력: JSON array

### object_extraction.py
- 입력: all claims + all quotes + risk_categories + metric_dictionary
- RiskFactor/GrowthDriver/Headwind 추출
- affects는 canonical metric만, 자신 없으면 unmapped_impacts에
- supported_by_claims 또는 supported_by_quotes 중 최소 1개 필수
- 출력: JSON array

### edge_generation.py
- 입력: all objects + claims + quotes + relations_whitelist
- Edge의 relation_name/relation_id가 whitelist와 일치해야 함
- from_id, to_id 객체 타입이 whitelist의 from/to와 일치
- 출력: JSON array

## 4. 배치 처리 전략

| 스테이지 | 배치 크기 | 스코프 |
|---------|----------|--------|
| extract_evidence_quotes | 20 spans | 배치 |
| extract_research_claims | 15 spans + quotes | 배치 |
| extract_risks_drivers_headwinds | full document | 전체 |
| extract_assumption_candidates | full document | 전체 |
| generate_edges | full document | 전체 |

## 5. 에러 핸들링 (Section 13.2~13.4)

- Per-batch: 3회 retry → 실패 시 batch_failures.jsonl 기록 → 다음 배치로 continue
- Per-stage: >50% 배치 실패 시 PipelineStageError
- Malformed JSON: markdown code block에서 추출 시도 → 실패 시 raw output 저장
- Rate limit: 60s sleep 후 1회 추가 retry

## 6. Deduplication

Quote deduplication: `(normalize_text(quote_text), source_span_id)` 해시 기반.
normalize_text는 whitespace normalization만 수행.
