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
다음으로 파고들 질문
```

The exact headings may vary, but the answer must be directly useful and filing-aware.

## Normal answer must not include

```text
mode names
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
다음으로 파고들 질문
```

Include exactly 3 concise Korean questions. Do not run tools to create them. Do not call them chains. Do not mention internal concepts.

## Evidence handling

Show grounding through careful wording and mechanism, not citation dumping.

Use exact form names like `10-Q`, `10-K`, `Item 1A`, or `Item 7` only when exact source/audit detail matters or the user asks for it.

For normal recent/latest questions, plain labels such as `최근 분기`, `CY2026Q1`, or `CY2025 연간 기준` are preferred.
