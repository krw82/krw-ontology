---
name: krw-ontology-idea-generation
description: Use when the user asks to discover, screen, rank, filter, or prioritize covered public-equity research candidates by an investment theme, business driver, financial condition, risk factor, beneficiary pathway, or user-provided ticker list. Produces filing-grounded research priorities and false-positive rejection, not final buy/sell recommendations.
---

# KRW Ontology Idea Generation

## 1. Purpose

Use this skill to turn a broad investment question into a bounded, filing-grounded research queue.

```text
investment theme or screen
-> explicit screen definition
-> covered-company candidate discovery
-> filing evidence validation, not keyword promotion
-> financial pathway and rejection risk
-> why this deserves research attention now
-> A / B / C / Reject research priority
-> next deep-research question
```

This skill finds what deserves deeper research. It does not decide what the user should buy or sell.

```text
Idea Generation = candidate discovery and research prioritization
KRW Ontology Research = deep analysis of a selected company or comparison
```

Default visible output:

```text
Korean Markdown only.
```

## 2. Shared Research Contract

Before research, read and follow these shared references from the filing-research skill:

```text
../krw-ontology-research/references/tool-policy.md
../krw-ontology-research/references/query-context-contract.md
../krw-ontology-research/references/period-and-latest-policy.md
../krw-ontology-research/references/trace-chain-policy.md
../krw-ontology-research/references/evidence-to-analyst-synthesis.md
../krw-ontology-research/references/financial-statement-interpretation.md
../krw-ontology-research/references/plain-korean-investor-language.md
../krw-ontology-research/references/forbidden-user-facing-language.md
```

These shared rules govern:

```text
internal English investment briefs
model-authored SearchPlan v2 before query_context
v3 global-spine candidate routing
MCP `filing_document_roles` for candidate validation current_driver / annual_baseline
latest 10-Q as current driver
latest 10-K as annual baseline
selected trace/chain verification
evidence -> mechanism -> financial meaning -> investor interpretation
plain Korean final prose
no internal tool/schema/index language in user-visible answers
```

Do not load or apply:

```text
../krw-ontology-research/references/investment-decision-questions.md
the buy/sell/hold report template
target-price or final recommendation logic
the normal exactly-three follow-up requirement
```

## 3. Internal Idea-Screen Brief

Before calling KRW ontology tools, convert the user's request into a concise internal English idea-screen brief.

If the user did not provide a usable screen, do not invent one silently.
A usable screen needs at least one concrete axis:

```text
business driver, product/channel exposure, financial condition, risk condition,
event type, company type, named ticker list, or explicit exclusion
```

For conditionless requests such as "good stocks", "companies to enter now", or
"what should I buy", first ask the user to choose a screen by offering 3-5
ready-to-send examples. Do not run broad discovery just to fill the answer.

The brief must preserve:

```text
theme or requested condition
named companies or ticker list
requested beneficiary or risk pathway
time context
financial channels
desired exclusions
```

Add evidence axes that help distinguish real exposure from a thematic false positive:

```text
direct business activity or customer demand exposure
latest management commentary
orders, backlog, pricing, volume, or contract evidence
revenue, margin, cash-flow, or balance-sheet pathway
capex, cost, financing, or execution burden
recent strengthening or weakening
first rejection evidence
```

The brief must also define the screen before discovery:

```text
Screen:
what kind of company should qualify

Evidence required:
what filing evidence would prove direct exposure

Financial path:
how the driver could reach revenue, margin, cash flow, capex, or balance sheet

Reject if:
what evidence would make the candidate a false positive

Why now:
what recent filing evidence, change, pressure, or catalyst makes the candidate worth prioritizing now
```

Do not treat "why now" as stock timing. It means research timing: the latest
filing made the exposure, pathway, burden, or risk more important to investigate.

Do not expose the internal English brief.

Example:

