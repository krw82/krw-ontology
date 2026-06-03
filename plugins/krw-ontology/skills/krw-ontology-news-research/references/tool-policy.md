# Tool Policy

Use stock-news event tools as the current-news discovery layer. Use KRW ontology tools as the company-specific interpretation layer.

Do not use any legacy generic web-search tool in this skill.

Normal news-mode flow:

```text
stock-news event discovery
-> event extraction and source audit
-> internal English event/investment brief
-> KRW ontology query_context
-> targeted ontology follow-up if needed
-> fused Korean investor interpretation
```

Tool names, MCP names, event IDs, source tiers, recency buckets, routing decisions, and internal briefs are internal only. Do not expose them in normal answers.

## 1. Stock-News Event Tools

These tools are web-search-backed event discovery tools. They do not write the final answer. They return structured article, event, source-audit, and ontology-bridge payloads for internal use.

### route_news_question

Role:

```text
Classifies the user's news question and recommends a bounded news/ontology path.
```

Use when:

```text
the question is broad or ambiguous
the user asks "why is this stock moving?"
the user asks about a sector, macro factor, or market theme
the user asks for comparison
the user asks for valuation/investment opinion based on current news
you need to decide whether company-news or market-news search is the right first call
```

Input shape:

```text
question: string
```

Good input:

```text
question = "GOOGL current pullback reasons and long-term view"
question = "Natural gas price news impact on covered energy companies"
```

Output to use internally:

```text
route_type
depth
reasons
suggested_news_tools
suggested_ontology_tools
max_search_queries
max_reader_calls
resolved entities
```

Do not expose route labels or suggested tools in the final answer.

### resolve_news_entities

Role:

```text
Resolves tickers, company names, sectors, factors, and default recency context against coverage_companies.json.
```

Use when:

```text
ticker/company ambiguity matters
the user uses a company alias
the user asks about a company that may not be covered
the question is tickerless but may map to covered companies
you need to avoid introducing non-covered companies
```

Input shape:

```text
question?: string
tickers?: string[]
companies?: string[]
```

Good inputs:

```text
question = "Alphabet current correction"
tickers = ["GOOGL"]
companies = ["ServiceNow"]
```

Output to use internally:

```text
covered entities
ontology_ticker
confidence
unmatched_terms
sectors
factors
default lookback_hours
trading_sessions_fallback
```

If no covered company resolves, either use `search_market_news_events` for a market/theme question or answer with a coverage caveat only when it changes the interpretation.

### search_company_news_events

Role:

```text
Default first search for company/ticker latest-news questions.
It searches recent company news, ranks articles, extracts event candidates, and returns ontology bridge briefs.
```

Use when:

```text
the user names a ticker or company
the question asks why a specific stock moved
the question asks for latest company-specific news
the event is likely company-specific: earnings, product, M&A, litigation, regulation, guidance, buyback, management change, customer/supplier news
```

Input shape:

```text
question?: string
ticker?: string
company?: string
lookback_hours?: number
max_events?: number
```

Defaults:

```text
lookback_hours = 72 unless user asks another period
max_events = usually 3-5 for normal answers
```

Good inputs:

```text
question = "GOOGL current pullback reasons and long-term view"
ticker = "GOOGL"
lookback_hours = 72
max_events = 5
```

Output to use internally:

```text
resolved entities
query plans
articles
events
audit
ontology_bridge_briefs
```

Use the event with the best mix of materiality, source confidence, recency, and relevance. Do not use every event if the answer only needs the main driver.

### search_market_news_events

Role:

```text
Default first search for tickerless sector, macro, factor, commodity, or cross-company news.
It searches recent market news and maps events back to covered companies when possible.
```

Use when:

```text
the user asks about a sector or macro event
the user asks about commodity prices, rates, regulation, AI capex, consumer demand, tariffs, or other factors
the user asks "which covered companies are affected?"
the user does not name a ticker
company search fails because no covered company was resolved
```

Input shape:

```text
question: string
domains?: string[]
lookback_hours?: number
max_events?: number
```

Good inputs:

```text
question = "latest natural gas price news impact on covered energy companies"
lookback_hours = 72
max_events = 5
```

Use `domains` only when the question or source-quality problem justifies narrowing to specific domains.

Output to use internally:

```text
resolved sectors/factors
articles
events
audit
ontology_bridge_briefs
```

After this tool, choose a bounded covered universe before calling ontology. Do not expand into a whole-catalog scan.

### search_news_by_domain

Role:

```text
Runs a domain-constrained search for official or high-priority source confirmation.
```

Use when:

```text
you need official confirmation
major-news sources conflict
snippet evidence is weak or mostly commentary
the claim depends on exact company wording, SEC/IR material, or a named source
you need to confirm a specific event on company IR, SEC, Reuters, Bloomberg, CNBC, WSJ, AP, or FT
```

Input shape:

```text
query: string
domains: string[]
max_results?: number
```

Good inputs:

