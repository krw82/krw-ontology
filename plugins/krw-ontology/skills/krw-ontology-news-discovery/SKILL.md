---
name: krw-ontology-news-discovery
description: Use for news-mode discovery only. Curate recent market-news event cards that a user can choose before KRW Ontology filing analysis. Do not write final investment analysis.
---

# KRW Ontology News Discovery Skill

This skill is only for the first step of news mode: finding user-selectable market-news event candidates.

It is not a final-answer skill.

```text
market-news discovery -> Korean event cards -> hidden ontology bridge brief
```

The selected card will later be analyzed by the normal news-research workflow. Do not call KRW Ontology tools in this skill.

## Operating Contract

Default visible output is one short Korean 안내문 only.

Good:

```text
최근 시장 뉴스에서 분석할 만한 후보를 추렸습니다. 하나를 선택하면 공시 기준으로 연결해 분석하겠습니다.
```

If the search result has zero usable `cards` after filtering, say that directly:

```text
최근 시장 뉴스에서 분석할 만한 후보를 찾지 못했습니다. 더 구체적인 기업명이나 기간을 넣어 다시 시도해주세요.
```

Do not write a long answer, investment conclusion, valuation judgment, filing baseline, or final Markdown report.

## Source Priority

Discovery cards must be based on market-news, financial-news, or app-normalized provider results. Provider names and raw API payloads are internal implementation details, not visible card content.

Prefer:

```text
Yahoo Finance
Google News-like aggregated market results
Reuters
Bloomberg
CNBC
WSJ
MarketWatch
AP
Barron's
Financial Times
major financial portals and market-news sites
```

Never show these as user-facing discovery cards:

```text
SEC Filings
Form 8-K
Form 10-Q
Form 10-K
Investor Relations
company IR pages
annual reports
quarterly reports
old filing pages
standalone company press-release pages
```

Filings and IR are verification material for the later analysis step, not discovery cards.

## Tool Policy

Allowed tools:

```text
route_news_question
resolve_news_entities
search_company_news_events
search_market_news_events
build_news_timeline
audit_news_sources
```

Forbidden tools:

```text
read_news_sources
search_news_by_domain
krw_ontology_query_context
krw_ontology_query
krw_ontology_retrieve
krw_ontology_trace
krw_ontology_chain
krw_ontology_compare
```

For company/ticker questions, use the app-provided market/news card pipeline when available. If this skill is running with stock-news tools instead, call `search_company_news_events` with market-news discovery intent:

```json
{
  "mode": "market_narrative_discovery",
  "exclude_official_sources": true,
  "price_move_direction": "down | up | unknown"
}
```

Use `price_move_direction: "down"` for Korean/English wording such as:

```text
왜 떨어져, 왜 빠져, 하락, 조정, 급락, 약세, why down, why falling, stock drops
```

Use `price_move_direction: "up"` for:

```text
왜 올라, 상승, 급등, 강세, why up, rally, jumps, gains
```

Use `"unknown"` for neutral latest-news questions.

## Card Quality

Each card should describe a market narrative, not a raw page title.

Bad:

```text
SEC Filings - Apple Investor Relations
Form 8-K - SEC.gov
Apple shares rise after ...
```

Good:

```text
중국 경쟁과 아이폰 판매 둔화 우려가 AAPL 약세 요인으로 부각
AI 투자 부담과 서비스 성장 기대가 엇갈리며 밸류에이션 논쟁
```

Cards are market narrative candidates. Use cautious wording:

```text
시장 뉴스는 ... 요인으로 언급했습니다.
... 우려가 투자심리에 부담으로 부각됐습니다.
```

Do not state a news narrative as confirmed company fact.

## Required Hidden Brief

Every card must carry an internal English ontology bridge brief for the later filing-analysis step.

Required meaning:

```text
ticker
company_name
market narrative
source URLs
likely financial axes
likely business/risk axes
suggested ontology questions
```

If the card is generated from an app-normalized provider result, the hidden brief must also preserve:

```text
candidate explanation
source timing when available
whether the event appears company-specific, market-driven, sector-driven, mixed, or unknown
```

Do not expose the English brief in visible chat text.

## Final Output

Write only the short Korean 안내문. The web runtime will create structured cards from the market/news provider or stock-news tool result.

Do not print JSON or Markdown cards yourself.
