---
name: krw-ontology-news-research
description: Use for investment research that starts from a selected KRW Feed issue, a current company or market news item, or a user-provided URL. Read the selected news first, then reuse the normal KRW ontology research method to explain what the event changes for the company in Korean investor language.
---

# KRW Ontology News Research Skill

Use this skill when the investor wants to understand what a selected news item means for a company, sector, or investment thesis.

This is a news-led entry point to normal company research. The selected news supplies the event context; KRW ontology supplies the company baseline, management commentary, financial channel, and investor interpretation.

```text
selected feed issue or news question
-> read the event and original wording
-> build an event-specific investment brief
-> normal ontology research method
-> Korean investor interpretation
```

Do not use this skill for a question that is primarily about an observed intraday or after-hours price move. Route that case to `krw-ontology-market-move-research`.

## 1. Source Contract

Use KRW Feed first; it is the primary stored news source.

```text
selected issue present
-> read that issue with get_feed_context

no selected issue, but ticker or company is present
-> list_feed_items for the named company or companies

feed is incomplete or needs one narrow corroboration
-> one Yahoo Finance news lookup for the same ticker and event
```

Never replace a selected feed issue with a Yahoo Finance item. Yahoo Finance can add context or resolve a material conflict, but the selected issue remains the analysis anchor.

Do not use WebSearch, WebFetch, Stock News MCP, or model memory to create a news event.

Treat every feed item as an observed market narrative until its exact fact is supported by the item's original wording, an official company statement, or company evidence. Do not dismiss a selected item merely because it is not an official statement. Explain what would matter if the narrative is directionally right, then ground that interpretation in company evidence.

When `get_feed_context` returns `krw-feed-context/v2`, read the selected issue's `source_materials` and its `research_packets` before authoring the SearchPlan. The original X wording and a verified company source outrank any generated card or packet. Treat image extraction as observed text with its own uncertainty; if it is marked failed, skipped, or unreadable, do not reconstruct it from memory.

When no relevant stored item exists, say that the available feed does not show a material recent item and continue from the latest company baseline only if that still answers the question.

## 2. Event Brief

Before the first company-research call, convert the Korean question and selected item into one concise internal English investment brief. Preserve:

```text
user intent
selected issue title, summary, original wording, image extraction when present, and publication time
internal research packet as a routing hint only, never as independent evidence
covered tickers and company names
event type and claimed change
event date versus company reporting period
business or financial channel
exact metrics, periods, and comparison axes
uncertainty, counterargument, and observable confirmation condition
```

Keep these layers separate internally:

```text
news fact = what the selected item literally reports
company baseline = what company evidence says about the relevant business or financial condition
inference = how the event could change revenue, margin, cash flow, balance sheet, risk, or capital allocation
```

Do not expose the English brief, source IDs, tool names, or internal layer labels in normal answers.

## 3. Research Workflow

Follow the normal research method after the event brief is ready.

```text
1. Resolve the selected event and covered tickers from KRW Feed.
2. Identify the one or more propositions that the investor actually needs answered.
3. Author one complete SearchPlan v2 with atomic clauses before the first ontology call.
4. Call query_context with exactly {search_plan}.
5. Read answerability, clause coverage, evidence units, computed values, calculation coverage, missing parts, recommended actions, continuation, and warnings.
6. Follow up only for a material named gap. Never repeat the same broad plan.
7. Write one evidence-forward Korean investor interpretation.
```

Apply the matching normal-research pattern:

```text
exact number or segment metric
-> separate metric clause with scope, dimension, period, and comparison axis

company wording or management commentary
-> require an EvidenceQuote or directly supported explanatory company text

event impact
-> event -> business mechanism -> affected financial line -> investor meaning

comparison
-> require the same period, basis, and question axis for every company

scenario or thesis
-> separate the condition that supports the view from the condition that breaks it
```

Use exact numbers only when metric lineage, dimension, unit, period, and calculation basis are aligned. Do not substitute total-company revenue for a named segment metric.

For a strong statement about what management said, prefer direct company wording. If no direct wording is found, do not write a synthetic quote or imply that management made the point.

Read `references/news-event-policy.md` for event interpretation rules and `references/tool-policy.md` for source and research-tool order.

## 4. Evidence and Recovery Rules

Do not write a substantive conclusion from a feed headline, a feed summary, a status field, or a route score alone.

Before making a material conclusion, establish all applicable layers:

```text
event layer: the selected item supports what happened or what is being discussed
company layer: company evidence supports the relevant baseline or management commentary
financial layer: the connection to revenue, margin, cash flow, balance sheet, or risk is direct or clearly labelled as inference
```

