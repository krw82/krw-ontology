# Evaluation Gates

Use these gates for web-chat quality checks and E2E harnesses.

## Normal answer gates

```text
Korean Markdown only
no internal terms
no mode/tool/pack names
no budget/fallback/error labels
no generic limitation heading
no FY as primary user-facing label
news-mode answers attempt stock-news event discovery first
news-mode event evidence is ranked by materiality, source quality, and recency
news-mode default current-news window is today, yesterday, within 72 hours, plus last 3 US trading sessions fallback unless the user asks for another period
the answer identifies or uses the current news/report/event before ontology interpretation
substantive event-impact answers identify what is new versus the filing baseline
substantive event-impact answers show the transmission channel into a business or financial variable
substantive event-impact answers separate immediate effects from structural effects when both matter
substantive event-impact answers include the strongest offset, counterargument, or falsification condition
answers start from the most recent available filing unless the user asks for a historical period or specific filing
a newer 10-Q beats an older 10-K for current drivers; a latest 10-K is primary only when no newer 10-Q exists
three follow-up questions present
strong claims traceable or metric-lineage grounded
direct vs related context separated
normal answers, comparison tables, and follow-up questions do not introduce non-covered companies or tickers unless explicitly requested by the user
normal answers are interpretation-first, not raw-number dumps
final answers synthesize news and ontology evidence into one investor interpretation rather than mechanically separating them
tables use interpretation columns unless exact numbers are requested or required for the conclusion
exact numbers are limited to the few figures needed to support, qualify, or correct the conclusion
material numbers are translated into business/investor meaning
final answers do not expose the internal English brief
final answers do not expose stock-news tool names, event IDs, source tiers, recency buckets, or ontology bridge briefs
Tier 5 social/forum/video sources are not used as final-answer evidence unless the user asks about sentiment, social reaction, or online discussion
```

## Tool behavior gates

```text
query_context is the first ontology call after current-event extraction
news-mode research uses stock-news event discovery before query_context
if only low-quality sources appear, run one constrained official/major-source confirmation search before converting the event into ontology search
legacy generic web-search tool references = 0
stock-news event tool names in final answers = 0
MCP research uses an internal English investment brief that preserves user intent, tickers, periods, metrics, and comparison axes
the English investment brief is ontology/MCP-aware and optimized for evidence retrieval, not literal translation
retrieve is legacy fallback only; do not use after sufficient query_context
trace/chain selected roots only; never brute-force every candidate
no repeated equivalent query
no broad full-response first pass
open tool result missing completed runs = 0
tool budget / fallback / retry wording visible to user = 0
overflow or oversized tool result becomes split-and-continue behavior, not a final-answer failure
```

## Financial statement interpretation gates

```text
P&L expenses, OCF/FCF calculation items, investing cash-flow items, and financing cash-flow items are separated
R&D is not added again as a post-FCF cash use
actual M&A cash spending uses acquisition cash-flow lines, not business-combination-related FCF addbacks
company-defined FCF and simple OCF minus capex are distinguished
share repurchases are not framed as pure shareholder return when filings indicate dilution management
capital-allocation inflection timing checks annual periods and latest quarters together
AI/platform/product attribution requires explicit filing support or is framed as an inference
```

## Latency and quality targets

```text
internal leak cases = 0
runtime setting leak cases = 0
budget/error/fallback/overflow wording cases = 0
B-grade cases <= 1 in 30Q smoke
```
