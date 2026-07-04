# Investment Decision Brief Rules

Use this reference only to convert vague buy/sell/hold-style questions, including Korean phrasing, into an internal English investment brief before KRW ontology MCP calls.

This file is primarily an internal English brief rule.

It also defines a detailed but scannable final report shape, but only for the triggered buy/sell/hold-style questions in this reference.

Do not let this reference change ordinary research questions.

## Trigger Scope

Apply only when the user's main intent is a vague investment decision, timing, or holding question:

```text
지금 사?
사도 돼?
사 말아?
지금 들어가도 돼?
팔아야 해?
보유해도 돼?
장기적으로 괜찮아?
이 주식 괜찮아?
좋은 회사야?
지금 매수?
물타도 돼?
비중 줄여?
손절해야 해?
```

Do not apply this reference when the user asks a clearer normal research question:

```text
metric/numeric series
business model or company overview
risk/thesis/scenario analysis
direct exposure check
comparison
contract/event/factual lookup
sector/global/macro discovery
```

For normal research questions, keep the normal KRW ontology classification and normal internal English brief.

## Core Action

Do not translate the user's question literally.

Convert it into a concise ontology-friendly English investment brief that asks what filing evidence is needed to judge the decision frame.

The internal brief should:

```text
- preserve ticker, company name, period, price, valuation, portfolio, or comparison assumptions from the user
- follow the web runtime Company filing anchor when provided
- use latest available filing evidence first
- treat the latest 10-Q as the current driver when available
- use the latest 10-K as annual business baseline when a newer 10-Q exists
- optimize for evidence retrieval, not final prose
- avoid definitive buy/sell recommendations, target prices, or ratings
```

Do not expose the internal English brief in the final English answer.

For investment-decision answers, the final judgment must be anchored on the web runtime Company filing anchor when provided. Start from `Current driver`, use `Annual baseline` only for business mix/annual trend/risk baseline, and use `Historical context` only for cycle comparison or change over time. If a newer 10-Q exists after the latest 10-K, use the 10-Q as the current driver and use the 10-K only as annual baseline/business mix context. Do not say the latest 10-Q needs to be checked if it was available through tools or provided as the Current driver.

## Evidence Dimensions For The Brief

Bias the brief toward decision-relevant filing evidence:

```text
revenue durability
demand indicators
segment performance
margin direction
cash-flow conversion
FCF quality
capex and reinvestment burden
debt, liquidity, and balance sheet risk
share count, dilution, SBC, and buybacks
capital allocation
management commentary and filing notes
material risks that could strengthen or weaken the thesis
```

Use numbers internally to verify the judgment, but the final answer should emphasize investor interpretation unless a number materially changes the decision frame.

## Brief Templates

Use one of these patterns as the internal English brief. Adjust only for the user's ticker/company, period, and explicit assumptions.

### New Buy / Entry Timing

For:

```text
지금 사?
사도 돼?
지금 들어가도 돼?
```

Internal brief:

```text
{Company} current new-buy decision frame: latest available filing evidence on growth durability, demand indicators, segment performance, margin direction, cash-flow conversion, capex and reinvestment burden, balance sheet and liquidity risk, dilution/SBC, capital allocation, and material risks. Assess what supports buying now versus waiting. Do not produce a definitive recommendation, rating, or target price.
```

### Buy Versus Wait

For:

```text
사 말아?
```

Internal brief:

```text
{Company} buy-versus-wait decision frame: filing-supported positives, negatives, current drivers, margin and cash-flow durability, capex burden, balance sheet risk, capital allocation, dilution/SBC, and material risks. Identify which filing conditions support a new buy, waiting, or avoiding the stock. Do not produce a definitive recommendation, rating, or target price.
```

### Hold

For:

```text
보유해도 돼?
계속 들고 가도 돼?
```

Internal brief:

