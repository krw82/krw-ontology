---
name: krw-ontology-market-move-research
description: Use when the user asks why a covered stock moved today or recently, whether a drop/rally is meaningful, or whether a current market move changes the investment view. Start from app-provided market-move context when available, treat price/news data as market observation, give a lightweight market/news explanation by default, and guide follow-ups toward KRW ontology filing research. Final answers are Korean investor prose.
---

# KRW Ontology Market Move Research Skill

This skill is for stock-move interpretation, not generic news summarization and not filing-only company research.

Use it when the user's question starts from an observed market move:

```text
오늘 왜 빠졌어?
왜 6% 하락했어?
이 뉴스 때문에 빠진 게 맞아?
시간외 급락 이유가 뭐야?
최근 조정이 장기 관점에 영향을 줘?
지금 반등은 의미 있어?
```

The workflow is:

```text
observed stock move / app-provided market move context
-> candidate event attribution
-> lightweight market/news interpretation
-> Korean follow-ups that bridge the move/news into KRW ontology filing research
```

## 1. Operating Contract

Default output is Korean Markdown only.

This skill is provider-agnostic. The web app may build `selected_news_event_context.version = market-move-context/v1` from Yahoo Finance, FMP, Polygon, or another market/news provider. Treat provider details as internal provenance, not as the answer frame.

Do not expose:

```text
provider names unless the user asks about data/debug
raw API payloads
MCP/tool/plugin/skill names
market-move-context/v1
source tier labels
ontology_bridge_brief
query_context
internal English briefs
```

Do not use this skill for:

```text
ordinary company overview
filing-only buy/sell/hold question without current price/news context
user-provided URL impact with no observed stock move
news event cards before the user selects an event
earnings deep dive
scenario sensitivity with no market move
```

Use `krw-ontology-news-research` for selected market news or user-provided URLs that are not primarily about an observed stock move.

## 2. Required Inputs

Prefer app-provided `selected_news_event_context` when present.

The ideal app context is:

```text
version = market-move-context/v1
ticker and company
as-of timestamp
session: regular, pre-market, after-hours, closed, or unknown
observed price direction and approximate magnitude
volume or activity signal when available
benchmark/sector relative signal when available
candidate event/news explanations
source previews or links when available
source_pack.ontology_search_brief_en or ontology_bridge_brief_en
```

If no app context is present but current-event tools are available, use bounded current-event discovery only enough to identify candidate explanations. Do not browse broadly or turn this into news discovery.

## 3. Research Workflow

Follow this order unless the user explicitly asks for a different format:

```text
1. Convert the Korean user request into an internal English market-move investment brief.
2. Anchor the observed move: ticker, session, timing, direction, approximate size, and relative context if provided.
3. Identify candidate explanations and rank them by timing, source quality, company specificity, and financial materiality.
4. Separate company-specific, sector/market-driven, mixed, and unknown explanations.
5. Give a lightweight Korean market/news interpretation from the app-provided context.
6. Do not automatically turn the first answer into a filing-grounded ontology report.
7. Use KRW ontology only when the user asks for filing/공시, long-term or durable thesis impact, financial impact, margin, cash flow, balance sheet, dilution, risk, or when the answer would otherwise imply a durable investment-thesis change.
8. End with follow-up prompts that bridge the price/news move into KRW ontology filing research.
```

Never expose the internal English brief.

## 3.1 Default Depth

For first-turn market-move questions, default to a lightweight market-move answer.

Use the app-provided market observation and news candidates first. Do not automatically turn every price-move question into a full filing-grounded ontology report.

Default first answer should focus on:

```text
observed move
likely market/news explanation candidates
why causality is uncertain
what would need filing-based verification next
```

Use KRW ontology only when:

```text
the user explicitly asks for filing/공시 evidence
the user asks for long-term, durable thesis, or investment assumption impact
the user asks for financial impact: revenue, margin, cash flow, balance sheet, dilution, capital allocation, or risk
the market narrative cannot be explained responsibly without checking the company baseline
the answer would otherwise imply that the move changes the durable investment view
```

If KRW ontology is not used in the first answer, do not apologize and do not mention tool limits. Instead, make the follow-up prompts invite the user into the filing-based check.

## 4. Read Before Acting

Load only the references needed for the question:

```text
references/market-move-context-policy.md
- Provider-agnostic stock-move context handling and causality rules.

references/event-attribution-policy.md
- Candidate explanation ranking, timing, source quality, and causal language.

references/ontology-bridge-policy.md
- How to turn market observations into ontology-friendly filing research.

references/output-contract.md
- Korean investor answer shape and forbidden wording.
```

## 5. Price Move Is Not The Cause

The observed move is what needs explanation.

Do not write:

```text
주가가 6% 빠졌기 때문에 AI 투자 우려가 원인입니다.
이 뉴스 하나 때문에 빠졌습니다.
```

Write like this:

```text
이번 하락은 단일 뉴스 하나로 단정하기보다는, 최근 보도에서 부각된 AI 투자 부담이 공시상 현금흐름/마진/자본배분 가정에 어떤 압력을 주는지로 해석하는 편이 맞습니다.
```

## 6. KRW Ontology Use

KRW ontology is available in this skill, but it is not mandatory for every first-turn market-move answer.

Use KRW ontology after the market move and candidate explanation are identified only when the question requires filing-grounded interpretation or durable thesis impact.

The first ontology call should normally be `query_context` using an internal English brief that includes:

```text
ticker
observed move and timing
candidate explanation
financial channel to test
business/risk channel to test
latest filing baseline needed
confirmation and falsification conditions
```

Use targeted follow-up only when it answers a clear missing part:

```text
specific metric
specific filing comment
specific risk/exposure
specific cash-flow or balance-sheet item
specific margin/cost/revenue channel
specific trace/chain root
```

Do not loop over similar searches. Do not use broad retrieve after sufficient `query_context`.

If the user only asks "why did it move today?", the normal answer can stop before ontology and use the follow-up questions to offer filing-grounded checks.

## 7. Investment Assumption Update

Only make an investment-assumption update when the user asks for thesis impact or when KRW ontology evidence was actually checked.

When applicable, use this language:

```text
strengthened = the move/news confirms a positive thesis driver
maintained = noisy move; filing baseline still supports the prior view
weakened = the move/news increases pressure on a key assumption
broken = the move/news directly undermines a core filing-supported assumption
unknown = source/timing/company-specific mechanism is insufficient
```

Use cautious language when evidence is incomplete. If ontology was not used, avoid durable labels such as strengthened/maintained/weakened/broken and phrase the answer as a market/news explanation instead.

## 8. Final Answer Style

Write for an investor, not for a data auditor.

Good default structure:

```text
첫 문단: 오늘/최근 움직임을 어떻게 봐야 하는지
가격 움직임: 방향, 크기, 시점
시장에서 거론된 이유: 뉴스/이벤트 후보
아직 단정하기 어려운 부분: 인과관계와 회사 고유 요인
다음에 확인할 것: 공시/재무/리스크로 검증할 축
```

Use numbers sparingly. Mention move size once if it frames the question. Do not dump raw price, volume, or article tables.

Normal answers should end with:

```text
이어서 볼 질문
```

Include exactly 3 concise Korean follow-up questions unless the user asks for no follow-ups or asks for raw/debug/status output.

Follow-ups must bridge the observed price/news move into KRW ontology research. They should ask how the candidate news connects to disclosed revenue, margin, cash flow, balance sheet, dilution, capital allocation, risk factors, or company comments. Do not use generic company research follow-ups.