```text
User:
AI 데이터센터 전력 수요 수혜 기업을 찾아줘.

Internal brief:
Covered-company idea screen for AI data-center power demand:
Screen = companies with direct operating exposure to AI/data-center power demand.
Evidence required = management commentary, customer demand, orders, backlog,
capacity, or contract evidence.
Financial path = demand -> orders/pricing/volume/capacity -> revenue/margin/cash flow.
Reject if = only generic AI or data-center language with no economic linkage.
Why now = latest filing shows a stronger or weaker conversion signal worth
prioritizing for deeper research.
```

## 4. Request Types

Classify the request before research:

```text
A. Theme or beneficiary discovery
B. Financial or business-condition screen
C. User-provided candidate-list triage
D. False-positive or weak-exposure filtering
E. User-provided URL idea-screen extraction
```

This skill is not for:

```text
deep analysis of one already-selected company
ordinary company overview
buy/sell/hold timing judgment
target price or fair value
current market-price screening
consensus estimate revisions
institutional positioning, flows, short interest, or borrow
portfolio sizing or benchmark construction
```

Route those requests to the appropriate workflow instead of inventing unavailable inputs.

## 5. Candidate Discovery

Before discovery, read and apply:

```text
references/search-order.md
```

Use a three-phase workflow:

```text
1. Screen definition:
   Translate the user's broad question into qualification, evidence, financial path, rejection, and why-now criteria.

2. Candidate discovery:
   Use a model-authored SearchPlan v2 with universe="covered" or the user's ticker list to find enough covered candidates for the requested screen.
   Do not finalize ranking in this phase.

3. Candidate validation:
   Verify each material candidate on the same axes before assigning A / B / C / Reject.
```

### 5.1 Theme or condition without a ticker list

Only use this path when the user has provided a concrete theme, driver,
financial condition, risk condition, company type, event type, or exclusion.

Start with one complete model-authored v3 global-spine discovery plan:

```text
search_plan={
  question: original user screen,
  intent: idea_screen,
  universe: "covered",
  clauses: atomic qualification, direct-exposure, financial-path, rejection, and why-now propositions,
  limit_tickers: sized to the requested candidate breadth,
  limit_results: sized to cover every required clause across the candidate pool
}
query_context(search_plan=search_plan)
```

Treat `resolved_scope.resolved_tickers` as routes to verify, not as final ranked ideas. Use `clause_coverage` and `evidence_units` to determine which candidates actually satisfy the screen.

Do not:

```text
run whole-catalog scans
rank more companies than the available evidence can compare consistently
repeat the same broad query_context with slightly different wording
advance a candidate from keyword relevance alone
```

### 5.2 User-provided URL context

Use this path when the user attaches a URL and asks the discovery workflow to
turn the article, event, or external source into covered-company research
candidates.

Before searching, read and follow:

```text
references/url-screen-compression.md
```

This reference owns URL screen compression, the boundary between discover URL
and company/news URL behavior, and evidence-driven continuation. Do not duplicate
or improvise a fixed call-count policy.

### 5.3 User-provided ticker list

Use only the named, covered tickers.

```text
up to the SearchPlan ticker-scope contract:
one multi-ticker SearchPlan with identical evaluation clauses for every candidate

larger lists:
split into contract-sized batches, then reconcile on the same evaluation axes and evidence floor
```

Exclude unavailable tickers silently unless their absence materially changes the requested screen.

### 5.4 Candidate validation

Validate material candidates with a complete but focused evidence path:

```text
1. Author a validation SearchPlan whose explicit ticker scope and atomic clauses apply the same screen to every selected candidate.
2. Call query_context with exactly {search_plan}; never send legacy top-level question/ticker/limit arguments.
3. Verify direct exposure, financial pathway, why-now evidence, strongest burden, and first rejection risk from ResearchState clause_coverage/evidence_units.
4. Follow missing_parts/recommended_actions only for a material required axis; do not repeat covered clauses.
5. Use selected trace/chain only when a material candidate claim needs stronger lineage support.
6. Stop when every material candidate can be classified consistently or disclose the remaining evidence boundary.
```

