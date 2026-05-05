# Design Doc: 10-K Evidence Ontology Workspace — v1 Implementation Plan

**Date:** 2026-05-02
**Status:** Pre-implementation
**MVP Target:** AAPL latest 10-K, end-to-end pipeline

> **Document status:** Architecture/design reference. For v1 implementation details, `dev_spec_v1.md` is the source of truth. If this design document conflicts with `dev_spec_v1.md`, implement `dev_spec_v1.md`.

---

## 1. Architecture Overview

```text
┌─────────────────────────────────────────────────────┐
│                  Claude Code / Codex                   │
│         (query interface — reads artifacts only)       │
│  artifact_index.json, graph_report.md, *.jsonl       │
└─────────────────────┬───────────────────────────────┘
                      │ reads
┌─────────────────────▼───────────────────────────────┐
│               File System (source of truth)            │
│  companies/{ticker}/ontology/{doc_type}/{period}/    │
│  companies/{ticker}/sources/{doc_type}/{period}/     │
│  ontology/schema/*.yaml                              │
└─────────────────────▲───────────────────────────────┘
                      │ writes
┌─────────────────────┴───────────────────────────────┐
│            Python CLI / Orchestrator                   │
│  krw-ontology build-evidence-ontology ...              │
│                                                      │
│  ┌──────────────┐  ┌────────────────────────────┐   │
│  │ Code stages  │  │ Claude Agent SDK Worker    │   │
│  │ download     │  │ extract_evidence_quotes   │   │
│  │ clean_md     │  │ extract_claims            │   │
│  │ build_spans  │  │ extract_risks_drivers_etc │   │
│  │ extract_xbrl │  │ extract_assumptions      │   │
│  │ validate     │  └────────────────────────────┘   │
│  │ build_index  │                                    │
│  └──────────────┘                                    │
└──────────────────────────────────────────────────────┘
```

### Separation of Concerns

| Layer | Technology | Responsibility |
|-------|-----------|-----------------|
| CLI/Orchestrator | Python (click/typer) | Pipeline scheduling, checkpoint, retry, file I/O |
| Extraction Worker | Claude Agent SDK | Quote, claim, risk, driver, headwind, assumption extraction |
| Validation | Python (deterministic) | Schema, exact match, numeric guard, relation whitelist |
| Query Interface | Claude Code / Codex | Reads artifacts, answers research questions |

---

## 2. PRD Patches

### Patch 1: ID Convention Normalization

**Problem:** PRD uses `10-K` in metadata (Section 11) but `10K` in IDs (Section 12).

**Resolution:**

```python
DOCUMENT_TYPE_DISPLAY = "10-K"   # human-readable, stored in metadata
DOCUMENT_TYPE_KEY = "10K"         # path-safe, used in IDs and directory names

MAPPING = {
    "10-K": "10K",
    "10-Q": "10Q",
    "earnings_call": "EARNINGS_CALL",
    "research_report": "RESEARCH_REPORT",
    "valuation_model": "VALUATION_MODEL",
}
```

- `metadata.json` stores `document_type: "10-K"` (display value)
- All IDs use key: `source:AAPL:FY2025:10K`
- Directory paths use key: `companies/AAPL/ontology/10K/FY2025/`
- `document_type` field in every JSONL object stores display value
- Normalization function in shared utils, not duplicated per module

### Patch 2: Missing Data Model Definitions

**2a. GrowthDriver** (mirrors RiskFactor structure)

