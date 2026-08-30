# Observation provider fixtures

All files in this directory are hand-written synthetic fixtures modeled on the
documented response shapes of each endpoint. No live API response was recorded
(this task ran in a sandbox with no network); every fixture carries a
first-line `_comment` noting its shape source and the same warning:

> synthetic; verify against live API before first production collection run.

| File | Endpoint shape | Notes |
| --- | --- | --- |
| `fmp_price.json` | FMP stable `/stable/historical-price-full` | Top-level `{symbol, historical: [...]}`; daily bars with `date, open, high, low, close, volume, change, changePercent`. Exact stable per-bar field set is uncertain. |
| `fmp_ratios.json` | FMP stable `/stable/ratios-ttm?period=quarter` | Bare JSON array of quarterly TTM ratio rows; `priceToEarningsRatioTTM` / `priceToBookRatioTTM` field names reused from the market snapshot adapter (`services/krw-ontology-runtime` `market/snapshot.py`). |
| `fred_cpi.json` | FRED `fred/series/observations` | `observations: [{date, realtime_start, realtime_end, value}]`; string values with `"."` as the missing sentinel (skipped by the adapter). |
| `fred_cpi_vintage.json` | FRED/ALFRED vintage view | Rows in the compact `{date, value, vintage}` shape; the live ALFRED API tags revision rows with `realtime_start`/`realtime_end` (adapter maps `realtime_start` to the vintage in vintage mode). |
| `fred_cpi_vintage_realtime.json` | Live ALFRED row shape | `realtime_start`/`realtime_end`-tagged revision rows; exercises the adapter's `realtime_start` → vintage mapping in vintage mode. |
| `fred_cpi_vintage_window.json` | FRED/ALFRED vintage view (crafted) | Window-filter fixture: in-window period 2026-06-01 has a first vintage published before the `2026-06-01` window start; out-of-window phenomenon_times must drop while every vintage of in-window periods is kept. |
| `polygon_daily.json` | Polygon `/v2/aggs/ticker/{t}/range/1/day/{from}/{to}` | `results: [{t (ms epoch UTC), o, h, l, c, v, vw, n}]`; `t` values are real UTC-midnight epochs for 2026-06-22..26. |
