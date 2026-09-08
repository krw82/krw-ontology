# Table Extraction Audit — XBRL Cross-Validation (30 tickers)

- Release: `20260830_193811` (read-only), seed `20260908`, 5 rows per ticker, 150 rows total
- Date: 2026-09-08. Sampled tickers (seeded, sorted): ACGL AMAT AXON BA CB CI CVNA DD DELL
  DIS ECL EOG FAST FFIV FWONK GILD KHC KKR MDB META MLM MO MPC MSFT NTRS ONC PCAR PFG TER TW
- Reference: SEC XBRL `companyfacts` (cache-first, `~/krw-ontology-data/cache/sec-companyfacts`,
  outside the repo). Wall clock ~152 s cold (30 sequential fetches; one read-timeout abort
  after 28 — the CLI exits cleanly and the cache retains progress, warm re-run 2 s).
  Cache misses: 30/30 tickers on the first pass, 0 on the re-run.
- Rows whose unit is not USD-denominated are flagged `non_usd_unit` before comparison
  (Task-1 reviewed minor; this sample hit 0 — all six dictionary metrics carry
  `USD`/`USD_per_share` units in the shards).

## Verdict distribution

| verdict | rows | share | meaning |
| --- | ---: | ---: | --- |
| match | 89 | 59.3% | within 0.1% of the XBRL value after unit conversion |
| tolerance | 1 | 0.7% | within 1% |
| mismatch | 57 | 38.0% | beyond 1% |
| missing_xbrl | 3 | 2.0% | tag has no companyfacts entry for the row's fiscal year |
| non_usd_unit | 0 | 0.0% | unit not USD-denominated; skipped before comparison |
| no_tag_map | 0 | 0.0% | metric outside `XBRL_TAG_MAP` |
| no_cik | 0 | 0.0% | ticker unresolvable to a CIK |
| total | 150 | | 30 tickers x 5 rows |

By document origin (the decisive split):

| doc type | rows | match | tolerance | mismatch | missing | match rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10-K | 96 | 80 | 1 | 12 | 3 | 84.2% |
| 10-Q | 54 | 9 | 0 | 45 | 0 | 16.7% |

By metric: eps 19/29 match (9 mismatch), net_income 24/44 (20), operating_income 9/18 (9),
revenue 19/31 (12), total_debt 11/14 (3), research_and_development 7/12 (4, +1 missing).

## Mismatch taxonomy (all 57 mismatches, hand-classified)

Classification uses the row's shard provenance (`source_fact_ids` names the us-gaap tag the
shard itself extracted) plus the selected companyfacts entry (tag, form, start/end, frame).

| class | rows | share | side | mechanism |
| --- | ---: | ---: | --- | --- |
| period — 10-Q row vs annual reference | 24 | 42% | harness | shard row is a correct quarterly value; fy-only lookup prefers the 10-K annual fact of the same fy label (e.g. ACGL revenue Q2-26 5.213B vs FY25 19.929B) |
| period — wrong quarter / cumulative-vs-quarter | 20 | 35% | harness | both sides 10-Q, but latest-end/frame tiebreaks select a different quarter than the shard's (Q1 vs Q2, 3-month vs 6-month; e.g. AXON eps 2.11 vs 0.37, CVNA revenue 13.808B 6-mo vs 7.376B 3-mo) |
| period — quarterly context captured inside a 10-K | 6 | 11% | shard | same tag, annual filing, but the shard captured a sub-annual column/context (AXON operating_income 16.456M vs 58.54M; MLM revenue 1.161B vs 6.15B; ECL revenue Q1 3.7519B while the lookup also picked a Q4 frame 4.0052B over the unframed 15.7414B annual) |
| tag-mapping — candidate divergence / fallback | 4 | 7% | mixed | AXON total_debt: no fy-2024 `LongTermDebt` entry in companyfacts, fallback `LongTermDebtNoncurrent`=0 (shard 680.289M is the real convertible debt); DD total_debt: `Liabilities` fallback (7.472B) overstates; NTRS revenue: tag-priority-1 `Revenues` (6.7612B) vs shard's originating `RevenueFromContract...` (4.4326B); CB total_debt: shard's own canonical mapping placed `ShortTermBorrowings` (1.46B) under total_debt |
| value — same tag, same period, different number | 2 | 4% | shard | CB net_income fy2022 5.246B vs 5.313B (1.3%, measure/rounding variant); DELL operating_income fy2024 5.411B vs 5.211B (3.8%) |
| unit | 0 | 0% | — | no unit-conversion mismatch observed; shard values are raw, `scale=ones`, XBRL raw |
| period — fiscal-year labeling (off-cycle filer) | 1 | 2% | harness | DELL eps fy2024 4.71 (correct DELL FY2024 basic EPS) vs a 6-month 10-Q value 1.44: for Feb-year-end filers the SEC `fy` label of annual facts is shifted, so no 10-K-form entry carries fy=2024 |

Totals: period/context 51 of 57 (89.5%), tag-mapping 4 (7.0%), genuine value errors 2 (3.5%),
unit 0. Shard-side findings (rows where the shard itself is wrong or mislabeled): 6 quarterly
context captures + 1 canonical tag mapping (`ShortTermBorrowings` -> total_debt) + 2 value
variants = 9 of 150 rows (6.0%), of which numeric-only = 2 (1.3%).