```text
query = "Alphabet Q1 2026 earnings AI capex buyback"
domains = ["abc.xyz", "sec.gov"]
max_results = 10

query = "Google DOJ antitrust appeal 2026"
domains = ["reuters.com", "bloomberg.com"]
max_results = 10
```

Use this as a confirmation tool, not as the default first call for every question.

### read_news_sources

Role:

```text
Reads selected URLs through the reader endpoint for verification.
```

Use when:

```text
the snippet is insufficient
the final claim depends on exact wording
the source has guidance, deal terms, accounting details, or management commentary
sources conflict
you need to verify publication date or whether a headline overstates the article
```

Input shape:

```text
urls: string[]
```

Limits:

```text
use 1-3 URLs for normal answers
never read every returned URL
prefer official or high-quality major-news URLs
```

Good input:

```text
urls = [
  "https://investor.example.com/news/...",
  "https://www.reuters.com/..."
]
```

Do not quote long passages. Use the read results to improve accuracy and write concise Korean analysis.

### build_news_timeline

Role:

```text
Sorts provided news event candidates by event date.
```

Use when:

```text
multiple events could explain the stock move
the timing matters for causality
the user asks "what happened first?"
the event sequence affects investment interpretation
```

Input shape:

```text
events: array<object>
```

Good use:

```text
Use events returned from search_company_news_events or search_market_news_events.
Do not manually invent event objects from memory.
```

Output to use internally:

```text
chronological event list
```

Use the timeline to avoid treating older background as today's driver.

### audit_news_sources

Role:

```text
Audits source quality, source-tier mix, weak-source risks, date quality, and recommended reader URLs.
```

Use when:

```text
only low-quality sources appear
sources conflict
the claim is strong or market-moving
the answer may rely on blogs, social, YouTube, Reddit, or commentary
the publication date is unclear
you need to decide whether to run read_news_sources
```

Input shape:

```text
articles: array<object>
```

Good use:

```text
Use articles returned by company/market/domain search.
```

Output to use internally:

```text
source_count
tier_counts
issues
recommended_reader_urls
```

If the audit flags weak-source risk and no stronger source is found, narrow the conclusion instead of adding a generic limitation section.

## 2. Stock-News Tool Selection Patterns

Company latest-news question:

```text
resolve_news_entities if needed
-> search_company_news_events
-> audit_news_sources if source quality is mixed
-> read_news_sources for top official/major source when exact facts matter
-> KRW ontology query_context
```

Tickerless sector/macro question:

```text
route_news_question
-> search_market_news_events
-> resolve_news_entities if coverage boundaries are unclear
-> choose bounded covered universe
-> KRW ontology query_context with selected tickers
```

Conflicting sources:

```text
search_company_news_events or search_market_news_events
-> audit_news_sources
-> search_news_by_domain for official/major confirmation
-> read_news_sources for top source(s)
-> answer narrowly if conflict remains
```

Multi-event timeline:

```text
search_company_news_events
-> build_news_timeline
-> select the event sequence that actually affects the investment question
-> KRW ontology query_context
```

## 3. KRW Ontology Tool Roles

Use these after current-event extraction.

```text
krw_ontology_query_context
Default first ontology call. Use the event-specific internal English investment brief. It returns research state, packs, answerability, and allowed next tools.

krw_ontology_query
Targeted structured follow-up for a specific metric, object type, ticker, period, filing comment, or missing part.

krw_ontology_compare
Explicit comparison only when query_context does not provide enough comparison state or the user asks for a comparison table.

krw_ontology_trace
Evidence lineage for one selected object. Use only when a strong claim needs verification.

krw_ontology_chain
Business/semantic/temporal mechanism around one selected object. Use only when the causal path matters.

krw_ontology_retrieve
Legacy fallback only. Do not use after sufficient query_context.

krw_ontology_company_context
Company orientation only when query_context lacks company-specific vocabulary or the user asks broad company profile context.

krw_ontology_index_context / krw_ontology_catalog / krw_ontology_quality
Debug, audit, or coverage only. Not for normal investor answers.
```

## 4. Response Detail

Do not request `response_detail="full"` in normal web chat. Use compact or ticker-summary output first, then selected trace/chain only when stronger verification is needed.

`response_detail="compact"` is intentional for broad first-pass ontology work.

## 5. Strong Claim Rule

Strong final claims require at least one of:

```text
official/company/SEC evidence
high-quality major-news evidence verified by source timing
direct ontology evidence
metric lineage or selected trace
```

Projection candidates, topic matches, commentary, and source snippets are routes, not final proof.

## 6. User-Facing Boundary

Final answers should say things like:

```text
최근 보도 흐름을 보면...
공식 발표/공시에서 확인되는 내용은...
투자적으로는 이 이벤트가 비용 구조보다 수요/마진 경로에 더 중요합니다.
```

Final answers should not say:

```text
I used search_company_news_events.
Source tier is tier_2_major_news.
The ontology_bridge_brief says...
query_context returned...
The MCP audit says...
```
