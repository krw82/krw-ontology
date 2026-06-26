# Market Move Context Policy

Use this reference only for `krw-ontology-market-move-research`.

`market-move-context/v1` is an app-normalized market observation supplied by the web runtime. It may be built from Yahoo Finance, FMP, Polygon, or another market/news provider, but the provider is not the analytical frame and should not appear in normal user-facing answers.

```text
market move context = observed stock move, session timing, relative market/sector context, and candidate event/news explanations
KRW ontology = filing-supported company baseline, mechanism, constraints, and investment-thesis implications
AI analyst = fused Korean investor interpretation
```

## 1. What This Context Is

Treat the context as a bounded input package:

```text
ticker and company
as-of time
session: regular, pre-market, after-hours, closed, or unknown
price direction and approximate magnitude
volume or activity signal when available
benchmark-relative or sector-relative signal when available
candidate event/news explanations
source timing and source links when provided
internal English ontology search brief
```

It is not:

```text
a company filing
a confirmed company statement
a proof of causality
a raw data table to reproduce
a reason to skip KRW ontology evidence
```

## 2. Provider Boundary

Do not expose provider or API implementation details in normal answers.

Avoid:

```text
Yahoo Finance MCP returned...
FMP API says...
market-move-context/v1 shows...
the provider payload...
raw quote/news feed...
```

Use investor-facing language instead:

```text
오늘 주가 흐름을 보면...
최근 시장 보도에서 언급된 설명 후보는...
같은 시간대 시장/섹터 흐름과 나눠보면...
공시 기준으로 이어지는 경로는...
```

## 3. Price Move Is Not The Cause

Never treat the price move itself as the explanation.

Correct reasoning:

```text
observed move = market reaction to be explained
candidate events = possible explanations
filing baseline = mechanism and thesis impact test
```

Bad:

```text
주가가 6% 빠졌기 때문에 AI 투자 우려가 원인입니다.
뉴스가 하나 있었으므로 그 뉴스가 하락의 이유입니다.
```

Good:

```text
하락폭은 크지만, 현재 확인되는 설명은 AI 투자 부담이 기존 현금흐름/마진 가정에 어떤 압력을 주는지로 이어집니다. 단일 뉴스 하나로 단정하기보다는, 이 이벤트가 공시상 이미 보이던 투자 부담을 재가격화했는지를 봐야 합니다.
```

## 4. Required Internal Checks

Before the final answer, separate these items internally:

```text
1. observed move: direction, magnitude, session, as-of time
2. relative move: company-specific, sector/market-driven, mixed, or unknown
3. event timing: which news or event appeared before or near the move
4. event quality: official/company/major-news/portal/commentary/social
5. ontology baseline: current filing period, business channel, financial channel, risk channel
6. thesis effect: strengthens, maintains, weakens, or breaks the relevant investment assumption
```

If the input lacks relative market or volume context, do not invent it. Write the answer around the confirmed parts and keep the causal claim narrower.

## 5. No Useful Candidate

If the context confirms a price move but no useful event candidate:

```text
do not invent a catalyst
do not write generic market commentary
fall back to the latest filing baseline
say the available recent context does not confirm a clear company-specific event if that matters
```

Good style:

```text
현재 확인된 최근 보도만으로는 하락을 하나의 회사 이벤트로 단정하기 어렵습니다. 그래서 투자적으로는 최신 공시에서 보이는 수요, 마진, 현금흐름, 자본배분 부담이 이번 움직임을 설명할 수 있는 기존 약점인지부터 봐야 합니다.
```

