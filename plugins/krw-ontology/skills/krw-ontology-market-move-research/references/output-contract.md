# Output Contract

Use this reference only for `krw-ontology-market-move-research`.

Final answers are Korean investor prose. They are not provider output, raw news summaries, or raw filing audits.

For first-turn market-move questions, the default answer is lightweight: explain the observed price move and the most relevant market/news candidates. Do not automatically write a full filing-grounded ontology report.

## 1. Preferred Shape

Use this flow when it fits the question:

```text
1. First paragraph: concise read on the move
2. Price move: direction, approximate magnitude, session/timing when available
3. Market/news candidates: what appears to have pressured or supported the stock
4. Causality boundary: what is still uncertain or not company-specific
5. Filing bridge: what should be checked next in filings if the user wants deeper analysis
```

Do not force a rigid table. Use tables only when they improve scanning.

If the user explicitly asks for 공시, long-term impact, thesis impact, financial impact, risk, cash flow, margin, balance sheet, dilution, or capital allocation, use the filing-grounded shape instead:

```text
1. First paragraph: concise judgment on how to read the move
2. Strongest explanation candidate
3. Filing baseline
4. Investment assumption update
5. What changes the judgment
```

## 2. Good Opening

Good:

```text
오늘 하락은 단일 원인으로 확정하기보다는, 장중에 부각된 AI 투자 부담/규제 우려 같은 뉴스 후보가 단기 심리를 눌렀는지부터 보는 게 맞습니다. 공시 기준의 장기 영향은 별도로 확인해야 합니다.
```

Bad:

```text
FMP 데이터에 따르면...
Yahoo Finance MCP가 보여주는 것은...
market-move-context/v1 기준으로...
공시 기준으로 점검한 결과...
```

## 3. Numbers

Use numbers only when they support the interpretation.

Good:

```text
하락폭이 컸다는 점은 단기 심리 훼손을 보여주지만, 투자적으로는 그 하락이 공시상 현금흐름 체력이나 마진 구조를 실제로 흔드는지가 핵심입니다.
```

Avoid raw tables of:

```text
price
open/high/low/close
volume
article counts
provider scores
```

## 4. Follow-Up Questions

Normal answers end with:

```text
이어서 볼 질문
```

Include exactly 3 concise Korean follow-up questions. Prefer prompts the user can send immediately:

```text
이번 뉴스가 매출·마진·현금흐름 중 어디에 연결되는지 공시 기준으로 봐줘.
이 이슈가 일회성 노이즈인지 구조적 리스크인지 최신 공시로 확인해줘.
이번 하락을 무시해도 되는 조건과 진짜 위험해지는 조건을 공시 기준으로 나눠줘.
```

Follow-ups must bridge the observed move/news candidate into filing-based research. Do not end with generic prompts such as "사업 구조를 정리해줘" or peer-comparison prompts unless the user asked for them.
