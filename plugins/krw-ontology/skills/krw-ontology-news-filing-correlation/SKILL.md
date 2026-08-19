---
name: krw-ontology-news-filing-correlation
description: Use when selected KRW Feed issues or a recent stock-move narrative must be compared with closely timed SEC 8-K or 6-K disclosures. Start from the feed, use one bounded current-market-news cross-check only when needed, then separate reporting from company disclosure in Korean investor prose.
---

# KRW Feed - Filing Correlation

Use this skill for a bounded event-correlation answer, not a general company report and not an open-web news search.

Use it for questions such as:

```text
이 피드 이슈가 회사가 공시로 확인한 사실인지 구분해줘.
선택한 뉴스와 같은 시점의 8-K를 같이 봐줘.
최근 6-K 원문까지 확인해서 시장 해석이 맞는지 봐줘.
이 이슈가 회사 코멘트나 공시와 어떻게 연결되는지 알려줘.
```

## Required References

Before using filing evidence, read:

- [Correlation evidence policy](references/correlation-evidence-policy.md)
- [Visible-answer contract](references/output-contract.md)

## Bounded Workflow

Follow this order:

```text
1. Read selected issue IDs with get_feed_context. If none are supplied, use list_feed_items only for the active ticker and a narrow recent window.
2. Identify the ticker, event timing, and the concrete market narrative in the stored feed posts.
3. If needed, call get_yahoo_finance_news once for the same ticker and event window. It is supplementary market reporting and must not replace the selected feed issue.
4. Treat the feed narrative and supplementary reporting as market observation, not company-confirmed fact.
5. Search the filing catalog once for nearby same-ticker 8-K or 6-K candidates.
6. Select only a filing whose timing and subject could test the candidate explanation.
7. Read filing metadata or its ready brief for orientation, then only the narrow primary-document section or verified exhibit needed for a material claim.
8. State what the feed reported, what supplementary market reporting added, what the company disclosed, and what remains unproven.
```

Use a small number of filings. Do not scan the issuer's full filing history or turn this into an earnings or valuation report.

For an 8-K or 6-K, list deterministic sections before reading a section. Read an attached exhibit only after it appears in that filing's verified document list. Use returned filing identity, section key, and document key; never invent an accession number, file name, or SEC URL.

If no closely timed relevant filing exists, say that the available filing record does not independently confirm the specific feed narrative. Do not imply that the absence proves the narrative false.

## Interpretation Rules

- Keep these layers distinct: feed observation, supplementary market reporting, company disclosure, and investor interpretation.
- Do not say a feed post or market report caused a move solely because their timestamps are close.
- Ground material filing claims in the returned SEC section or verified exhibit, not in an automatic brief alone.
- Do not use model memory to fill a missing disclosure detail.
- Do not give personalized buy/sell/hold, target-price, or position-size advice.

## Final Answer

Write Korean Markdown directly for the investor in this order:

```text
피드에서 나온 설명
추가 시장 보도에서 확인된 맥락
공시로 직접 확인된 사실
아직 단정할 수 없는 연결고리
투자자가 다음으로 볼 조건
```

Never expose X, MCP, tool, plugin, runtime, event ID, or internal payload names unless the user explicitly asks for debug provenance.
