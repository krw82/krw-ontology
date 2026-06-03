# News Event Policy

Use this reference only for `krw-ontology-news-research`.

News mode is news-led and ontology-augmented.

```text
stock-news event discovery = current event and source context
KRW ontology = company-specific baseline, financial channel, accounting context, risk channel, and investment meaning
AI analyst = fused Korean investor interpretation
```

The final answer should not mechanically separate "news" and "filing" sections unless that is the clearest structure. Internally preserve source provenance, but write the user-facing answer as one coherent investment interpretation.

## 1. When To Search News Events

In news mode, attempt stock-news event discovery before the final answer.

Use it to find:

```text
latest news or reporting
company announcement
earnings-related update
post-filing event
M&A, partnership, litigation, regulation, product, guidance, or capital-allocation update
management comment after the latest indexed filing
market event that may change the filing baseline
current market reaction to a known event
```

Do not answer from memory if the news event layer can verify the current situation.

## 2. Default Recency Window

Default current-news window:

```text
today
yesterday
within 72 hours
last 3 US trading sessions fallback
```

Extend to 7 calendar days only when the default window lacks useful high-quality evidence. Use older sources only as background or to confirm the original event date.

If the user names a specific period, respect it.

## 3. Source Roles

Separate discovery from verification.

Discovery role:

```text
major financial news
market-reaction reporting
high-quality financial portals with clear original reporting
```

Verification role:

```text
SEC filings
company IR
earnings releases
shareholder letters
official blogs or press releases
regulatory or exchange sources
```

Commentary role:

```text
Trefis
TIKR
Seeking Alpha
newsletters
blogs
YouTube
Reddit
forums
social platforms
```

Commentary can help identify what the market is talking about, but it is not final-answer evidence unless the user explicitly asks about sentiment, social reaction, or online discussion.

## 4. Source Quality

Use the MCP source classifications internally:

```text
tier_1_official
tier_2_major_news
tier_3_financial_portal
tier_4_commentary
tier_5_social
unknown
```

Do not expose these labels to the user.

Practical source priority:

```text
official/company/SEC source for exact facts and accounting
major financial news for current market reaction and fresh reporting
financial portals only when they add clear market-reaction context
commentary only as leads or sentiment context
social/forum/video only when user asks about sentiment or online reaction
```

Do not let a low-quality recent source override a slightly older but stronger source.

## 5. Event Extraction

After stock-news event discovery, extract the current event internally:

```text
company or companies
event date
source date
source type
recency bucket
event type
affected product, segment, geography, customer, supplier, or business line
claimed revenue, margin, cash-flow, cost, risk, valuation, or capital-allocation impact
source confidence
open questions
unsupported claims
ontology bridge brief
```

Do not expose internal fields such as event IDs, source tiers, recency buckets, or ontology bridge briefs.

## 6. News-To-Ontology Mapping

After the event is identified, search KRW ontology for the event's investment meaning.

Map news events to ontology concepts such as:

```text
business combinations / acquisition cash outflow
cash flow / FCF / capex
share repurchases / dilution management
SBC / R&D / talent investment
cost of revenue / gross margin / operating margin
revenue recognition / RPO / backlog
product platform / customer demand
segment commentary
risk factors and direct exposure
management discussion and notes
```

Do not run ontology search as a generic company overview unless the event is too vague to map. Search the specific financial or business channel implied by the event.

## 7. Filing Vs News

Direct filing evidence remains the baseline for:

```text
historical reported numbers
accounting treatment
segment definitions
cash-flow classification
annual mix
latest reported period baseline
```

News evidence may update the baseline only when it is newer, reliable, and explicitly updates the same fact, metric, event, guidance, or transaction.

Do not let article snippets override direct filing evidence for historical reported numbers or accounting classification.

## 8. Conflict Handling

If news evidence conflicts with ontology or filing evidence:

```text
check event date and filing period
check source type and reliability
check metric definition
check GAAP vs non-GAAP
check reported result vs guidance, estimate, or commentary
prefer primary source for exact facts
prefer direct filing evidence for historical classification
state the conflict only when it changes the investment interpretation
```

If a news claim is plausible but not explicitly supported by direct evidence, frame it as an inference.

## 9. No Useful Recent Event

If the default news window has no useful high-quality current event:

```text
do not invent a catalyst
do not over-weight stale commentary
do not add a generic limitation section
answer from the latest filing baseline
briefly say that no material new recent event was confirmed if it matters
```

Good user-facing style:

```text
최근 며칠 보도 흐름만으로는 새로운 핵심 이벤트가 뚜렷하지 않습니다. 그래서 투자적으로는 최신 공시에서 보이는 수요/마진/현금흐름 흐름을 기준으로 보는 편이 맞습니다.
```

## 10. Final Answer Shape

Write Korean investor prose:

```text
current event
-> company-specific baseline
-> financial channel
-> thesis/risk/valuation implication
-> what to watch next
```

Use source timing naturally when material:

```text
오늘 보도 이후
전일 보도 기준
최근 며칠 보도 흐름상
공식 발표와 공시 기준으로 보면
```

Do not turn the final answer into a source-ranking report.
