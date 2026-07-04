# Period And Latest Policy

## User-facing labels

Use CY-style ontology labels in final answers.

```text
Good: CY2026Q1, CY2025
Bad: FY2026, FY 2026
```

If the issuer fiscal calendar matters, add only a short parenthetical note.

## Fiscal calendar label guard

The ontology period label is the default user-facing label. Some issuers'
fiscal quarters do not match the calendar quarter in the ontology label.

When retrieved evidence or source text uses an issuer fiscal label that differs
from the ontology CY label, show both labels on first mention:

```text
Korean: CY2026Q2 / Micron FY2026 Q3 10-Q
English: CY2026Q2 (Micron fiscal Q3 2026 10-Q)
```

After first mention, prefer `the latest 10-Q`, `latest quarter`, or the
ontology CY label. Do not write `Q3`, `FY2026 Q3`, or `third quarter` alone
when that can be confused with the ontology period.

Do not infer that a newer CY period exists because a filing's source text says
issuer fiscal Q3/Q4. Treat issuer fiscal labels as aliases for the confirmed
filing period, not proof of another document.

## Default period anchor

Unless the user asks for a specific historical period, specific filing, or long-horizon trend, start with the most recent available filing evidence by filing/period recency.

Use older filings as baseline context:

```text
annual revenue mix
business model baseline
historical trend
change, persistence, improvement, or deterioration
```

## MCP filing document roles

When MCP responses include `filing_document_roles`, follow those roles before generic document ordering.

The role contract is 10-Q/10-K only:

```text
filing_document_roles.<TICKER>.current_driver
  latest available 10-Q when it exists
  otherwise latest available 10-K
  use as the latest reported financial baseline for current/news-event interpretation

filing_document_roles.<TICKER>.annual_baseline
  latest available 10-K
  use for annual business mix, segment structure, annual revenue/cost baseline, and historical risk baseline

filing_document_roles.<TICKER>.latest_available
  latest available 10-Q/10-K filing
  usually the same as current_driver
```

`current_document_anchors` is compatibility shorthand for `filing_document_roles.<TICKER>.current_driver`.

Do not introduce other filing-form roles. In this ontology workflow, the document-role boundary is `10-Q` and `10-K`.

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

## Business model plus recent drivers

Answer order:

```text
1. most recent filing drivers/current changes
2. annual mix/business baseline when it is not already the most recent filing
3. interpretation and caveats
```

Do not open with annual-only framing when the user explicitly asks for recent drivers.

## News date vs filing period

In news mode, separate source timing from financial-period timing.

```text
news date = event/reporting date
source date = publication or company announcement date
filing period = the financial baseline period
```

Recent news can be the current driver even when the latest filing period is older. The filing baseline still determines reported numbers, accounting classification, segment definitions, cash-flow treatment, and annual mix.

Example: if a current news event appears after `CY2026Q1 10-Q`, use the news as the current event and `CY2026Q1` as the latest reported financial baseline.

Example: if available filings are `CY2025 10-K` and `CY2026Q1 10-Q`, use `CY2026Q1` as the current financial driver and `CY2025` as annual mix/business baseline.