The 3 `missing_xbrl` rows share one root cause: DELL eps fy2023, MDB eps fy2022, PCAR
R&D fy2023 are Jan/Feb fiscal-year-end filers whose companyfacts `fy` labels are shifted
relative to the shard's filing-aligned `fiscal_year` (DELL EPS fy values present:
2024+; MDB EPS: 2023+; PCAR R&D: 2024+), so no `fy == fiscal_year` entry exists.

Value-level precision on the clean annual subset (same tag, 10-K both sides, annual
period): 80 match + 1 tolerance + 2 value-mismatch = 83 comparable rows -> 96.4% within
0.1%, 97.6% within 1%.

## Section weak spots (all 356 shards, 1899 documents — `section_quality_stats`)

Document totals: pass 1714 (90.3%), warn 83 (4.4%), fail 102 (5.4%).

Table-heavy sections ranked by fail rate:

| section | title | docs | fail | warn | fail_rate | missing | low_conf |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| item8 | Financial Statements and Supplementary Data | 21 | 21 | 0 | 1.000 | 21 | 0 |
| item7a | Quantitative and Qualitative Disclosures About Market Risk | 33 | 31 | 2 | 0.939 | 33 | 0 |
| part1_item1 | (10-Q) Financial Statements | 45 | 32 | 13 | 0.711 | 45 | 0 |

Non-table-heavy context (highest-volume weak spots overall, outside the audit's table scope):
part1_item2 (MD&A) 71 docs / fail_rate 0.915, part1_item4 (Controls) 71 docs / 0.803,
part2_item1a (Risk Factors) 60 docs / 0.350, item7 (MD&A) 32 docs / 0.938, item1a 32 / 0.781,
item1 21 / 1.000. `item8` confirms at 1.000: whenever the 10-K financial-statements section
goes missing, the document hard-fails — it is never merely warned on. All flagged sections
were flagged as `missing` (undetected), not low-confidence, except two documents
(part1_item4 x2, part2_item1a x1).

## Interpretation vs the T2-RAGBench prior ("73% of SEC-QA errors are table/structure")

Our numbers confirm the table-heavy-weakness thesis and sharpen it into a structural, not
numeric, finding: 51 of 57 mismatches (89.5%) are period/context selection errors — the
extractor (or the audit's fy-only lookup) attaches the right metric to the wrong column,
quarter, or fiscal label — while only 2 rows (3.5% of mismatches, 1.3% of all rows) are
genuine numeric errors, and on clean annual same-tag comparisons value precision is 96.4%
within 0.1%. A large share of the period class is measurement-harness period alignment
(quarterly shard rows against annual or adjacent-quarter references), so the honest
shard-side error rate is 6.0% — and even those 9 rows are structure/mapping errors
(wrong column, wrong canonical tag), not wrong arithmetic. The section statistics
corroborate at the document level: the table-heaviest section (`item8`, the 10-K
financial statements) has fail_rate 1.000 and is always missing-never-low-confidence,
and the top table-heavy sections (item8, item7a, part1_item1) are all
missing-dominated. Conclusion: consistent with RAGBench, the weakness is table/structure
extraction (section detection plus period/column alignment), not numeric fidelity;
improvement work should target section detection for item8/item7a/part1_item1 and
period-context alignment of metric rows, not number parsing.

Audit-harness follow-ups this run exposed (not shard errors): fy-only lookup cannot align
quarterly rows; the frame tiebreak can prefer a framed quarterly entry over an unframed
annual with the same end date (ECL); off-cycle filers need fiscal-calendar-aware fy
matching (DELL/MDB/PCAR); the total_debt candidate list could add
`LongTermDebtAndCapitalLeaseObligations` (AXON's 680.289M convertible debt has no fy-2024
`LongTermDebt` entry today).

## Reproduction

```bash
# 1. ticker -> CIK map (public identifier exchange; download once)
curl -fsS --compressed -H "User-Agent: KRW Audit research@krw-ontology.local" \
  -o ~/krw-ontology-data/cache/company_tickers.json \
  https://www.sec.gov/files/company_tickers.json
python3 -c "
import json
d = json.load(open('$HOME/krw-ontology-data/cache/company_tickers.json'))
m = {e['ticker']: e['cik_str'] for e in d.values()}
# absent from the SEC file on 2026-09-08, verified via submissions API / exchange file:
m.update({'AVB': 915912, 'EA': 712515, 'BK': 1390777})  # BK renamed to BNY, same registrant
json.dump(m, open('$HOME/krw-ontology-data/cache/cik_map.json', 'w'))"

# 2. audit (same seed -> same 30 tickers and rows, cache-first)
uv run python scripts/audit_table_extraction.py \
  --release-root ~/krw-ontology-data/releases/v2-dev/dev/20260830_193811 \
  --tickers 30 \
  --out benchmarks/reports/table_audit_20260908.json \
  --cik-map ~/krw-ontology-data/cache/cik_map.json \
  --cache-dir ~/krw-ontology-data/cache/sec-companyfacts

# 3. merge full-release section stats into the JSON (deterministic, read-only)
uv run python -c "
import json
from pathlib import Path
from krw_ontology.eval_gold.table_audit import section_quality_stats
p = Path('benchmarks/reports/table_audit_20260908.json')
r = json.loads(p.read_text())
r['section_quality'] = section_quality_stats(
    Path.home() / 'krw-ontology-data/releases/v2-dev/dev/20260830_193811')
p.write_text(json.dumps(r, ensure_ascii=False, indent=2) + '\n')"
```
