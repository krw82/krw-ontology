# News Research Tool Policy

Use this reference only for `krw-ontology-news-research`.

## 1. Source Order

Use KRW Feed first. It is the product's stored market-event layer.

```text
selected issue IDs -> get_feed_context
named ticker or company -> list_feed_items
specific stored items -> get_feed_items
one material feed gap or conflict -> get_yahoo_finance_news once for the same ticker
event interpretation -> KRW ontology company research
```

Do not use WebSearch, WebFetch, Stock News MCP, or repeated Yahoo Finance lookups in this skill.

Use Yahoo Finance only to supplement the feed with a directly relevant recent report. It cannot replace a selected issue and cannot become the sole basis for a strong company claim when company evidence is available.

## 2. KRW Feed Roles

```text
list_feed_items
- Find a bounded set of recent issues for named tickers or companies.

get_feed_items
- Read specific stored feed posts when a compact item view is enough.

get_feed_context
- Read selected issue context, linked source items, bounded original wording, image-extraction records, internal research packets, and ticker context.
- In `krw-feed-context/v2`, use a packet only to choose the next company-research clause. Never cite it as an external source or let it override the linked original wording.

get_yahoo_finance_news
- Make one narrow supplementary or corroborating check for the same ticker and event.
```

Read only the items needed to identify the event, the relevant original wording, and the covered tickers. Do not scan the feed as a substitute for company research.

## 3. Ontology Research Roles

Use the same evidence discipline as normal company research.

```text
query_context
- First company-research call. Author one complete event-specific SearchPlan v2 and call with exactly {search_plan}.

query
- Targeted follow-up for one named missing clause, metric, period, dimension, or company comment.

compare
- Explicit aligned comparison only when the selected event requires it.

trace
- Verify a selected quote, metric, date, amount, term, or strong claim.

chain
- Expand one selected business mechanism when the event-to-financial path needs explanation.

retrieve
- Rare bounded evidence extension for a named gap; never the default news-research call.

company_context
- Orientation only when needed to author a company-specific SearchPlan.
```

After the first company-research call, read answerability, clause coverage, evidence units, computed values, calculation coverage, missing parts, recommended actions, continuation, and warnings. Continue only for a material named gap. Do not repeat the same broad plan with cosmetic wording changes.

## 4. Research Boundaries

```text
feed item = event context, not automatic company fact
company evidence = business and financial baseline
analyst synthesis = event meaning, caveats, and conditions
```

Do not make an exact metric claim without aligned metric evidence. Do not present a company comment without direct support. Do not use source frequency, social attention, or a recent headline as evidence that a stock will rise or fall.