If one required clause is open, use the narrowest targeted follow-up named by the research result. If an exact metric, company quote, comparison axis, or scenario condition is still not supported, omit that claim or state the remaining question naturally. Do not fill it with generic risk-factor language.

Do not claim that an event is priced in, estimate revisions are certain, or a stock should be bought, sold, or held from news and filings alone.

## 4A. Direct-Evidence Conclusion Gate

Before using a conclusion-shaped phrase about the covered company, require an explicit company-specific path in the selected source and retrieved company evidence.

Conclusion-shaped phrases include `positive`, `negative`, `demand benefit`, `revenue upside`, `margin pressure`, `cash-flow impact`, `competitive strengthening`, `risk-sharing`, `purchase commitment`, `binding commitment`, and equivalent Korean wording.

```text
product launch / partnership / third-party report / competitor incident / macro or policy event
without a directly evidenced company path
-> do not call it positive or negative for the company
-> do not create a financial base-upside-downside scenario
-> state what is confirmed, what is not established, and the observable confirmation signal
```

Apply these rules:

- An announcement, collaboration, deployment, initiative, pilot, or memorandum is not a purchase order, contract, binding commitment, customer adoption, revenue, margin, or cash-flow evidence unless the original source explicitly says so.
- Do not infer a customer purchase amount, GPU count, shipment, data-center demand, product commercial status, partner role, competitive advantage, supply-chain benefit, or risk-sharing structure from a generic announcement.
- Do not convert a third-party incident into a covered-company risk unless retrieved material establishes exposure to that company.
- Historical results provide baseline context only. They do not prove that a new event will repeat the same revenue, margin, or demand effect.
- If direct evidence is absent, use exactly this reasoning shape: `확인된 사실` -> `회사 영향은 아직 확인되지 않음` -> `다음 확인 신호`.
- An inference may be included only when it is labelled as conditional and every link in the path is directly supported. If any link is missing, omit the financial inference.

## 4B. Korean Financial Terminology and Unit Gate

Preserve source units and financial terminology exactly before writing Korean prose.

- Translate `revenue` as `매출`, never `수입`.
- Prefer the original notation for material figures: write `$500B`, `$90 billion`, or `$1.75T` rather than performing an unnecessary Korean conversion.
- If a Korean unit conversion is necessary, verify it mechanically: `$1B = 10억 달러`, `$1T = 1조 달러`. Never silently change a magnitude, currency, period, or basis.
- Preserve whether a number is revenue, spending, notional value, capacity, contracts, shipment volume, or a non-binding initiative. Do not relabel one as another.
- Before publishing an exact figure, verify unit, currency, period, scope, and whether it is reported or derived. Omit it if any of these are uncertain.
- Do not include agent-progress narration such as `자료를 확보했습니다`, `분석을 작성합니다`, `Let me`, tool availability, or retrieval commentary in the user-facing answer.

## 5. Answer Shape

Write concise Korean investor prose. Adapt the structure to the question, but make this reasoning visible when relevant:

```text
what the selected news changes
what the company has actually said or reported
how the event reaches revenue, margin, cash flow, risk, or capital allocation
what supports the interpretation and what would weaken it
```

Do not turn the answer into an article summary or a source-audit checklist. Do not mechanically split the answer into "news evidence" and "filing evidence" sections.

Use plain Korean. Put the investor judgment early. Keep facts, calculations, inferences, and scenarios distinct in the wording even when the prose is fused.

Normal answers end with exactly three concise Korean follow-up questions under:

```text
### 이어서 볼 질문
```

At least one follow-up must let the user examine the conditions that would strengthen or break the current interpretation.

## 6. References

Load only what the question needs:

```text
references/news-event-policy.md
- Feed-first event handling, event-to-company interpretation, and quality gates.

references/tool-policy.md
- KRW Feed, bounded Yahoo Finance, and ontology research-tool roles.

references/feed-research-packet-contract.md
- `krw-feed-context/v2` source-material and research-packet interpretation rules.

references/query-context-contract.md
- SearchPlan v2 and ResearchState handling.

references/evidence-to-analyst-synthesis.md
- Evidence-forward Korean investor interpretation.

references/financial-statement-interpretation.md
- Cash flow, capex, M&A, buybacks, SBC, and accounting guardrails.

references/period-and-latest-policy.md
- Latest filing precedence and event-date versus reporting-period handling.

references/trace-chain-policy.md
- Selective verification and business-mechanism expansion.

references/forbidden-user-facing-language.md
- Internal terms that must not appear in normal answers.
```
