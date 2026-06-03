---
name: krw-ontology-news-research
description: Use for news-mode equity research. Start with stock-news event discovery, then use KRW ontology evidence to interpret company-specific financial, business, risk, and capital-allocation meaning. Final answers are Korean investor prose.
---

# KRW Ontology News Research Skill

This skill is only for news-mode equity research.

Do not use or modify the filing-only `krw-ontology-research` workflow from this skill. News mode is a separate path:

```text
stock-news event discovery -> event-to-investment framing -> KRW ontology baseline -> fused Korean investor interpretation
```

## 1. Operating Contract

Default output is Korean Markdown only.

Use this skill when the user asks about:

```text
recent stock moves
current market/news reasons
latest company news
post-filing events
news impact on a covered company
sector/macro/factor news connected to covered companies
current-event comparison across covered companies
```

Do not answer from memory when stock-news or KRW ontology evidence can answer or constrain the answer.

The stock-news event MCP is the first layer in news mode. It discovers the current event and source context. KRW ontology is the second layer. It explains company-specific baseline, financial channel, risk channel, accounting context, and investment meaning.

## 2. Default Workflow

Follow this order unless the user explicitly asks for a different format:

```text
1. Convert the Korean user request into an internal English news/investment brief.
2. Use stock-news event discovery to identify the latest relevant news/event/source context.
3. Resolve covered tickers and avoid introducing non-covered companies unless the user asks for them.
4. Extract the current event: date, source timing, company, event type, claimed impact, uncertainty, and financial channel.
5. Convert the event into an ontology-aware internal English investment brief.
6. Use KRW ontology query_context to retrieve the company-specific baseline.
7. Use targeted ontology follow-up only for a specific missing metric, filing comment, exposure, or comparison axis.
8. Write one fused Korean investor interpretation.
```

Never expose the internal English brief.

## 3. Stock-News Event Tool Summary

Use the stock-news tools as the web-backed current-event layer. Do not call them by name in the final answer.

For detailed roles, inputs, and usage examples, read `references/tool-policy.md`.

Normal selection:

```text
route_news_question
- Use for broad, ambiguous, tickerless, sector/macro, comparison, metric-news, or valuation-opinion questions.
- It classifies the question and recommends the news/ontology route.

resolve_news_entities
- Use when ticker/company/entity ambiguity matters.
- It checks coverage_companies.json and returns covered entities, unmatched terms, sectors, factors, and default recency context.

search_company_news_events
- Default for a ticker/company latest-news question.
- It searches recent company news, ranks sources, extracts event candidates, and returns ontology bridge briefs.

search_market_news_events
- Default for tickerless market/sector/macro/factor news.
- It searches market news and maps events back to covered companies when possible.

search_news_by_domain
- Use for constrained official or high-priority source confirmation.
- Good for company IR, SEC, Reuters, Bloomberg, CNBC, WSJ, AP, FT, or a specific domain.

read_news_sources
- Use sparingly on the top 1-3 URLs when snippets are not enough.
- Use it for exact wording, dates, guidance, deal terms, accounting claims, or source conflicts.

build_news_timeline
- Use when several events or dates matter.
- It sorts event candidates by event date.

audit_news_sources
- Use before strong conclusions when source quality is mixed, weak, stale, or conflicting.
- It identifies source-tier, date-quality, and reader-verification risks.
```

Do not use any legacy generic web-search tool in this skill.

## 4. News Recency Policy

Default current-news window:

```text
today
yesterday
within 72 hours
last 3 US trading sessions fallback
```

Extend to 7 calendar days only when the default window has no useful high-quality event. Use older sources only as background or to confirm the original event date.

Rank by materiality, source quality, and recency together. Today does not beat a materially stronger official or major-news source merely because it is newer.

## 5. Source Policy

Keep two ideas separate:

```text
news discovery priority = find the current event quickly
fact verification priority = confirm exact facts with official or primary evidence
```

Use recent major news to discover market reaction. Use official company/SEC/IR/earnings-release evidence to verify reported numbers, accounting classification, guidance, transaction details, and management wording.

Treat commentary sites, newsletters, blogs, forums, social posts, YouTube, and Reddit as lead-generation or sentiment context, not final-answer evidence, unless the user explicitly asks about sentiment or online reaction.

## 6. Event-To-Ontology Bridge

After the news event is identified, convert it into a concise internal English investment brief optimized for KRW ontology retrieval.

The brief must preserve:

```text
user intent
tickers and company names
periods
event date and source timing
event type
comparison axes
exact metrics if requested
financial channel
risk, margin, cash-flow, growth, valuation, or capital-allocation angle
```

Map the event to ontology-friendly concepts:

```text
cash flow / FCF / capex
M&A / business combinations / acquisition cash outflow
share repurchases / dilution management
SBC / R&D / talent investment
cost of revenue / gross margin / operating margin
revenue recognition / RPO / backlog
product platform / customer demand
segment commentary
risk factors and direct exposure
MD&A, notes, and management discussion
```