```text
{Company} hold decision frame: whether latest available filing evidence shows the investment thesis is intact, improving, or deteriorating. Focus on revenue durability, demand indicators, segment performance, margin direction, cash-flow conversion, capex burden, balance sheet/liquidity, dilution/SBC, capital allocation, and material risks.
```

### Sell Risk

For:

```text
팔아야 해?
손절해야 해?
```

Internal brief:

```text
{Company} sell-risk decision frame: filing evidence for thesis break, growth slowdown, demand deterioration, margin compression, cash-flow deterioration, leverage or liquidity pressure, dilution, capital allocation deterioration, or worsening material risk exposure. Do not produce a definitive sell recommendation, rating, or target price.
```

### Long-Term Quality

For:

```text
장기적으로 괜찮아?
장투해도 돼?
```

Internal brief:

```text
{Company} long-term investment quality frame: business model durability, revenue visibility, segment quality, margin structure, reinvestment runway, cash generation, capital allocation discipline, balance sheet resilience, dilution/SBC, and structural risks. Assess whether filing evidence supports long-term quality versus watch-first caution.
```

### Business Quality Versus Stock Decision

For:

```text
좋은 회사야?
이 주식 괜찮아?
```

Internal brief:

```text
{Company} business quality versus stock decision frame: latest available filing evidence on durable growth, segment economics, margin quality, cash-flow conversion, reinvestment needs, balance sheet risk, dilution/SBC, capital allocation, and risks that could make the business high quality but the stock less attractive, or vice versa.
```

## Final Answer Boundary

After using the internal brief, follow the normal KRW ontology research answer rules.

For triggered buy/sell/hold-style questions only, write a detailed but scannable compact investment-decision report in English.

Do not make it a short quick answer unless the user explicitly asks for "짧게", "한 줄로", or "간단히".

The first visible sentence must be the practical filing-supported judgment.
This opening judgment sentence is outside the heading sequence and must appear before any Markdown heading, title, table, caveat, or disclaimer.

Good opening:

```text
My view: a new buy still looks premature; existing holders can continue holding.
```

Do not start with a disclaimer such as "공시자료만으로는 판단할 수 없습니다" unless the user explicitly asks for a target price, personalized portfolio instruction, or exact valuation.

Use standardized decision labels:

```text
Buy candidate
Worth staged interest
Wait for confirmation
Better to wait
Holdable
Consider trimming
Risk outweighs reward
Better to avoid
```

Exact output contract for triggered buy/sell/hold-style questions:

Start with the opening `My view:` sentence, then use the exact English heading order below. The heading order begins after the opening judgment sentence. Do not rename, omit, merge, or reorder these headings unless the user explicitly asks for a short answer.

```text
My view: {filing-supported decision}. {new buyer and/or holder split judgment}.

# {Ticker or Company} Investment Decision Summary

## Decision Label
| Investor type | Decision | Reason |
|---|---|---|
| New buyer | {standardized label} | {one-line filing-supported reason} |
| Existing holder | {standardized label} | {one-line filing-supported reason} |

## Decision Dashboard
| Area | Judgment | Reason |
|---|---|---|
| Growth/demand | {positive/neutral/risk interpretation} | {filing-supported reason, not a raw number dump} |
| Margin/profitability | {positive/neutral/risk interpretation} | {filing-supported reason, not a raw number dump} |
| Cash flow/investment burden | {positive/neutral/risk interpretation} | {filing-supported reason, not a raw number dump} |
| Financial stability/dilution | {positive/neutral/risk interpretation} | {filing-supported reason, not a raw number dump} |
| Key risk | {positive/neutral/risk interpretation} | {filing-supported reason, not a raw number dump} |

## Why I See It This Way
Separate positive filing evidence from risk or confirmation points in English prose.

## What Would Change The View
Explain what future filing/earnings evidence would make the view more positive or more negative. Use qualitative condition language unless the user supplied a threshold or filings/guidance explicitly provide one.

## Final View
Separate good company, good stock, and good entry timing.

## Next Questions To Dig Into
End with exactly 3 action-oriented English follow-up prompts.
```

