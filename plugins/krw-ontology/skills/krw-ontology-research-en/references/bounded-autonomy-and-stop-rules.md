# Bounded Autonomy and Stop Rules

The goal is not to remove AI judgment. The goal is to prevent unstable exploration while preserving analytical autonomy.

```text
AI autonomy to preserve:
interpretation
comparison judgment
caveats
business mechanism explanation
final English prose

AI autonomy to bound:
repeated broad search
same query with slight wording changes
unbounded retrieve fallback
tracing every candidate
turning related evidence into direct proof
```

## Default workflow

```text
1. query_context first
2. targeted query/compare only for explicit gaps
3. selected trace/chain only when materially useful
4. final English answer
```

## Tool budgets as behavior, not user-facing content

Normal answers should not mention budget, mode, tool limit, or internal failure names.

Behavioral defaults:

```text
after query_context:
at most one targeted query/compare per clear missing part

trace/chain:
selected roots only, not every candidate

retrieve:
legacy fallback only; never broad retrieve after sufficient query_context

catalog/index/quality:
debug, audit, or coverage checks only; not normal answer flow
```

## Stop conditions

Stop searching when:

```text
research_status is sufficient_for_default_answer
answerability is enough for a narrow answer
same ticker/topic has already returned no better candidates
remaining gap is market data, price target, fair value, or final investment opinion
direct exposure is unsupported and related context is already identified
metric dimension evidence is missing after one targeted metric lookup
the next tool would only make the answer more exhaustive, not more correct
```

## Sector/global stop rule

For sector/global/macro questions:

```text
use a bounded covered universe
synthesize cross-company signals
avoid whole-catalog inventory
avoid unsupported tickers
answer from covered companies and caveat coverage only if it changes interpretation
```

The answer should not become a coverage report. It should explain what the covered company disclosures imply about the macro or sector question.