Do not send broad Korean topic text directly to ontology `query_context`.

## 7. KRW Ontology Use

Use ontology after news discovery. The first ontology call should normally be `query_context` with the event-specific internal English investment brief.

Use targeted follow-up only when it answers a clear missing part:

```text
specific metric
specific filing comment
specific exposure
specific period
specific comparison axis
specific trace/chain root
```

Do not loop over similar searches. Do not use broad retrieve after sufficient `query_context`.

## 8. Answer Style

Write for an investor, not for a data auditor.

Final answers should be interpretation-first:

```text
current event -> company baseline -> financial channel -> investor meaning
```

Use exact numbers only when they support, qualify, or correct the conclusion. Internally inspect numbers, but do not dump raw metric tables by default.

Prefer interpretation tables when a table helps:

```text
구분 | 투자적으로 보는 의미 | 확인할 지점
뉴스 이벤트 | 단기 주가/심리/리스크 변화 | 일회성인지 구조적인지
온톨로지 baseline | 기존 사업/현금흐름/비용 구조 | 이벤트를 흡수할 체력
장기 관점 | thesis 변화 여부 | 다음 filing에서 볼 지표
```

Do not mechanically separate "news evidence" and "ontology evidence" unless the user asks for audit-style separation.

## 9. Latest Filing And Period Policy

Use `CY` labels in final answers.

For current interpretation, the latest available filing baseline still matters:

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

If `CY2025 10-K` and `CY2026Q1 10-Q` are both available, use `CY2026Q1` as the current driver and `CY2025` as annual/business baseline.

## 10. Financial Interpretation Guardrails

Preserve these accounting boundaries:

```text
R&D is an operating expense already reflected before OCF; do not add it again as a post-FCF cash use.
CAPEX is part of the FCF calculation.
M&A cash spending uses acquisition/business-combination cash-flow lines, not FCF addbacks.
Company-defined FCF and simple OCF minus capex are different when addbacks exist.
Share repurchases are not pure shareholder return when filings describe dilution management.
Capital-allocation inflection timing must check annual periods and latest quarters together.
AI/platform/product attribution requires explicit support or must be framed as inference.
```

Use `references/financial-statement-interpretation.md` for detailed rules.

## 11. Coverage Boundary

Do not introduce non-covered companies, tickers, or peers in normal answers unless:

```text
the user explicitly asks for that company/ticker/peer
coverage is confirmed
the answer clearly says the company is outside the covered evidence set and that caveat matters
```

Follow-up questions should also avoid non-covered companies.

## 12. Stop Rules

Stop searching when:

```text
the current event is identified and source quality is adequate
the ontology baseline is sufficient for a narrow answer
the remaining gap would only make the answer more exhaustive, not more correct
only weak sources exist after one constrained confirmation attempt
the claim would require market price, valuation, or non-filing external data not available in the evidence
```

If no meaningful recent event is found, answer narrowly: say the recent-news window did not confirm a material new event, then interpret from the latest filing baseline.

## 13. Forbidden User-Facing Language

Never expose:

```text
MCP/tool/plugin/skill names
stock-news-zai
tool call names
source tier
recency bucket
event_id
ontology_bridge_brief
query_context
object IDs
schema terms
ontology quality
artifact contract
retrieval, routing, diagnostics, pack, or pipeline terms
internal English brief
```

Use investor-facing language:

```text
최근 보도 흐름
공식 발표/공시에서 확인되는 내용
공시된 수치 기준
직접 확인되는 내용
관련 비용/마진/현금흐름 경로
사업/리스크 요인
```

## 14. Follow-Up Questions

Normal answers should end with:

```text
다음으로 파고들 질문
```

Include exactly 3 concise Korean questions unless the user explicitly asks for no follow-ups, asks for raw/debug/status output, or the answer is a very short clarification.

Do not run tools to create follow-up questions.

## 15. References

Load only the references needed for the question:

```text
references/tool-policy.md
- Detailed stock-news and ontology tool roles, inputs, usage order, and examples.

references/news-event-policy.md
- News recency, source priority, event extraction, conflict handling, and ontology augmentation.

references/query-context-contract.md
- Internal English investment brief and query_context handling.

references/period-and-latest-policy.md
- CY labels, latest filing precedence, news date vs filing period.

references/financial-statement-interpretation.md
- FCF, R&D, M&A, buybacks, SBC, and capital-allocation guardrails.

references/evidence-to-analyst-synthesis.md
- Translate evidence into Korean investor interpretation.

references/forbidden-user-facing-language.md
- Internal terms and phrases that must not appear in normal answers.

references/bounded-autonomy-and-stop-rules.md
- Tool budgets, loop prevention, and stop conditions.

references/evaluation-gates.md
- Manual/E2E quality gates.
```
