---
name: krw-ontology-news-discovery
description: Use when the user wants to find recent company or market issues. Start from the stored KRW Feed, then use one bounded current-market-news cross-check only when the feed is incomplete or needs corroboration.
---

# KRW Feed Discovery

Use this skill to find issues from the product's stored KRW Feed. The feed is the primary source; one bounded current-market-news read may supplement it. This is not an open-web search and not a final company-research report.

```text
user question
-> stored X-feed issues
-> compact Korean issue orientation
-> optional follow-up into event or filing analysis
```

## Source Boundary

- Use KRW Feed MCP first for news/event discovery.
- Use at most one current-market-news lookup for the same ticker only when the feed has no usable direct item, is materially incomplete, or needs a narrow cross-check.
- Feed items are stored X-based market observations. They may describe a company, sector, rumor, or market narrative; they are not company-confirmed facts.
- Supplementary market reporting is also observation, not company-confirmed fact. Keep it visibly separate from feed findings.
- Do not use WebSearch, WebFetch, Stock News MCP, or model memory to fill a gap.
- If neither the feed nor the bounded supplementary check has a relevant item, say that no relevant item was found in the available sources. Do not imply that no event occurred anywhere.

## Tool Workflow

1. If the user names one or more tickers, call `list_feed_items` with those tickers.
2. If the app provides selected issue IDs, call `get_feed_items` or `get_feed_context` for those IDs rather than rediscovering the event.
3. Read only enough feed-post context to distinguish direct company items from sector or market context.
4. If needed, call `get_yahoo_finance_news` once for the same ticker and a narrow recent window. It may add context but must not replace a selected feed issue.
5. Do not call KRW Ontology filing tools in this discovery step.
6. Do not create synthetic event cards. The product feed already owns card rendering and selection.

The current feed lookup is ticker and recency bounded. If the user gives neither a ticker nor a selected feed item, ask for a company/ticker or direct them to select an item from the feed.

## Interpretation Rules

- Rank items by recency, company specificity, and whether they describe a material business or financial channel.
- Keep direct company items separate from sector-wide or market-wide discussion.
- Do not claim that a post caused a price move solely because it is recent.
- Do not turn a feed summary into an earnings, valuation, or buy/sell conclusion.

## Final Output

Write a compact Korean Markdown orientation for the investor:

```text
최근 피드에서 확인된 이슈
추가 시장 보도에서 확인된 맥락
회사 직접 관련 내용
시장/섹터 맥락
다음으로 확인할 것
```

When the user has not selected an item yet, end by inviting a specific stored issue to be examined further. Do not expose X, MCP, tool names, issue IDs, raw payloads, or internal retrieval details unless the user explicitly asks for debug provenance.
