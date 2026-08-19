---
name: krw-ontology-market-move-research
description: Use when the user asks why a covered stock moved recently or whether a move matters. Start from selected or ticker-matched KRW Feed issues, use one bounded current-market-news cross-check when needed, and connect to filing research only when the question requires it.
---

# KRW Feed Market-Move Research

Use this skill for stock-move interpretation. It is not generic news summarization and it does not treat a recent feed post as proven price causality.

```text
observed move or selected feed issue
-> stored X-feed context
-> candidate explanation and uncertainty
-> optional 8-K/6-K or company research follow-up
```

## Source Boundary

- Use KRW Feed first for event/news context. Use at most one current-market-news lookup for the same ticker only to cross-check or fill a material feed coverage gap.
- If price, volume, or session context is already supplied by the app, treat it as an observed market fact.
- A separate quote/identity tool may be used to identify the observed price move when it is available. It must not be used to replace feed-first event context.
- Feed posts and supplementary market reports remain market observations, not company-confirmed disclosures.

## Tool Workflow

1. Prefer selected issue IDs from the app. Call `get_feed_context` for the selected issues.
2. Without selected issues, call `list_feed_items` for the active ticker, then use `get_feed_context` only for the few most relevant items.
3. When a cross-check is needed, call `get_yahoo_finance_news` once for the same ticker. Keep its findings as supplementary market context and never let it replace the selected feed issue.
4. Separate candidate explanations into company-specific, sector/market-driven, mixed, or unknown.
5. Compare timing and subject matter, but never state that a post or report caused the move solely because it appeared nearby.
6. If the user asks whether the issue affects a durable thesis, financials, risk, or a company disclosure, continue into filing-grounded research. Otherwise give a bounded first-pass interpretation.

## Default Answer

Explain:

```text
how to read the observed move
what the selected or matched feed items actually discuss
which part is company-specific versus market/sector context
what remains unproven
what condition would justify deeper filing analysis
```

Do not produce entry prices, target prices, stop-loss levels, or personalized buy/sell instructions.

## Final Output

Write Korean Markdown directly for the investor. End with exactly three concise numbered follow-up questions under `### 이어서 볼 질문` unless the user asks for no follow-ups.

At least one follow-up must invite a check of whether the market narrative is confirmed or contradicted by company comments, financial impact, or closely timed filings. Do not expose X, MCP, tool names, issue IDs, raw payloads, or internal retrieval details unless the user explicitly asks for debug provenance.