Never request `response_detail="full"` in normal web chat.

## 6. PM-Style Triage

Read and apply:

```text
references/search-order.md
references/candidate-funnel.md
references/candidate-evidence-policy.md
references/rejection-policy.md
references/output-contract.md
```

Evaluate every material candidate on the same axes:

```text
Exposure:
Does the company directly participate in the requested business or financial pathway?

Evidence:
Is the connection supported by recent company filing commentary, notes, contracts, or metrics?

Financial path:
How could the driver affect revenue, margin, cash flow, capital intensity, or balance-sheet risk?

Recent change:
Did the latest available filing strengthen, weaken, or leave the idea unchanged?

Why now:
Why should this company be researched before other candidates in this screen?

Burden:
What cost, capex, financing, execution, concentration, or cycle risk offsets the potential benefit?

First rejection:
What is the earliest filing-supported reason this candidate may be a false positive?

Next research:
What exact same-company question should be investigated next?
```

## 7. Priority Buckets

Use these buckets only as research priority:

```text
A - immediate deep-research candidate
B - watchlist candidate; one material condition remains
C - thematic screen flag; exposure or financial linkage remains weak
Reject - filing evidence does not support advancing the idea
```

Hard interpretation:

```text
A means research first.
A does not mean buy now.
B does not mean hold.
Reject means reject from this screen, not permanently reject the company.
```

Do not convert these buckets into ratings, target prices, expected returns, or portfolio actions.

## 8. Evidence Floor

A candidate cannot reach A from thematic relevance alone.

For A, require:

```text
credible direct or operational exposure
recent filing evidence
a plausible revenue, margin, cash-flow, or balance-sheet pathway
at least one explicit offset or first-rejection risk
clear reason to research this candidate first within the screen
```

B may have a credible pathway with one important evidence gap.

C is appropriate when:

```text
the theme is mentioned but economic linkage is not demonstrated
evidence is old, generic, or mostly risk-factor boilerplate
direct exposure is unclear
the financial pathway is speculative
```

Reject when the evidence fails the rules in `references/rejection-policy.md`.

Do not fill evidence gaps with model memory, market folklore, or unsupported peer assumptions.

## 9. Answer Contract

Follow `references/output-contract.md`.

Default answer shape:

```text
one-sentence research-priority conclusion
candidate funnel table
A candidates
B/C candidates
rejected or deprioritized false positives
next same-company research prompt for each important candidate
```

Use interpretation-oriented tables:

```text
등급 | 기업 | 후보로 잡힌 이유 | 실적 연결 | 첫 반증 위험
```

Do not overload the answer with raw numbers. Use exact figures only when they materially change candidate classification.

Do not describe ontology objects, candidate scores, ranking internals, search diagnostics, tools, packs, schemas, or index structure.

## 10. Handoff

End with actionable same-company deep-research prompts, not generic follow-up questions. Render them as a numbered Markdown list so the user can pick the next investigation directly.

Good:

```text
1. VST의 데이터센터 전력 수요가 실제 매출과 현금흐름으로 연결되는지 자세히 분석해줘.
2. ETN의 최근 수주 신호가 매출총이익률 개선으로 이어지는지 확인해줘.
3. XYL이 B 후보에 머문 핵심 근거 공백만 자세히 봐줘.
```

Do not introduce new peer companies in the handoff unless the user requested a comparison.

The next workflow for a selected candidate is `krw-ontology-research`.

## 11. Final Guardrails

Before sending the answer, verify:

```text
only covered companies are presented as researched candidates
the latest available filing drives current judgments
every A candidate has exposure proof, financial pathway, and first rejection
keyword matches and generic risk language were not promoted into strong ideas
no market price, consensus, positioning, short-interest, or portfolio facts were invented
the buckets are clearly research priorities, not recommendations
internal English briefs and implementation terms are absent
the Korean answer is concise, readable, and investment-focused
```
