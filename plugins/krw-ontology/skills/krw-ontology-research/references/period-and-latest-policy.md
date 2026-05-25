# Period And Latest Policy

## User-facing labels

Use CY-style ontology labels in final answers.

```text
Good: CY2026Q1, CY2025
Bad: FY2026, FY 2026
```

If the issuer fiscal calendar matters, add only a short parenthetical note.

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

start with the most recent available filing by filing/period recency. A newer 10-Q beats an older 10-K for current drivers; a latest 10-K is primary only when no newer 10-Q exists.

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
