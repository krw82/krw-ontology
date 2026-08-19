---
name: krw-ontology-filing-follow-up
description: Use only when the KRW web runtime states that this chat was opened from one verified SEC filing card. Answer a focused follow-up about that exact 8-K, 6-K, or Form 4 from its verified metadata and bounded SEC source content. Do not use for ordinary company research, filing discovery, cross-filing comparison, current-news analysis, valuation, or personalized trading advice.
---

# KRW Ontology Source Filing Follow-up

## 1. Runtime Boundary

This skill requires a runtime-owned source filing context. The filing identity is
not supplied by the user and must never be changed, guessed, or widened.

Use this skill only for a question about the single SEC filing card that opened
the chat. Do not search for, substitute, or compare another filing. Do not turn
the request into a general company research pass.

The available filing tools are read-only and are constrained by the runtime to
the original filing card.

## 2. Required Reference Files

Before reading the source, follow these binding reference files:

- [Source filing scope policy](references/source-filing-scope.md)
- [Visible-answer contract](references/output-contract.md)

They refine this skill's source and answer boundaries. They never authorize a
tool, source, or claim outside the active filing.

## 3. Request Boundary

Use this skill for questions such as:

```text
이 공시에서 실제로 새로 나온 내용이 뭐야?
이 8-K가 투자 가설에 어떤 의미가 있어?
이 Form 4 거래를 어떻게 해석해야 해?
공시에 적힌 위험이나 다음 확인 항목은 뭐야?
```

Do not use this skill for:

```text
이 회사 전체를 분석해줘.
지난 공시와 비교해줘.
최근 뉴스와 주가 반응까지 설명해줘.
목표주가와 매수 의견을 줘.
```

If the user asks beyond the source filing, answer only the portion supported by
the active filing and state the remaining point as a next check, without
silently substituting broader research.

## 4. Source Reading Workflow

First identify the filing type from the verified filing metadata.

For an 8-K or 6-K:

```text
1. Read the filing metadata.
2. Use the ready filing brief only as an orientation aid when it is available.
3. List the deterministic filing sections.
4. Read only the section or sections needed for the user's question.
5. If that section identifies a relevant attached exhibit, list this filing's
   verified documents and select only the one necessary text exhibit.
6. Read the selected exhibit by its returned document key. Never guess a file
   name, URL, or document key.
7. Base the answer on the returned SEC link and exact primary-document or
   exhibit content.
```

For example, when an Item 2.02 or Item 9.01 identifies an earnings press
release and the user asks about reported financial results, read the verified
`EX-99.1` only when it appears in this filing's document list. An attachment
listed by the source filing is still part of the same single-filing boundary;
it does not authorize a different accession, another filing, or current news.

For a Form 4:

```text
1. Read the filing metadata.
2. Read the structured insider owners, transactions, and summary facts.
3. Distinguish a reported transaction from an inferred management signal.
```

Do not repeat unchanged calls. Read the narrowest relevant source section and
avoid a raw-document dump.

## 5. Interpretation Discipline

Separate three things:

```text
reported fact in this filing
reasonable investor interpretation
what this filing alone cannot establish
```

Do not claim that one filing proves a durable earnings, valuation, or investment
outcome unless that exact filing directly supports it. For Form 4, do not infer
an executive's intent, future price direction, or a buy/sell signal from the
transaction alone.

When a material detail is absent, say what document detail would resolve it.
Never fill the gap from model memory, a different filing, current news, or an
unverified market claim.

## 6. Visible Answer

Write concise Korean Markdown investor prose. Start with the direct answer,
then explain the filing fact and its practical implication when material.

Do not expose tool names, MCP names, runtime restrictions, internal IDs, raw
payloads, or internal workflow narration. Do not give personalized buy, sell,
hold, position-size, or target-price advice.
