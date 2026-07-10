# Ontology Bridge Policy

Use this reference only for `krw-ontology-market-move-research`.

The bridge converts a market observation into filing-grounded research when filing-grounded depth is needed. It should be concise, English, and optimized for a model-authored SearchPlan v2.

Do not invoke the bridge automatically for every first-turn market-move question. A simple "why did it move today?" question can be answered from the app-provided price/news context, with follow-up prompts guiding the user toward KRW ontology research.

Use this bridge when:

```text
the user asks for 공시/filing evidence
the user asks for long-term or durable thesis impact
the user asks whether the move changes buy/sell/hold, investment assumption, or risk view
the user asks for revenue, margin, cash flow, balance sheet, dilution, capital allocation, or risk impact
the answer would otherwise imply a durable company-specific conclusion
```

## 1. Required Brief

The internal English brief should include:

```text
ticker and company
observed move and session timing
candidate event/news explanation
why the event could matter economically
financial channel to test
business/risk channel to test
latest filing baseline needed
confirmation and falsification conditions
```

Do not send raw Korean topic text or raw provider output directly to ontology tools.

Before the first filing call, convert the brief into one complete SearchPlan v2.
Use atomic qualitative clauses with `required_concepts` and
`required_predicates`; put exact metrics in separate clauses with metric scope,
dimensions, comparison axes, and calculation window. Use explicit tickers for
the named company and size result limits to the required clause coverage.

Call `query_context` with exactly `{search_plan}`. Read ResearchState v2
`clause_coverage`/`evidence_units` for filing support and
`computed_values`/`calculation_coverage` for arithmetic. Continue only through
material `missing_parts`, `recommended_actions`, or `continuation`; never send
legacy top-level question/ticker/limit arguments or repeat a covered clause.

## 2. Ontology-Friendly Channels

Map the explanation to one or more of:

```text
revenue growth / demand / backlog / RPO
gross margin / cost of revenue / input cost / cloud or infrastructure cost
operating margin / R&D / SBC / headcount
cash flow / FCF / CAPEX / capital intensity
balance sheet / debt / liquidity / lease commitments
share repurchases / dilution management
M&A / business combinations / acquisition cash outflow
customer concentration / supplier exposure / geography
regulatory / legal / antitrust / export-control risk
product platform / AI platform / segment commentary
```

## 3. Latest Filing Baseline

Use the latest available filing as the current baseline:

```text
newer 10-Q > older 10-K for current drivers
latest 10-K = annual mix/business baseline when a newer 10-Q exists
latest 10-K = primary only when no newer 10-Q exists
```

News date and filing period are different:

```text
news date = event/reporting date
filing period = financial baseline period
```

Do not invent future filing periods. If a future/current quarter is not in the indexed filing set, describe it as the next report or upcoming period instead of naming a specific filing as if it exists.

## 4. Stop Rule

Stop ontology work when:

```text
the candidate explanation has a sufficient company-specific baseline
the answer can state what assumption is affected
additional searches would only make the answer longer, not more correct
the remaining gap is market pricing, estimate revisions, or real-time data not present in ontology
```

If ontology work is not needed for the first answer, stop before the first ontology call and write a market/news explanation. Then use the three follow-up prompts to offer filing-grounded checks.
