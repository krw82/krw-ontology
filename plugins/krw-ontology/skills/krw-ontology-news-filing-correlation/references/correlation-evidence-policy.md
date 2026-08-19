# Correlation Evidence Policy

Apply this policy only in the news–filing correlation workflow.

## Scope

- Start with the active company ticker and the event window in the user question or selected market context.
- Search recent catalog metadata exactly once, only for that ticker. Prefer 8-K and 6-K candidates whose filing date and subject plausibly overlap the news/event window; then inspect a returned candidate instead of repeating the search.
- A filing can corroborate, qualify, or fail to corroborate a market narrative. It does not by itself prove that the filing caused the price move.
- Do not broaden into a historical catalog sweep, ordinary ontology report, or a second issuer unless the user explicitly asks and the runtime permits it.

## Reading Sequence

1. Retrieve candidate metadata.
2. Use a filing brief only to decide relevance.
3. List the chosen 8-K/6-K sections.
4. Read the narrow section needed for a material claim.
5. When needed, list verified filing documents and read one returned text exhibit.

For a Form 4, use structured transaction facts only; do not manufacture a narrative link to the price move.

## Claim Labels

Use these distinctions in ordinary Korean rather than as internal labels:

| Evidence state | User-facing meaning |
| --- | --- |
| Market report only | 시장에서 거론된 설명이지만 회사 공시로 직접 확인되지는 않음 |
| Disclosure-aligned | 공시에 해당 사실 또는 관련 경영진 설명이 직접 있음 |
| Disclosure-qualified | 공시는 관련 사실을 보이지만 시장의 강한 해석까지는 뒷받침하지 않음 |
| Unresolved | 시점·내용·회사 고유 영향의 연결이 부족해 인과를 단정할 수 없음 |

Never use a filing’s absence to claim that a report is false. Say that the available filing record did not independently establish that specific point.