```json
{
  "id": "growth_driver:AAPL:FY2025:10K:services-revenue-expansion",
  "type": "GrowthDriver",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "name": "Services revenue expansion",
  "category": "revenue_growth",
  "description": "Services segment continues to show double-digit growth driven by App Store, iCloud, and Apple Music.",
  "supported_by_claims": ["claim:AAPL:FY2025:10K:services-growth"],
  "supported_by_quotes": ["quote:AAPL:FY2025:10K:item7:0045:002"],
  "affects": ["revenue_growth", "gross_margin"],
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

**2b. Headwind** (mirrors RiskFactor structure)

```json
{
  "id": "headwind:AAPL:FY2025:10K:fx-pressure",
  "type": "Headwind",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "name": "Foreign exchange headwind",
  "category": "macro_economic",
  "description": "Strengthening US dollar creates headwind for international revenue.",
  "supported_by_claims": ["claim:AAPL:FY2025:10K:fx-risk"],
  "supported_by_quotes": ["quote:AAPL:FY2025:10K:item7a:0089:001"],
  "affects": ["revenue_growth", "gross_margin"],
  "confidence": "medium",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

**Note:** GrowthDriver and Headwind share the same schema structure as RiskFactor. The `type` field distinguishes them. This is intentional — reduces schema surface area and allows shared validation logic. A single `ResearchObject` schema with type discrimination is the implementation approach.

**2c. Metric** (canonical object, links to metric_dictionary.yaml)

```json
{
  "id": "metric:gross_margin",
  "type": "Metric",
  "name": "Gross Margin",
  "category": "profitability",
  "unit": "percent",
  "description": "Gross profit divided by total revenue.",
  "standard_name": "GrossMargin",
  "related_xbrl_tags": ["us-gaap:GrossProfit", "us-gaap:Revenues"],
  "schema_version": "0.1.0"
}
```

Note: Metrics are canonical (not per-ticker, not per-period). They live at `ontology/schema/metric_dictionary.yaml` and are referenced by name from RiskFactor.affects, GrowthDriver.affects, Headwind.affects, and ResearchClaim.related_metrics.

**2d. XBRLFact**

```json
{
  "id": "xbrl:AAPL:FY2025:10K:GrossProfit:a1b2c3d4",
  "type": "XBRLFact",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "taxonomy_tag": "us-gaap:GrossProfit",
  "safe_taxonomy_tag": "GrossProfit",
  "value": 170782000000,
  "unit": "USD",
  "context_ref": "FY2025",
  "source_filing_detail": "income-statement",
  "schema_version": "0.1.0"
}
```

### Patch 3: objects.yaml Cleanup for v1

**v1 core objects** (defined + implemented):

```yaml
objects:
  - SourceDocument
  - SourceSpan
  - EvidenceQuote
  - LanguageSignal
  - ResearchClaim
  - RiskFactor
  - GrowthDriver
  - Headwind
  - AssumptionCandidate
  - Metric
  - XBRLFact
  - Edge
```

**Future objects** (defined in YAML as placeholders, not implemented in v1):

```yaml
future_objects:
  - KeyPhrase          # v1.5 — key phrase extraction for search
  - ResearchReport     # v2 — research report as source
  - ValuationModel     # v4 — model integration
  - ModelAssumption    # v4 — model assumption tracking
  - Event              # v2 — corporate event extraction
```

**Remove from v1 file structure:**
- `key_phrases.jsonl` — moved to v1.5
- `events.jsonl` — moved to v2
- `models/` directory — moved to v4

### Patch 4: v1 Scope Narrowing

**v1:** 10-K only
**v1.1:** + 10-Q
**v1.2:** + earnings_call
**v1.3:** + research_report, investor_presentation
**v1.5:** SQLite FTS, cross-period comparison, KeyPhrase extraction

**Implication:** `document_type` field in CLI only accepts `10-K` for v1. `--document-type` validation rejects anything else. Pipeline stages are tested against 10-K structure only (Item sections, XBRL, HTML format).

### Patch 5: Span/Batch-Based AI Extraction Strategy

**Problem:** 10-K is 80K–150K words. Cannot fit entire document into a single AI call.

**Strategy: Span-Batch Extraction Pipeline**

```
clean.md (full document, ~100K words)
  │
  ├── extract_sections() → list of sections [Item 1A, Item 1B, Item 7, ...]
  │
  ├── build_source_spans() → spans.jsonl (~500-1500 spans, ~500-1200 chars each)
  │
  └── AI extraction (Claude Agent SDK session):
        │
        ├── Phase 1: Quote Extraction (per span batch)
        │     Input:  10-50 spans (text + metadata)
        │     Output: EvidenceQuote objects
        │     Validation: exact match against source span text
        │     Dedupe: by quote_text hash + source_span_id
        │
        ├── Phase 2: Claim Extraction (per span batch, with quotes)
        │     Input:  spans + their quotes
        │     Output: ResearchClaim objects (each links to quote IDs)
        │     Validation: every claim has supported_by_quotes
        │
        ├── Phase 3: Research Object Extraction (per document, with claims)
        │     Input:  all claims + quotes for this document
        │     Output: RiskFactor, GrowthDriver, Headwind, AssumptionCandidate
        │     Validation: every object links to claim or quote
        │
        └── Phase 4: Edge Generation (per document)
              Input:  all objects + quotes
              Output: Edge objects
              Validation: relation whitelist
```

**Batch sizing:**

```python
QUOTE_EXTRACTION_BATCH = 20  # spans per AI call for quote extraction
CLAIM_EXTRACTION_BATCH = 15   # spans (with quotes) per AI call for claim extraction
OBJECT_EXTRACTION_BATCH = "full_document"  # claims are smaller, fit more
MAX_CONTEXT_TOKENS = 180000  # Claude context window safety margin
```

**Deduplication:**

```python
def dedupe_quotes(new_quotes: list, existing: dict) -> list:
    """Deduplicate by quote_text normalized hash + source_span_id."""
    seen = set()
    unique = []
    for q in new_quotes:
        key = (normalize_text(q["quote_text"]), q["source_span_id"])
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique
```

### Patch 6: Referential Integrity — Strict Validation with Rejection

**Problem:** Objects with dangling references (e.g., a claim referencing a non-existent quote ID) corrupt the ontology graph.

**Policy:** Strict referential integrity. Dangling reference objects are **rejected**, not deferred.

```python
# Validation rules:
# - quote.source_span_id MUST exist in spans.jsonl
# - claim.supported_by_quotes[*] MUST exist in evidence_quotes.jsonl
# - risk/growth/headwind/assumption supported_by_claims|quotes[*] MUST exist
# - edge.from_id/to_id IDs MUST exist in any accepted object file

# Violation handling:
# 1. Object with dangling reference → rejected_objects.jsonl (NEVER in accepted artifacts)
# 2. Trigger retry of the extraction stage that produced the invalid object
# 3. If retry fails after 3 attempts → object stays in rejected_objects.jsonl
# 4. All rejection details logged in audit_report.md

# needs_review is reserved ONLY for:
# - Ambiguous cases where evidence exists but judgment is uncertain
# - NOT for dangling references
```

**Output:** `rejected_objects.jsonl` alongside accepted artifacts. Schema matches the rejected object's type with additional `rejection_reason` and `rejection_stage` fields.

### Patch 7: 10-K/A Amendment Exclusion

**Policy:** v1 processes original 10-K filings only. 10-K/A amendments are excluded by default.

```python
# At discover_source_document:
# 1. Find latest 10-K accession number
# 2. If the filing is a 10-K/A: log amendment_skipped, continue searching for original 10-K
# 3. v1 has no --include-amendments flag (deferred to v1.1)

# Rationale:
# - 10-K/A mixed with 10-K would corrupt period comparison and source_document_id
# - Amendment diffing is a separate feature (v1.1)
```

**Deferred to v1.1:** `--include-amendments` flag, 10-K/A as source type, amendment diffing logic.

### Patch 8: Table Handling Strategy

**Policy:** Markdown tables for AI context; XBRL facts as numeric source of truth.

```python
TABLE_HANDLING = {
    "raw_html": "preserve as-is (original filing)",
    "clean_md": "convert HTML tables to markdown tables (best-effort)",
    "spans": "include table text in spans (AI reads table content)",
    "numeric_verification": "XBRL facts only (not markdown table values)",
    "complex_tables": {
        "status": "table_extraction_status: deferred",
        "flag": "needs_table_review: true",
        "reason": "tables with merged cells, nested structures, or XBRL mismatches"
    }
}

# Structured table extraction (pandas/DataFrame) deferred to v1.5
# v1 principle: markdown table is for AI reading context, not for data extraction
```

### Patch 9: Pipeline Config Reproducibility

**Problem:** No way to record which CLI flags, model, and schema versions were used for a run.

**Fix:** Write `pipeline_config.json` at the start of every pipeline run:

```json
{
  "cli_version": "0.1.0",
  "model": "claude-sonnet-4-20250514",
  "schema_version": "0.1.0",
  "cli_flags": {"document_type": "10-K", "latest": true},
  "started_at": "2026-05-02T14:30:00Z",
  "python_version": "3.12.3"
}
```

**Rule:** Model name and pricing come from project config, not hardcoded in pipeline code.

### Patch 10: Content Hash for Change Detection

**Fix:** Store SHA-256 of `raw.html` in `metadata.json`. At `discover_source_document`, compare against stored hash. Skip processing if unchanged (override with `--force`).

```json
{
  "metadata": {
    "raw_html_sha256": "abc123...",
    "raw_html_bytes": 1234567
  }
}
```

### Patch 11: Runtime Cost Logging

**Policy:** No fixed cost estimates as Phase gate. Pipeline logs actual token usage and estimated cost per stage at runtime.

```python
# After each AI stage:
STAGE_LOG = {
    "stage": "extract_evidence_quotes",
    "input_tokens": 45000,
    "output_tokens": 12000,
    "estimated_cost_usd": 0.35,
    "duration_seconds": 45,
    "batches_processed": 25,
    "batches_failed": 0
}
```

### Patch 12: LanguageSignal as EvidenceQuote Optional Field

**Change:** Instead of a separate language-signal AI stage, language signals are embedded as an optional field during EvidenceQuote extraction.

```json
{
  "id": "quote:AAPL:FY2025:10K:item7a:0089:001",
  "type": "EvidenceQuote",
  "quote_text": "may adversely affect our business...",
  "language_signals": [
    {
      "signal_text": "may adversely affect",
      "signal_type": "potential_negative",
      "strength": "medium"
    }
  ]
}
```

**Pipeline impact:**
- Dedicated language-signal extraction removed from pipeline stages (no separate AI call)
- Quote extraction prompt updated to include signal classification
- Validator normalizes embedded signals into `language_signals.jsonl`
- Cost reduction: one fewer AI stage per document

### Patch 13: Metric Dictionary — 25 Canonical Metrics

**Scope:** 25 canonical metrics across 5 categories. Unmapped metrics flagged as `needs_review`.

```yaml
canonical_metrics:
  revenue_growth:
    canonical_name: revenue
    display_name: Revenue Growth
    category: revenue
    unit: percent
    description: "Year-over-year change in total revenue."
    aliases: [revenue_growth_rate, sales_growth, net_sales_growth]
    xbrl_tags: []

  # Revenue/growth (3)
  revenue:
    canonical_name: revenue
    display_name: Revenue
    category: revenue
    unit: USD
    description: "Total revenue from continuing operations."
    aliases: [total_revenue, net_sales, sales]
    xbrl_tags: [us-gaap:Revenues, us-gaap:SalesRevenueNet]
  segment_revenue:
    canonical_name: segment_revenue
    display_name: Segment Revenue
    category: revenue
    unit: USD
    description: "Revenue breakdown by business segment."
    aliases: [segment_sales]
    xbrl_tags: []

  # Margin/profitability (7)
  gross_margin:
    canonical_name: gross_margin
    display_name: Gross Margin
    category: profitability
    unit: percent
    description: "Gross profit divided by total revenue."
    aliases: [gross_profit_margin]
    xbrl_tags: []
  gross_profit:
    canonical_name: gross_profit
    display_name: Gross Profit
    category: profitability
    unit: USD
    description: "Revenue minus cost of revenue."
    aliases: []
    xbrl_tags: [us-gaap:GrossProfit]
  operating_margin:
    canonical_name: operating_margin
    display_name: Operating Margin
    category: profitability
    unit: percent
    description: "Operating income divided by total revenue."
    aliases: []
    xbrl_tags: []
  operating_income:
    canonical_name: operating_income
    display_name: Operating Income
    category: profitability
    unit: USD
    description: "Income from core business operations."
    aliases: []
    xbrl_tags: [us-gaap:OperatingIncomeLoss]
  net_income:
    canonical_name: net_income
    display_name: Net Income
    category: profitability
    unit: USD
    description: "Bottom-line profit after all expenses and taxes."
    aliases: [net_profit]
    xbrl_tags: [us-gaap:NetIncomeLoss]
  net_margin:
    canonical_name: net_margin
    display_name: Net Margin
    category: profitability
    unit: percent
    description: "Net income divided by total revenue."
    aliases: []
    xbrl_tags: []
  eps:
    canonical_name: eps
    display_name: Earnings Per Share
    category: profitability
    unit: USD_per_share
    description: "Net income available to common shareholders divided by weighted average shares."
    aliases: [earnings_per_share]
    xbrl_tags: [us-gaap:EarningsPerShareBasic]

  # Costs (4)
  cost_of_revenue:
    canonical_name: cost_of_revenue
    display_name: Cost of Revenue
    category: costs
    unit: USD
    description: "Direct costs attributable to the production of revenue."
    aliases: [cost_of_goods_sold, cogs]
    xbrl_tags: [us-gaap:CostOfGoodsAndServicesSold]
  operating_expense:
    canonical_name: operating_expense
    display_name: Operating Expense
    category: costs
    unit: USD
    description: "Total operating expenses."
    aliases: [total_operating_expenses, sg_and_a]
    xbrl_tags: [us-gaap:OperatingExpenses]
  research_and_development:
    canonical_name: research_and_development
    display_name: Research and Development
    category: costs
    unit: USD
    description: "R&D expenditure."
    aliases: [rd_expense, research_development_expense]
    xbrl_tags: [us-gaap:ResearchAndDevelopmentExpense]
  selling_general_and_admin:
    canonical_name: selling_general_and_admin
    display_name: Selling, General and Administrative
    category: costs
    unit: USD
    description: "SG&A expenditure."
    aliases: [sga, selling_general_administrative]
    xbrl_tags: [us-gaap:SellingGeneralAndAdministrativeExpense]

  # Cash flow (4)
  operating_cash_flow:
    canonical_name: operating_cash_flow
    display_name: Operating Cash Flow
    category: cash_flow
    unit: USD
    description: "Net cash from operating activities."
    aliases: [cash_from_operations]
    xbrl_tags: [us-gaap:NetCashProvidedByUsedInOperatingActivities]
  capital_expenditures:
    canonical_name: capital_expenditures
    display_name: Capital Expenditures
    category: cash_flow
    unit: USD
    description: "Cash spent on property, plant and equipment."
    aliases: [capex, pp_and_e]
    xbrl_tags: [us-gaap:PaymentsForAcquisitionOfPropertyPlantAndEquipment]
  free_cash_flow:
    canonical_name: free_cash_flow
    display_name: Free Cash Flow
    category: cash_flow
    unit: USD
    description: "Operating cash flow minus capital expenditures."
    aliases: [fcf]
    xbrl_tags: []
  fcf_margin:
    canonical_name: fcf_margin
    display_name: FCF Margin
    category: cash_flow
    unit: percent
    description: "Free cash flow divided by total revenue."
    aliases: [free_cash_flow_margin]
    xbrl_tags: []

  # Balance sheet/leverage (5)
  cash_and_equivalents:
    canonical_name: cash_and_equivalents
    display_name: Cash and Equivalents
    category: balance_sheet
    unit: USD
    description: "Cash, cash equivalents, and short-term investments."
    aliases: [cash, total_cash]
    xbrl_tags: [us-gaap:CashAndCashEquivalentsAtCarryingValue]
  total_assets:
    canonical_name: total_assets
    display_name: Total Assets
    category: balance_sheet
    unit: USD
    description: "Sum of all assets."
    aliases: []
    xbrl_tags: [us-gaap:Assets]
  total_liabilities:
    canonical_name: total_liabilities
    display_name: Total Liabilities
    category: balance_sheet
    unit: USD
    description: "Sum of all liabilities."
    aliases: []
    xbrl_tags: [us-gaap:Liabilities]
  total_debt:
    canonical_name: total_debt
    display_name: Total Debt
    category: balance_sheet
    unit: USD
    description: "Short-term debt plus current portion of long-term debt plus long-term debt."
    aliases: [debt]
    xbrl_tags: [us-gaap:ShortTermBorrowings, us-gaap:LongTermDebt]
  shareholders_equity:
    canonical_name: shareholders_equity
    display_name: Shareholders' Equity
    category: balance_sheet
    unit: USD
    description: "Total owners' equity."
    aliases: [total_equity, stockholders_equity]
    xbrl_tags: [us-gaap:StockholdersEquity]

  # Efficiency/returns (2)
  roe:
    canonical_name: roe
    display_name: Return on Equity
    category: efficiency
    unit: percent
    description: "Net income divided by average shareholders' equity."
    aliases: [return_on_equity]
    xbrl_tags: []
  roa:
    canonical_name: roa
    display_name: Return on Assets
    category: efficiency
    unit: percent
    description: "Net income divided by average total assets."
    aliases: [return_on_assets]
    xbrl_tags: []
```

**Key rule:** `revenue` is canonical. `total_revenue`, `net_sales`, `sales` are aliases. AI extraction maps mentions to canonical names. Unmapped metrics are flagged `needs_review` — v1 does not auto-add new metrics.

### Patch 14: Empty Section Handling

**Policy:** `extract_sections` returns only sections present in the document. Missing sections produce an empty list, not an error.

```python
# Some 10-Ks omit sections (e.g., Item 1B if no unresolved staff comments).
# extract_sections handles this gracefully:
# - Missing section → empty list for that item
# - build_source_spans → zero spans from empty sections
# - No error, no warning, no special logging
# - audit_report.md lists "Sections found: [Item 1, Item 1A, Item 7, Item 7A, Item 8]"
#   to document what was actually present
```

```python
PIPELINE_STAGES = [
    "resolve_ticker",
    "discover_source_document",
    "download_source_document",
    "clean_to_markdown",
    "extract_sections",
    "build_source_spans",
    "extract_xbrl_facts",          # CODE
    "extract_evidence_quotes",    # AI (language_signals extracted as optional field)
    "extract_research_claims",     # AI
    "extract_risks_drivers_headwinds",  # AI
    "extract_assumption_candidates",      # AI
    "generate_edges",              # AI
    "validate_ontology",           # CODE
    "build_indexes",               # CODE
    "build_graph_report",          # CODE
]

# Note: dedicated language-signal extraction was removed — language signals are now embedded as
# an optional field in EvidenceQuote objects during quote extraction. The validator
# normalizes these into language_signals.jsonl. No separate AI call needed.

# Checkpoint after every stage — if stage N fails, resume from stage N
# AI stages produce intermediate files that survive pipeline restart
# CODE stages produce deterministic outputs that can be re-run idempotently
```

**Retry logic for AI stages:**
- Retry up to 3 times with exponential backoff
- On persistent batch failure: write `batch_failures.jsonl`, produce no accepted objects for that batch, continue
- Pipeline never blocks on a single extraction failure

---

## 3. v1 MVP Target

**Ticker:** AAPL
**Document:** Latest 10-K (FY2025)
**End-to-end:** `krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest`

**Expected outputs (from PRD Section 18.2):**

```text
companies/AAPL/sources/10K/FY2025/raw.html
companies/AAPL/sources/10K/FY2025/clean.md
companies/AAPL/sources/10K/FY2025/metadata.json
companies/AAPL/ontology/10K/FY2025/spans.jsonl
companies/AAPL/ontology/10K/FY2025/evidence_quotes.jsonl
companies/AAPL/ontology/10K/FY2025/language_signals.jsonl
companies/AAPL/ontology/10K/FY2025/claims.jsonl
companies/AAPL/ontology/10K/FY2025/rejected_objects.jsonl
companies/AAPL/ontology/10K/FY2025/batch_failures.jsonl
companies/AAPL/ontology/10K/FY2025/risks.jsonl
companies/AAPL/ontology/10K/FY2025/growth_drivers.jsonl
companies/AAPL/ontology/10K/FY2025/headwinds.jsonl
companies/AAPL/ontology/10K/FY2025/assumption_candidates.jsonl
companies/AAPL/ontology/10K/FY2025/edges.jsonl
companies/AAPL/ontology/10K/FY2025/xbrl_facts.jsonl
companies/AAPL/ontology/10K/FY2025/artifact_index.json
companies/AAPL/ontology/10K/FY2025/graph_report.md
companies/AAPL/ontology/10K/FY2025/audit_report.md
companies/AAPL/ontology/10K/FY2025/pipeline_config.json
companies/AAPL/indexes/company_artifact_index.json
```

**MVP validation criteria (from PRD Section 25):**
1. All accepted quotes exact match source spans
2. All accepted claims have quote support
3. All accepted relations are allowed
4. No accepted numeric claims without source support
5. Claude Code can answer: "AAPL FY2025 10-K에서 gross margin downside에 중요한 문구 찾아줘" with quote_text, source path, confidence

---

## 4. Implementation Order

### Phase 1: Foundation (no AI needed)
1. Project structure, pyproject.toml, CLI skeleton
2. Global ontology YAML configs (objects, relations, quote_types, language_signals, claim_types, risk_categories, metric_dictionary)
3. ID normalization utilities
4. `krw-ontology init-workspace`
5. `resolve_ticker` — SEC EDGAR CIK lookup
6. `discover_source_document` — find latest 10-K accession number
7. `download_source_document` — download raw HTML from EDGAR
8. `clean_to_markdown` — HTML → readable MD
9. `extract_sections` — split MD into Item sections
10. `build_source_spans` — section → spans.jsonl

### Phase 2: AI Extraction (Claude Agent SDK)
11. Claude Agent SDK extraction worker setup (session management, prompt templates)
12. `extract_evidence_quotes` — span batch → quotes + language signals (with exact match)
13. `extract_research_claims` — quotes → claims
14. `extract_risks_drivers_headwinds` — claims → risk/growth/headwind objects
15. `extract_assumption_candidates` — claims → assumption candidates
16. `generate_edges` — all objects → edges

### Phase 3: Validation + Index
17. Python validators (schema, exact match, claim support, numeric guard, relation whitelist)
18. `krw-ontology validate` command
19. `build_indexes` — artifact_index, company_artifact_index
20. `build_graph_report` — graph_report.md, audit_report.md
21. AGENTS.md / CLAUDE.md templates for Claude Code query interface

### Phase 4: Integration + MVP Test
22. `krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest` (end-to-end)
23. Test with actual AAPL 10-K
24. Validate against MVP acceptance criteria
25. Test Claude Code query interface

---

## 5. Key Technical Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | File-canonical, no DB in v1 | PRD core principle. Git diff reviewable. |
| 2 | EvidenceQuote as 1st-class object | All claims trace to exact source text. |
| 3 | AI extracts, code validates | Deterministic validation is non-negotiable for financial data. |
| 4 | Schema-governed extraction | objects.yaml, relations.yaml constrain AI output. |
| 5 | v1 = 10-K only | Reduce surface area. Test with one filing type first. |
| 6 | Span-batch extraction | 10-K too large for single AI call. |
| 7 | Claude Agent SDK for extraction only | Pipeline is Python, not an agent. Query interface stays Claude Code. |
| 8 | Checkpoint after every stage | Never lose progress on 150K-word document processing. |
| 9 | ResearchObject = shared schema for Risk/GrowthDriver/Headwind | Reduce schema surface. Type field discriminates. |
| 10 | Metrics are canonical, not per-ticker | Referenced by name, not duplicated. |
| 11 | Strict referential integrity (rejected, not needs_review) | Dangling references corrupt the graph; rejection is the only safe default. |
| 12 | 10-K/A excluded by default in v1 | Amendments corrupt period comparison and source IDs. |
| 13 | LanguageSignal as EvidenceQuote optional field | Eliminates one AI stage call; validator normalizes to JSONL. |
| 14 | XBRL as numeric source of truth | Markdown tables are for AI context; XBRL for verification. |
| 15 | Runtime cost logging, not fixed estimates | Actual token usage logged per stage; estimates are reference-only. |

---

## 6. Things Explicitly Deferred

| Item | Target Version |
|------|---------------|
| 10-Q support | v1.1 |
| 10-K/A amendment support (--include-amendments) | v1.1 |
| --dry-run flag | v1.1 |
| Earnings call support | v1.2 |
| SQLite FTS search index | v1.5 |
| Cross-period comparison | v1.5 |
| KeyPhrase extraction | v1.5 |
| Kuzu graph query | v2 |
| Postgres + pgvector | v3 |
| Valuation model integration | v4 |
| Multi-user support | v3 |
| Web UI | v3 |