Do not apply this report shape to ordinary research questions.

For triggered buy/sell/hold-style questions, do not omit `Decision Label`, `Decision Dashboard`, `Why I See It This Way`, `What Would Change The View`, `Final View`, or `Next Questions To Dig Into` unless the user explicitly asks for a short answer.

Never start the final answer with `#`, `##`, a table, or a section label. The opening sentence must be visible first:

```text
My view: ...
```

Do not infer whether the user is in profit, loss, take-profit, or stop-loss state unless the user states cost basis, return, purchase price, or position status. For "Should I cut losses?" or "손절할까?" questions, answer from filing-supported thesis-break risk and hold/sell risk, not assumed profit/loss.

Good opening for stop-loss or cut-loss questions:

```text
My view: based on filing evidence alone, cutting losses still looks premature. Existing holders can keep holding until there is clearer evidence that the operating trend is breaking.
```

In the report, prefer qualitative investment interpretation over raw numeric tables. Use numbers only when they materially change the judgment. The dashboard should read like an investment judgment table, not a filing data table; avoid columns that are primarily raw figures such as "공시 근거" packed with numbers.

Do not invent numeric thresholds, percentage thresholds, valuation bands, time horizons, checklist cutoffs, allocation sizes, buy levels, sell levels, stop-loss levels, margin thresholds, growth thresholds, or "N out of M conditions" rules unless the user supplied them or the filing/guidance explicitly supports them. Use qualitative condition language instead.

Bad threshold:

```text
HBM 수주 가시성이 12개월 이상 확보되면 추가 매수.
Cut losses if margin falls below 60%.
조건 중 2개 이상이 발생하면 비중 축소.
```

Good condition language:

```text
The view becomes more positive if HBM order visibility improves and evidence builds that the high-margin product mix is holding.
The thesis-break risk rises if margin recovery stalls and demand improvement does not translate into pricing or inventory improvement.
```

For these triggered questions, the three final follow-ups must be action-oriented decision continuations, not generic research questions or raw metric prompts. Render them as a numbered Markdown list using `1.`, `2.`, `3.`. Each follow-up must stay on the same company and include one of these decision-action frames: `new-buy checklist`, `sell signals`, `thesis-break judgment`, `hold conditions`, `risk improvement/deterioration conditions`, or `view-change conditions`.

Keep follow-ups focused on the same company and the user's original decision type. Do not introduce new peer companies, tickers, or comparison prompts unless the user explicitly asked for comparison or named peers.

Good follow-ups:

```text
Should we build a {Ticker} new-buy checklist?
Should we look only at {Ticker} sell signals for existing holders?
Should we isolate the signals that would make a {Ticker} thesis-break judgment stronger?
Should we define what a new {Ticker} buyer should confirm before entering?
Should we separate the conditions that would improve versus worsen {Ticker}'s risk profile?
```

Bad follow-ups:

```text
Should we compare {Ticker} vs {Peer} right now?
How should we think about {Ticker}'s AI monetization?
What is the margin impact?
What are the risks?
What is {Ticker}'s HBM revenue mix?
What is {Ticker}'s capex level?
What are {Ticker}'s cash and debt balances?
```

The final English answer may use clear non-prescriptive judgment labels such as:

```text
Can be viewed as a buy candidate
Better to wait for confirmation than buy aggressively
Existing holders can continue holding
New entrants are closer to wait-and-see
Better suited to long-term growth investors
Still burdensome for cash-flow-focused investors
Weak basis for a short-term rebound call
The filings do not yet show a clear thesis break
```

Avoid:

```text
Definitely buy
Definitely sell
Buy it now
You must sell
The target price is X
Formal buy/hold/sell rating
Personalized investment advice
```
