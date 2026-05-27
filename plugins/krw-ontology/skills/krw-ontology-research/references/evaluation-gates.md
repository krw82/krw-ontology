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
retrieve is legacy fallback only; do not use after sufficient query_context
trace/chain selected roots only; never brute-force every candidate
no repeated equivalent query
no broad full-response first pass
open tool result missing completed runs = 0
tool budget / fallback / retry wording visible to user = 0
overflow or oversized tool result becomes split-and-continue behavior, not a final-answer failure
```

## Latency and quality targets

```text
internal leak cases = 0
runtime setting leak cases = 0
budget/error/fallback/overflow wording cases = 0
B-grade cases <= 1 in 30Q smoke
```
