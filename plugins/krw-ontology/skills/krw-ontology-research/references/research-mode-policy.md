# Research Mode Policy

Research modes are product controls selected by the UI/runner. They are not answer content.

Do not infer deep mode from the user's wording. A user can ask for a detailed explanation while staying in Fast or Standard mode.

## V1 default

V1 web chat is deep-first.

```text
public default = Deep
Fast/Standard = internal optimization paths for future tuning
normal path = one agent run researches and writes the final answer
composer fallback = emergency recovery only
```

The UI may hide the mode selector. Do not mention mode names, budgets, fallback, or recovery behavior in user-facing answers.

All V1 web-chat research modes forbid `response_detail="full"`. Deep mode means more careful selected verification, not larger raw query payloads.

## Fast

Purpose: quick practical answer.

```text
query_context: 1
query: 0-1 targeted only
trace/chain: 0 by default
retrieve: 0
company_context/index_context/catalog/quality: 0
response_detail="full": forbidden
```

Answer directly with available evidence. Do not mention mode or lookup limits.

## Standard

Purpose: internal optimized web-chat analysis, not the V1 public default.

```text
query_context: 1
query: 0-1 targeted only for missing parts
trace/chain: normally 1-2 selected roots
retrieve: 0 by default
compare: only when explicit and query_context is insufficient
response_detail="full": forbidden
```

Stop when `research_status` is sufficient. Use selected trace/chain only when it materially strengthens strong claims.

## Deep

Purpose: V1 default web-chat analysis with higher reliability.

```text
query_context first
more trace/chain allowed
additional query allowed only when materially useful
unscoped retrieve still discouraged
same agent run normally writes final Markdown answer
response_detail="full": forbidden
```

Deep mode is not permission to brute-force tools or chain every candidate.

Deep mode should not rely on budget-stop composer fallback as normal control flow. If fallback is needed, treat it as emergency recovery from SDK/transport/partial-run failure and keep all recovery details internal.

## Conflict resolution

Choose the strictest rule among:

```text
runner budget
agent_autonomy
kernel policy
skill policy
```
