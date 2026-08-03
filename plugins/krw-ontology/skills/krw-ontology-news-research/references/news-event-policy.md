# Feed-First News Event Policy

Use this reference only for `krw-ontology-news-research`.

## 1. Event Selection

Treat a selected KRW Feed issue as the user's chosen research starting point.

```text
selected issue -> read it first -> keep it as the analysis anchor
no selected issue -> find a bounded ticker or company item in KRW Feed
feed lacks a material detail -> use one Yahoo Finance item only as a supplement
```

Never replace the selected issue with a supplementary item. Do not construct a market event from model memory, generic web search, or a loosely related headline.

When several selected issues are present, preserve each issue and identify whether they describe one event, a sequence, or unrelated narratives before forming a conclusion.

If the selected issue is primarily an observed intraday or after-hours price move, route to `krw-ontology-market-move-research` instead of this skill.

## 2. Event Interpretation Ledger

Build this internal ledger before writing the SearchPlan:

```text
reported event: what the feed item literally says
original wording: the exact company, executive, or source wording when present
company baseline: what the company has reported about the affected business condition
financial channel: revenue, volume, pricing, margin, cost, cash flow, balance sheet, dilution, or capital allocation
counterargument: the strongest fact that limits the event's significance
observable condition: what later company commentary, earnings, or disclosure would confirm or weaken the interpretation
```

Do not turn a title or summary into a company fact when the original wording does not support it. Do not require an official source before discussing a selected narrative; frame uncertain claims as scenarios and explain the company baseline that matters if they are directionally right.

## 3. Event-To-Research Patterns

### Company statement or official post

```text
Question: "AMD and Meta say they are co-engineering AI infrastructure. What changes?"

Plan:
- retrieve the company wording about the collaboration
- retrieve the affected product, demand, or infrastructure baseline
- retrieve any direct financial channel or management commentary
- state whether the connection is commercial, technical, or still unquantified
```

Do not infer revenue magnitude from a partnership announcement without a reported contract, demand, backlog, guidance, or management comment.

### Earnings or operating update

```text
Question: "Microsoft reported cloud growth. Does this strengthen the AI thesis?"

Plan:
- retrieve the exact reported metric with its period and scope
- retrieve explanatory management commentary
- retrieve the cost, capex, margin, or cash-flow counterweight when material
- separate reported performance from the forward thesis
```

Do not answer an Azure question with total Microsoft revenue unless the requested segment metric is genuinely unavailable and the answer says only what total-company evidence can support.

### Partnership, contract, acquisition, or product event

```text
Question: "Does this partnership change the investment case?"

Plan:
- identify the stated commitment, counterparty, and product scope
- retrieve the company's existing customer, product, capacity, or capital-allocation baseline
- test the first financial transmission channel
- identify what later disclosure would make the event material
```

### Rumor, future plan, or market narrative

```text
Question: "A report says the company may raise capital. What does that mean?"

Plan:
- treat the report as a scenario, not a filing-presence test
- retrieve cash generation, liquidity, debt, capex, buyback, or dilution baseline
- explain the financial consequence if the scenario occurs
- name the confirmation condition
```

### Multi-company or sector event

```text
Question: "How do recent AI updates differ across NVDA, AMD, and MSFT?"

Plan:
- select only the relevant feed items for each company
- give every company the same comparison axis
- retrieve company baseline evidence for each axis
- compare business channel and evidence strength, not headline volume
```

## 4. Quality Gates

Before making a material conclusion, check:

```text
event identity is clear
event date and company reporting period are not conflated
named company and ticker match the evidence
each exact metric has scope, dimension, unit, and period support
each management-comment claim has direct company wording or direct explanatory support
each causal claim names the first affected financial variable
each comparison uses the same period and axis
each scenario has both a confirming and a weakening condition when evidence permits
```

If a gate fails, narrow the conclusion. Do not use generic risk language as filler and do not manufacture a numeric estimate, management quote, or buy/sell conclusion.

## 5. Final Answer Behavior

Lead with the investment interpretation, then make the evidence and mechanism clear in natural Korean.

Useful phrasing:

```text
이 뉴스가 의미 있으려면 실제로 확인돼야 할 것은 ...입니다.
회사가 직접 언급한 부분은 ...이고, 아직 숫자로 연결되지 않은 부분은 ...입니다.
투자적으로는 매출 자체보다 ... 경로를 먼저 보는 편이 맞습니다.
이 해석은 다음 실적 발표에서 ...이 확인되면 강해지고, ...이면 약해집니다.
```

Avoid source-audit labels, internal tool terminology, and article-replacement summaries.
