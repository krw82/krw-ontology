# Bounded Autonomy and Stop Rules

Preserve analytical judgment while making news and ontology continuation
evidence-state driven.

```text
Preserve:
interpretation
comparison judgment
caveats
business mechanism explanation
final Korean prose

Prevent:
re-running similar news searches after the current event is identified
over-weighting weak commentary over official or major-news evidence
repeating broad ontology plans with cosmetic wording changes
tracing every candidate
turning related filing context into proof of event causality
shrinking evidence budgets before logs show that quality is preserved
```

## Default workflow

```text
1. Identify and frame the selected current event.
2. Have the active DeepSeek agent author a complete event-specific SearchPlan.
3. Call query_context with {search_plan}.
4. Read answerability, clause_coverage, calculation_coverage, missing_parts,
   recommended_actions, continuation, and warnings.
5. Continue only for a specific event or ontology gap that could change correctness.
6. Use selected trace/chain only when materially useful.
7. Write the fused Korean investor answer.
```

`plan_query` is optional validation/debug and performs no retrieval.

## Adaptive budgets

Do not expose limits, modes, or internal failures in normal answers.

Choose `limit_results` and `limit_tickers` for the actual number of required
clauses, tickers, metric periods/pairs, and possible conflicts. Do not impose a
fixed top-5, limit-3, one-follow-up, or other hard tool-call rule. Reduce
budgets later only when production logs and quality evaluation support it.

## Stop conditions

Stop searching when:

```text
the current event is identified with adequate source quality
answerability.status is answerable and every load-bearing required clause is covered
the requested numeric axes have covered calculation_coverage
a narrow partial answer is safe and remaining gaps cannot change it
direct exposure or event causality is unsupported and related context is separated
the remaining gap requires market price, valuation, or unavailable external data
only weak news sources remain after targeted confirmation
the next call would only add volume rather than improve correctness
```

Continue when a `missing_parts` or `recommended_actions` entry identifies a
material, answerable gap. Target that clause, ticker, metric, period, or object;
do not repeat the full broad plan.

## Tickerless post-news discovery

For a concrete tickerless event with a factor, channel, product, business
model, metric, event type, or company type, set `universe="covered"` and
leave `tickers` empty. For explicit companies, set `tickers` and omit
`universe`.

Let the agent choose a discovery budget large enough for the requested breadth
and evidence needs. Treat routed companies as candidates until their required
clauses are covered. Avoid whole-catalog inventory and unsupported companies.
