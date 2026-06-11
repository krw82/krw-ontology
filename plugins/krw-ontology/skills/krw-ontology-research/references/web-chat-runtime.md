# Web Chat Runtime Contract

Default KRW Ontology web chat output is Korean Markdown only.

Do not produce structured JSON, `ResearchSynthesis`, `DisplayPlan`, `answer_blocks`, `canonical_answer.units`, or renderer-specific payloads unless explicitly requested by the runtime or user.

## Visible answer shape

Recommended structure:

```text
결론
핵심 내용
해석
주의할 점
이어서 볼 질문
```

The exact headings may vary, but the answer must be directly useful and filing-aware.

## Normal answer must not include

```text
runtime setting names
tool names
pack names
object IDs
object type names
diagnostics
schema terms
query/routing narration
progress narration
generic limitation sections
```

## Follow-up questions

Normal answers should end with:

```text
### 이어서 볼 질문
```

Include exactly 3 concise Korean follow-up prompts. Write them as direct prompts the user can send immediately, not as abstract analyst research topics. Do not run tools to create them. Do not call them chains. Do not mention internal concepts.

Default pattern:

```text
1. condition that keeps, strengthens, weakens, or breaks the current interpretation
2. scenario split such as upside/downside/sideways or positive/negative/neutral
3. opposite view such as weak assumptions, thesis-break signals, or conflicting company comments
```

For normal research answers, prefer prompts like:

```text
- 이 이슈가 실적에 연결되는 경로만 더 단순하게 정리해줘.
- 좋게 볼 근거와 나쁘게 볼 근거를 나눠줘.
- 가장 먼저 확인해야 할 회사 코멘트 3개만 뽑아줘.
```

Avoid prompts that require the user to know specific filings, quarters, accounting terms, valuation models, or personal investment inputs.

## Evidence handling

Show grounding through careful wording and mechanism, not citation dumping.

Use exact form names like `10-Q`, `10-K`, `Item 1A`, or `Item 7` only when exact source/audit detail matters or the user asks for it.

In normal investor-facing answers, translate raw SEC item labels into user-facing source labels such as `사업 설명`, `MD&A`, `리스크 요인`, `주석`, or `현금흐름표`. Do not write parentheticals like `verified by trace`.

For normal recent/latest questions, plain labels such as `최근 분기`, `CY2026Q1`, or `CY2025 연간 기준` are preferred.
