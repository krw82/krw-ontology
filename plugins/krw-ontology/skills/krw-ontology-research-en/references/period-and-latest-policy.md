# Period And Latest Policy

## User-facing labels

Use CY-style ontology labels in final answers.

```text
Good: CY2026Q1, CY2025
Bad: FY2026, FY 2026
```

If the issuer fiscal calendar matters, add only a short parenthetical note.

## Runtime filing anchor

If the web runtime context provides a `Company filing anchor`, obey it.

Use the provided `Current driver` as the starting point for current company analysis and investment-decision answers.

Use the provided `Annual baseline` only for annual business mix, historical trend, and risk baseline.

Use `Historical context` only for cycle comparison or change over time.

Do not let older 10-K evidence dominate the current judgment when a Current driver is provided.

## Default period anchor

Unless the user asks for a specific historical period, specific filing, or long-horizon trend, start with the most recent available filing evidence by filing/period recency.

Use older filings as baseline context:

```text
annual revenue mix
business model baseline
historical trend
change, persistence, improvement, or deterioration
```

## Recent/latest questions

If the user says:

```text
최근
최신
최근 매출 동인
최근 분기
latest
recent
most recent
```

the same default applies with extra force: start with the most recent available filing by filing/period recency. A newer 10-Q beats an older 10-K for current drivers, financial impact, cost, cash flow, risk, and management commentary; a latest 10-K is primary only when no newer 10-Q exists.

Example: if the available documents are `CY2025 10-K` and `CY2026Q1 10-Q`, lead with `CY2026Q1 10-Q` for current drivers and use `CY2025 10-K` as annual mix/business baseline context.

Example: if `CY2026Q1 10-Q` and `CY2026Q2 10-Q` are both available, lead with `CY2026Q2 10-Q`.

Do not say that the latest 10-Q needs to be checked when it was available through tools or provided as the Current driver. Use it as the current driver, or state clearly that the latest 10-Q was not confirmed in the retrieved evidence.

## Investment-decision anchor

For buy/sell/hold/timing questions, anchor the judgment on the latest available filing.

```text
Current driver: latest available 10-Q when it exists
Annual baseline: latest 10-K when a newer 10-Q exists
Historical context: older 10-K/10-Q periods
```

Do not let older annual filing evidence dominate the final decision if a newer quarterly filing is available. Older 10-K evidence may explain business mix, segment structure, historical cycle, and risk baseline, but the current investor judgment should start from the newest filing driver.

## Business model plus recent drivers

Answer order:

```text
1. most recent filing drivers/current changes
2. annual mix/business baseline when it is not already the most recent filing
3. interpretation and caveats
```

Do not open with annual-only framing when the user explicitly asks for recent drivers.
