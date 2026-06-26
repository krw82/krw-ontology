---
name: krw-ontology-router
description: Route KRW Ontology web-chat questions into the correct owning workflow. Use only for classification, never for research or final answers.
---

# KRW Ontology Router Skill

This is a thin routing skill. It does not research, browse, call MCP tools, draft investment answers, or compose final Markdown.

Its only job is to choose the owning workflow for a user question before the web app starts the durable research run.

## Output Contract

Return one structured routing decision:

```json
{
  "run_kind": "company_research"
}
```

Allowed `run_kind` values:

```text
company_research
market_move_research
news_discovery
news_research
idea_generation
scenario_sensitivity
```

Return only `run_kind`. Do not return ticker, company name, confidence, reason, source notes, or analysis.

Use `company_research` when uncertain or when routing fails.

## Routing Rules

### company_research

Use for ordinary filing-grounded company research:

```text
business model
financial structure
risk factors
capital allocation
buy/sell/hold questions without a current price/news move
filing-based thesis or investment interpretation
company comparison when the user names companies
```

### scenario_sensitivity

Use when the user asks for scenarios, sensitivities, future paths, or judgment
change conditions for an already selected company, thesis, or prior answer:

```text
앞으로의 시나리오 알려줘
상승/하락/기준 시나리오로 나눠줘
판단이 바뀌는 조건을 알려줘
어떤 조건이면 이 thesis가 깨져?
AI 투자 사이클이 성공/실패하는 경우를 나눠줘
what are the upside/base/downside scenarios?
what would break the thesis?
```

This route should use the scenario-sensitivity workflow. It starts from latest
filing/company context, extracts the key investment assumptions, connects the
business path to the financial path, and defines strengthen / maintain / weaken
/ thesis-break conditions.

### market_move_research

Use when the user starts from an observed stock move:

```text
오늘 왜 빠졌어?
오늘 가격 변동이 왜 이래?
왜 시간외 급락했어?
주가가 오른 이유가 뭐야?
이 뉴스 때문에 빠진 게 맞아?
최근 조정이 장기 관점에 영향을 줘?
why did AAPL move today?
why are shares down after hours?
```

This route starts from market/price/news context. The first answer should usually explain the observed move and market/news candidates, then guide follow-ups into KRW Ontology filing research.

### news_discovery

Use when the user wants to find/select current market-news events first:

```text
오늘 AAPL 관련 뉴스 찾아줘
최근 반도체 섹터에서 볼 만한 뉴스 골라줘
GOOGL 관련 시장 이벤트 후보 보여줘
```

### news_research

Use only when a specific market narrative, selected event, or user-provided URL is already present and should be interpreted against KRW Ontology evidence.

Do not choose `news_research` just because the question contains the word "news".

### idea_generation

Use when the user asks to discover candidate companies from conditions, themes, financial filters, exclusions, or investment idea screens:

```text
AI 데이터센터 투자 수혜주 찾아줘
차입 부담은 낮고 현금흐름이 좋아지는 기업 찾아줘
메모리 가격 상승에 유리한 회사 후보를 뽑아줘
```

## Important Boundary

Generic public-equity routers often avoid simple share-price questions. KRW Ontology should not.

For this product, a covered-company question such as "오늘 가격 변동이 왜 이래?" should route to `market_move_research`, not default company research, when there is a ticker in the session or question.

Do not expose routing, tool, MCP, skill, plugin, or internal mode names to the user.
