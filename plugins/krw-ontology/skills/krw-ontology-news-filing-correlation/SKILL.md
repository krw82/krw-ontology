---
name: krw-ontology-news-filing-correlation
description: Use when a covered company’s recent stock move or selected market-news event must be compared with related recent SEC 8-K or 6-K disclosures, or when the user explicitly asks to check a recent 8-K/6-K alongside news. Establish the market observation first, read only closely timed and relevant filings, separate market reporting from company disclosure, and answer in Korean investor prose.
---

# KRW Ontology News–Filing Correlation

Use this skill for a bounded event-correlation answer, not a general company report and not a single source-card follow-up.

Use it for questions such as:

```text
최근 주가가 왜 움직였는지 뉴스와 같은 시점의 8-K를 같이 봐줘.
이 뉴스가 회사가 공시로 확인한 사실인지 구분해줘.
최근 6-K 원문까지 확인해서 시장 해석이 맞는지 봐줘.
선택한 뉴스가 실제 공시 내용과 어떻게 연결되는지 알려줘.
```

Do not use it for generic filing research without a recent event, a source filing card opened by the user, or broad news discovery with no company/ticker context.

## Required References

Before using filing evidence, read:

- [Correlation evidence policy](references/correlation-evidence-policy.md)
- [Visible-answer contract](references/output-contract.md)

## Bounded Workflow

Follow this order:

```text
1. Identify the ticker, observed price move, timing, and market-news candidates.
2. Treat price and news as market observations, not company-confirmed facts.
3. Search the catalog once for the same ticker’s nearby 8-K or 6-K candidates.
4. Select only a filing whose timing and subject could test the candidate explanation.
5. Read filing metadata or its ready brief for orientation.
6. Read the narrow primary-document section or verified exhibit needed for the material claim.
7. State what the market reported, what the company disclosed, and what remains unproven.
```

Use a small number of filings. Search the catalog only once per answer, then inspect its candidates rather than repeating that search. Do not scan a company’s entire filing history or turn this into an earnings/valuation report.

For an 8-K or 6-K, list deterministic sections before reading a section. Read an attached exhibit only after it appears in that filing’s verified document list. Use the returned filing identity, section key, and document key; never invent an accession number, file name, or SEC URL.

If no closely timed and relevant filing exists, say that the available filing record does not independently confirm the specific market narrative. Do not imply that the absence proves the narrative false.

## Interpretation Rules

- Keep these layers distinct: market observation, reported company disclosure, and investor interpretation.
- Do not say a headline caused a move solely because their timestamps are close.
- Treat an automatic filing brief as orientation. Ground a material filing claim in the returned SEC section or verified exhibit.
- Do not use model memory to fill a missing disclosure detail.
- Do not give personalized buy/sell/hold, target-price, or position-size advice.

## Final Answer

Write Korean Markdown directly for the investor. Start with the best-supported explanation, then make the source boundary visible in ordinary language:

```text
시장 보도에서 나온 설명
공시로 직접 확인된 사실
아직 단정할 수 없는 연결고리
투자자가 다음으로 볼 조건
```

Never expose provider, MCP, tool, plugin, runtime, event-ID, or internal payload names unless the user explicitly asks for debug provenance.
