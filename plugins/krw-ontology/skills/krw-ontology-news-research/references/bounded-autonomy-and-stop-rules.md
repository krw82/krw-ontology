# Bounded Autonomy and Stop Rules

The goal is not to remove AI judgment. The goal is to prevent unstable exploration while preserving analytical autonomy.

```text
AI autonomy to preserve:
interpretation
comparison judgment
caveats
business mechanism explanation
final Korean prose

AI autonomy to bound:
repeated broad search
same query with slight wording changes
unbounded retrieve fallback
tracing every candidate
turning related evidence into direct proof
re-running similar stock-news searches after the current event is already identified
over-weighting weak recent commentary over stronger official or major-news evidence
```

## Default workflow

```text
1. stock-news event discovery first for current-event discovery
2. event extraction and event-to-investment framing
3. query_context as the first ontology call
4. targeted query/compare only for explicit ontology gaps
5. selected trace/chain only when materially useful
6. fused Korean investor answer
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
stock-news event discovery found a material current event with adequate source quality
research_status is sufficient_for_default_answer
answerability is enough for a narrow answer
same ticker/topic has already returned no better candidates
remaining gap is market data, price target, fair value, or final investment opinion
direct exposure is unsupported and related context is already identified
metric dimension evidence is missing after one targeted metric lookup
the next tool would only make the answer more exhaustive, not more correct
only weak news sources remain after one constrained official/major-source confirmation attempt
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

Do not introduce non-covered companies, tickers, or peers in normal answers, comparison tables, or follow-up questions unless:

```text
the user explicitly asked for that company/ticker/peer
coverage is confirmed through indexed/catalog/company metadata
the answer clearly states that the company is outside the covered evidence set and the user asked for that caveat
```
