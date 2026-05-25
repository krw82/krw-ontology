# Evaluation Gates

Use these gates for web-chat quality checks and E2E harnesses.

## Normal answer gates

```text
Korean Markdown only
no internal terms
no mode/tool/pack names
no budget/fallback/error labels
no generic limitation heading
no FY as primary user-facing label
latest questions start from the most recent available filing; a newer 10-Q beats an older 10-K, while the latest 10-K is primary only when no newer 10-Q exists
three follow-up questions present
strong claims traceable or metric-lineage grounded
direct vs related context separated
```

## Tool behavior gates

```text
query_context first
V1 public path uses Deep by default
retrieve count = 0 in Fast/Standard unless explicit fallback condition
Fast trace/chain = 0 normally
Standard trace/chain = 1-2 normally
Deep can use more selected trace/chain, but must not brute-force every candidate
no repeated equivalent query
no broad full-response first pass
tool_budget_exceeded user-visible failure = 0
composer fallback user-visible wording = 0
```

## Latency and quality targets

```text
standard internal smoke p95 around 10-12 seconds
deep V1 prioritizes completed answer quality over shallow latency
internal leak cases = 0
mode violation cases = 0
budget/error/fallback wording cases = 0
B-grade cases <= 1 in 30Q smoke
```
