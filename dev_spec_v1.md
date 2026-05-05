# Development Specification: 10-K Evidence Ontology Workspace — v1

**Date:** 2026-05-02
**Status:** Pre-implementation

## Document Authority

`dev_spec_v1.md` is the implementation source of truth for v1. If this file conflicts with the PRD or design document, implement this file.

- **Implementation source of truth:** `dev_spec_v1.md`
- **Product intent reference:** `10_k_thin_ontology_builder_prd.md`
- **Architecture/design reference:** `design_doc_v1.md`
- **Preserved product principles:** file-canonical, quote-first, EvidenceQuote-centered, AI extracts/code validates, strict reference integrity, DB-ready but not DB-first, Claude Code/Codex as a read-only query interface over generated artifacts.

## Product Boundary

The v1 ontology is an evidence organization engine, not an investment opinion engine.

Canonical artifacts may store:

- Exact source evidence, evidence-backed claims, language signals, document-grounded risk/growth/headwind classifications, canonical metric references, and reviewable modeling cues.

Canonical artifacts must not store:

- Buy/sell/hold recommendations, target prices, portfolio advice, or final forecasts. Downstream AI may generate those opinions at query time by reading the ontology plus current market/user context.

---

## 1. Project Structure

```
krw-ontology/
├── pyproject.toml
├── README.md
├── CLAUDE.md                          # Query interface instructions for Claude Code
├── src/
│   └── krw_ontology/
│       ├── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py               # Typer CLI app
│       ├── config/
│       │   ├── __init__.py
│       │   ├── settings.py            # PipelineConfig, model config loader
│       │   └── constants.py          # DOCUMENT_TYPE_KEY/DISPLAY mapping, batch sizes
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── orchestrator.py       # Pipeline orchestration, checkpoint, retry
│       │   ├── stages/
│       │   │   ├── __init__.py
│       │   │   ├── resolve_ticker.py
│       │   │   ├── discover_source.py
│       │   │   ├── download_source.py
│       │   │   ├── clean_markdown.py
│       │   │   ├── extract_sections.py
│       │   │   ├── build_spans.py
│       │   │   ├── extract_xbrl.py
│       │   │   ├── build_numeric_evidence.py
│       │   │   └── build_indexes.py
│       │   └── checkpoint.py          # Checkpoint read/write, stage completion tracking
│       ├── extraction/
│       │   ├── __init__.py
│       │   ├── worker.py             # Claude Agent SDK session management
│       │   ├── prompts/
│       │   │   ├── __init__.py
│       │   │   ├── quote_extraction.py
│       │   │   ├── claim_extraction.py
│       │   │   ├── object_extraction.py
│       │   │   └── edge_generation.py
│       │   └── schemas.py            # Pydantic models for AI output validation
│       ├── validation/
│       │   ├── __init__.py
│       │   ├── schema_validator.py   # JSON schema / Pydantic validation
│       │   ├── exact_match.py        # Quote text exact match against source spans
│       │   ├── reference_validator.py # ID referential integrity
│       │   ├── support_validator.py  # Claim/quote support requirement
│       │   ├── metric_validator.py   # Canonical metric mapping + unmapped metrics
│       │   ├── numeric_guard.py      # Numeric claim XBRL cross-check
│       │   └── relation_validator.py # Edge relation whitelist check
│       ├── schema/
│       │   ├── __init__.py
│       │   ├── objects.py           # Object type definitions and ID generation
│       │   └── id_utils.py          # normalize_doc_type, generate_id, etc.
│       └── utils/
│           ├── __init__.py
│           ├── io.py                # JSONL read/write, atomic file write
│           └── logging.py           # Structured logging setup
├── ontology/
│   └── schema/
│       ├── objects.yaml             # Object type definitions
│       ├── relations.yaml           # Relation definitions (list format)
│       ├── quote_types.yaml         # EvidenceQuote type taxonomy
│       ├── language_signals.yaml    # LanguageSignal type taxonomy
│       ├── claim_types.yaml         # ResearchClaim type taxonomy
│       ├── risk_categories.yaml     # RiskFactor category taxonomy
│       └── metric_dictionary.yaml   # 25 canonical metrics + aliases
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_id_utils.py
│   │   ├── test_schema_validator.py
│   │   ├── test_exact_match.py
│   │   ├── test_reference_validator.py
│   │   ├── test_support_validator.py
│   │   ├── test_metric_validator.py
│   │   ├── test_numeric_guard.py
│   │   ├── test_relation_validator.py
│   │   ├── test_checkpoint.py
│   │   └── test_io.py
│   ├── fixtures/
│   │   ├── mini_10k.html          # Minimal synthetic 10-K for fast tests
│   │   ├── mini_10k_clean.md
│   │   └── mini_10k_spans.jsonl
│   └── integration/
│       ├── __init__.py
│       └── test_pipeline_mini_10k.py
├── design_doc_v1.md
├── 10_k_thin_ontology_builder_prd.md
└── dev_spec_v1.md                  # This file
```

### Runtime Output Structure (per company/document)

```
companies/
└── {ticker}/
    ├── indexes/
    │   └── company_artifact_index.json
    ├── sources/
    │   └── {doc_type_key}/
    │       └── {period}/
    │           ├── raw.html
    │           ├── clean.md
    │           └── metadata.json
    └── ontology/
        └── {doc_type_key}/
            └── {period}/
                ├── spans.jsonl
                ├── evidence_quotes.jsonl
                ├── language_signals.jsonl
                ├── claims.jsonl
                ├── risks.jsonl
                ├── growth_drivers.jsonl
                ├── headwinds.jsonl
                ├── assumption_candidates.jsonl
                ├── edges.jsonl
                ├── xbrl_facts.jsonl
                ├── financial_metric_values.jsonl
                ├── derived_metric_values.jsonl
                ├── numeric_evidence.jsonl
                ├── calculated_numeric_support.jsonl
                ├── rejected_objects.jsonl
                ├── batch_failures.jsonl
                ├── artifact_index.json
                ├── graph_report.md
                ├── audit_report.md
                └── pipeline_config.json
```

---

## 2. pyproject.toml

```toml
[project]
name = "krw-ontology"
version = "0.1.0"
description = "10-K Evidence Ontology Builder — file-canonical equity research pipeline"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.0",
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "markdownify>=0.12",
    "lxml>=5.0",
    "claude-agent-sdk",          # Claude Code Agent SDK for extraction worker
    "pyyaml>=6.0",
    "rich>=13.0",               # Progress bars, logging
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "mypy>=1.10",
]

[project.scripts]
krw-ontology = "krw_ontology.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.backends"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: marks tests that require network or API calls",
]
```

---

## 3. CLI Specification

The public v1 CLI contract is the `krw-ontology` console script. Stage-level commands such as standalone ingest, span build, or extraction commands are not public v1 commands; they may be added later as internal/debug or future commands.

### 3.1 Commands

```
krw-ontology init-workspace
    Creates ontology/schema/ directory with starter YAML configs.

krw-ontology build-evidence-ontology TICKER
    --document-type TEXT    # "10-K" only in v1 (validated)
    --latest                # Use most recent filing
    --period TEXT           # Explicit period override (e.g., "FY2025")
    --force                 # Re-process even if content hash matches
    --output-dir PATH       # Override default output directory

krw-ontology validate TICKER
    --document-type TEXT
    --period TEXT
    # Re-runs validation on existing ontology artifacts

krw-ontology build-report TICKER
    --document-type TEXT
    --period TEXT
    # Regenerates graph_report.md and audit_report.md from existing artifacts
```

### 3.2 CLI Validation Rules

```python
# document_type validation for v1
ACCEPTED_DOC_TYPES = {"10-K"}
ACCEPTED_DOC_TYPES_KEYS = {"10K"}

def validate_document_type(doc_type: str) -> str:
    """Raise typer.BadParameter if not in ACCEPTED_DOC_TYPES."""
    if doc_type not in ACCEPTED_DOC_TYPES:
        raise typer.BadParameter(
            f"Document type '{doc_type}' not supported in v1. "
            f"Supported: {', '.join(sorted(ACCEPTED_DOC_TYPES))}"
        )
    return doc_type
```

---

## 4. Configuration Files

### 4.1 pipeline_config.json

Written at the start of every pipeline run. Immutable once written.

```json
{
  "cli_version": "0.1.0",
  "model": "claude-sonnet-4-20250514",
  "schema_version": "0.1.0",
  "cli_flags": {
    "document_type": "10-K",
    "latest": true,
    "force": false
  },
  "started_at": "2026-05-02T14:30:00Z",
  "python_version": "3.12.3",
  "platform": "macOS"
}
```

**Field rules:**
- `model`: loaded from project config or environment variable `KRW_MODEL`. Never hardcoded in pipeline code.
- `schema_version`: read from `ontology/schema/objects.yaml` header.
- `cli_flags`: exact CLI flags used for this run.

### 4.2 Project Config (~/.config/krw-ontology/config.yaml or .krw-ontology.yaml)

```yaml
model: claude-sonnet-4-20250514
max_retries: 3
retry_base_delay_seconds: 5
sec_user_agent: "krw-ontology/0.1 contact@example.com"
batch_sizes:
  quote_extraction: 20
  claim_extraction: 15
max_context_tokens: 180000
```

`sec_user_agent` must be set to a descriptive value before live SEC requests. The placeholder email is not valid for production use.

### 4.3 metadata.json

```json
{
  "ticker": "AAPL",
  "cik": "0000320193",
  "accession_number": "0000320193-25-000001",
  "filing_date": "2025-11-01",
  "document_type": "10-K",
  "doc_type_key": "10K",
  "period": "FY2025",
  "fiscal_year_end": "2025-09-27",
  "raw_html_sha256": "abc123def456...",
  "raw_html_bytes": 1234567,
  "clean_md_sha256": "789abc012def...",
  "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/aapl-20250927.htm",
  "extracted_at": "2025-11-02T10:30:00Z",
  "pipeline_version": "0.1.0",
  "schema_version": "0.1.0"
}
```

**Field rules:**
- `document_type`: display value ("10-K").
- `doc_type_key`: path-safe value ("10K"), used in directory names and IDs.
- `raw_html_sha256`: SHA-256 hex digest of the downloaded raw HTML. Used for change detection.
- `cik`: SEC EDGAR Central Index Key (zero-padded 10 digits).
- `period`: derived from fiscal year end date. Format: `FY{year}`.

---

## 5. ID Convention

### 5.1 Format

Most object IDs are scoped by ticker, period, document type, and a local ID. `SourceDocument` and `Metric` are special cases.

```
source:{ticker}:{period}:{doc_type_key}
{object_type}:{ticker}:{period}:{doc_type_key}:{local_id}
metric:{canonical_name}
```

Examples:
```
source:AAPL:FY2025:10K
span:AAPL:FY2025:10K:item1a:0042
quote:AAPL:FY2025:10K:item7a:0089:001
claim:AAPL:FY2025:10K:revenue-growth-acceleration
risk:AAPL:FY2025:10K:supply-chain-disruption
xbrl:AAPL:FY2025:10K:GrossProfit:a1b2c3d4
metric:gross_margin
```

### 5.2 ID Generation Rules

```python
def generate_source_document_id(ticker: str, period: str, doc_type_key: str) -> str:
    """SourceDocument has no local_id suffix."""
    return f"source:{ticker}:{period}:{doc_type_key}"

def generate_scoped_id(
    object_type: str,
    ticker: str,
    period: str,
    doc_type_key: str,
    local_id: str,
) -> str:
    """Generate IDs for objects that require a local_id."""
    return f"{object_type}:{ticker}:{period}:{doc_type_key}:{local_id}"

def generate_metric_id(canonical_name: str) -> str:
    """Metrics are canonical, not ticker- or period-scoped."""
    return f"metric:{canonical_name}"

# local_id rules:
# SourceDocument: no local_id; use generate_source_document_id()
# SourceSpan: "{section_name}:{sequence:04d}" e.g. "item7a:0042"
# EvidenceQuote: "{section_name}:{sequence:04d}:{quote_seq:03d}" e.g. "item7a:0089:001"
# ResearchClaim: "{slug}" e.g. "revenue-growth-acceleration"
# ResearchObject: "{slug}" e.g. "supply-chain-disruption"
# Metric: no ticker/period; use generate_metric_id()
# XBRLFact: "{safe_taxonomy_tag}:{hash8}" e.g. "GrossProfit:a1b2c3d4"
# Edge: "{relation_id}:{hash10}" e.g. "supports:a3f29bc8e1"
# LanguageSignal: "{section_name}:{sequence:04d}:{signal_seq:03d}" (same parent as quote)
```

`XBRLFact` IDs must include a context/value hash because the same taxonomy tag can appear multiple times in one filing. `Edge` IDs must include the relation because the same endpoints can have multiple valid relationships.

### 5.3 Document Type Mapping

```python
DOCUMENT_TYPE_DISPLAY = "10-K"   # metadata, human-readable
DOCUMENT_TYPE_KEY = "10K"         # paths, IDs

MAPPING = {
    "10-K": "10K",
    "10-Q": "10Q",
    "earnings_call": "EARNINGS_CALL",
    "research_report": "RESEARCH_REPORT",
    "valuation_model": "VALUATION_MODEL",
}

def normalize_doc_type(display: str) -> str:
    """10-K → 10K, 10-Q → 10Q, etc."""
    return MAPPING[display]

def denormalize_doc_type(key: str) -> str:
    """10K → 10-K, 10Q → 10-Q, etc."""
    return {v: k for k, v in MAPPING.items()}[key]
```

---

## 6. YAML Schema Definitions

### 6.1 objects.yaml

```yaml
schema_version: "0.1.0"

types:
  SourceDocument:
    id_pattern: "source:{ticker}:{period}:{doc_type_key}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["SourceDocument"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: false}
      document_type: {type: string, required: true}  # display value
      period: {type: string, required: true}
      metadata_ref: {type: string, description: "path to metadata.json", required: false}
      schema_version: {type: string, required: true}

  SourceSpan:
    id_pattern: "span:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["SourceSpan"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      section_name: {type: string, required: true}
      section_number: {type: string, required: true}
      span_index: {type: integer, required: true}
      start_char: {type: integer, required: true, description: "Absolute offset within clean.md"}
      end_char: {type: integer, required: true, description: "Absolute offset within clean.md"}
      text: {type: string, required: true}
      text_hash: {type: string, required: true, description: "sha256 hash of normalized span text"}
      char_count: {type: integer, required: true}
      section_detection_confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      section_detection_method: {type: string, required: true}
      schema_version: {type: string, required: true}

  EvidenceQuote:
    id_pattern: "quote:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}:{quote_seq:03d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["EvidenceQuote"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      source_span_id: {type: string, required: true}
      quote_text: {type: string, required: true}
      quote_type: {type: string, enum_ref: "quote_types.yaml", required: true}
      section_name: {type: string, required: true}
      start_char: {type: integer, required: false, description: "Optional offset within SourceSpan.text"}
      end_char: {type: integer, required: false, description: "Optional offset within SourceSpan.text"}
      absolute_start_char: {type: integer, required: false, description: "Optional derived offset within clean.md"}
      absolute_end_char: {type: integer, required: false, description: "Optional derived offset within clean.md"}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      language_signals: {type: array, items: LanguageSignal, required: false, description: "Optional. Embedded during quote extraction. Validator normalizes to language_signals.jsonl."}
      schema_version: {type: string, required: true}

  LanguageSignal:
    id_pattern: "signal:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}:{signal_seq:03d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["LanguageSignal"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      source_quote_id: {type: string, required: true}
      signal_text: {type: string, required: true}
      signal_type: {type: string, enum_ref: "language_signals.yaml", required: true}
      strength: {type: string, enum: ["strong", "medium", "weak"], required: true}
      direction: {type: string, enum: ["positive", "negative", "neutral", "risk"], required: true}
      certainty: {type: string, enum: ["observed", "conditional", "expected", "uncertain", "structural"], required: true}
      temporal_scope: {type: string, enum: ["historical", "current", "future_or_potential", "ongoing"], required: true}
      exact_match_verified: {type: boolean, required: true}
      schema_version: {type: string, required: true}

  ResearchClaim:
    id_pattern: "claim:{ticker}:{period}:{doc_type_key}:{slug}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["ResearchClaim"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      claim_text: {type: string, required: true}
      claim_type: {type: string, enum_ref: "claim_types.yaml", required: true}
      supported_by_quotes: {type: array, items: string, required: true, min_items: 1}
      related_metrics: {type: array, items: string, required: false, description: "Canonical metric names from metric_dictionary.yaml"}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}

  ResearchObject:
    description: "Shared schema for RiskFactor, GrowthDriver, Headwind. Type field discriminates."
    id_pattern: "{type_key}:{ticker}:{period}:{doc_type_key}:{slug}"
    type_key: "risk|growth_driver|headwind"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["RiskFactor", "GrowthDriver", "Headwind"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      name: {type: string, required: true}
      category: {type: string, required: true}
      description: {type: string, required: true}
      supported_by_claims: {type: array, items: string, required: false}
      supported_by_quotes: {type: array, items: string, required: false}
      affects: {type: array, items: string, required: false, description: "Canonical metric names from metric_dictionary.yaml. Only include confident mappings."}
      unmapped_impacts: {type: array, items: string, required: false, description: "Impact labels that did not map confidently to canonical metrics."}
      unmapped_metrics: {type: array, items: string, required: false}
      qualitative_impact: {type: string, required: true}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}

  AssumptionCandidate:
    description: "Reviewable modeling cue. This is not a final forecast assumption, investment opinion, target price, or recommendation."
    id_pattern: "assumption:{ticker}:{period}:{doc_type_key}:{slug}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["AssumptionCandidate"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      name: {type: string, required: true}
      assumption_text: {type: string, required: true}
      assumption_type: {type: string, enum: ["growth_rate", "margin", "capex", "tax_rate", "wacc", "other"], required: true}
      value_hint: {type: string, required: false}
      supported_by_claims: {type: array, items: string, required: false}
      supported_by_quotes: {type: array, items: string, required: false}
      related_metrics: {type: array, items: string, required: false, description: "Canonical metric names from metric_dictionary.yaml. Only include confident mappings."}
      unmapped_metrics: {type: array, items: string, required: false}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "needs_review"}
      schema_version: {type: string, required: true}

  Metric:
    id_pattern: "metric:{canonical_name}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["Metric"], required: true}
      name: {type: string, required: true}
      category: {type: string, required: true}
      unit: {type: string, required: true}
      description: {type: string, required: true}
      schema_version: {type: string, required: true}

  XBRLFact:
    id_pattern: "xbrl:{ticker}:{period}:{doc_type_key}:{safe_taxonomy_tag}:{hash8}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["XBRLFact"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      taxonomy_tag: {type: string, required: true}
      safe_taxonomy_tag: {type: string, required: true}
      value: {type: number, required: true}
      unit: {type: string, required: true}
      context_ref: {type: string, required: true}
      source_filing_detail: {type: string, required: true}
      decimals: {type: integer, required: false}
      schema_version: {type: string, required: true}

  Edge:
    id_pattern: "edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["Edge"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      from_id: {type: string, required: true}
      to_id: {type: string, required: true}
      relation_name: {type: string, required: true, description: "Name from relations.yaml"}
      relation_id: {type: string, required: true, description: "ID from relations.yaml"}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}
```

**Cross-field rules:**
- `RiskFactor`, `GrowthDriver`, `Headwind`, and `AssumptionCandidate` must have at least one non-empty support field: `supported_by_claims` or `supported_by_quotes`.
- `SourceSpan.start_char` and `SourceSpan.end_char` are absolute offsets within `clean.md`.
- `EvidenceQuote.start_char` and `EvidenceQuote.end_char` are optional offsets within `SourceSpan.text`.
- `EvidenceQuote.absolute_start_char` and `EvidenceQuote.absolute_end_char` are optional derived offsets within `clean.md`, computed as `SourceSpan.start_char + quote.start_char` when quote offsets are available.
- `EvidenceQuote.quote_text` exact match against `SourceSpan.text` is required; quote offsets are not required in v1.
- `LanguageSignal.signal_text` must exact match text inside its parent `EvidenceQuote.quote_text`; `exact_match_verified` records that validation result.
- `XBRLFact.id` local hash is `sha1(context_ref + "|" + unit + "|" + normalized_value)[:8]`.
- `Edge.id` hash is `sha1(from_id + "|" + relation_id + "|" + to_id)[:10]`.

### 6.2 relations.yaml (list format — C1 fix)

```yaml
schema_version: "0.1.0"

relations:
  - id: contains
    name: contains
    from: SourceDocument
    to: SourceSpan

  - id: contains_quote
    name: contains_quote
    from: SourceSpan
    to: EvidenceQuote

  - id: has_signal
    name: has_signal
    from: EvidenceQuote
    to: LanguageSignal

  - id: supports
    name: supports
    from: EvidenceQuote
    to: ResearchClaim

  - id: describes_risk
    name: describes_risk
    from: ResearchClaim
    to: RiskFactor

  - id: describes_driver
    name: describes_driver
    from: ResearchClaim
    to: GrowthDriver

  - id: describes_headwind
    name: describes_headwind
    from: ResearchClaim
    to: Headwind

  - id: affects_risk
    name: affects
    from: RiskFactor
    to: Metric

  - id: affects_growth
    name: affects
    from: GrowthDriver
    to: Metric

  - id: affects_headwind
    name: affects
    from: Headwind
    to: Metric

  - id: supports_assumption
    name: supports_assumption
    from: EvidenceQuote
    to: AssumptionCandidate

  - id: derived_from
    name: derived_from
    from: AssumptionCandidate
    to: ResearchClaim
```

### 6.3 quote_types.yaml

```yaml
quote_types:
  risk_language:
    description: "Language describing potential or actual downside risk."
    ai_guidance: "Look for words like: risk, may adversely, could negatively, potential loss, exposure, volatility, uncertainty."

  business_description:
    description: "Language describing how the company operates or makes money."
    ai_guidance: "Look for words like: we operate, our business model, revenue streams, customer base, geographic segments."

  revenue_driver:
    description: "Language explaining growth or decline in sales."
    ai_guidance: "Look for words like: grew, increased, revenue, sales, demand, expansion."

  margin_driver:
    description: "Language explaining margin, cost structure, or profitability."
    ai_guidance: "Look for words like: margin, profitability, cost structure, gross profit, operating leverage."

  headwind:
    description: "Language describing pressure on demand, revenue, margin, or operations."
    ai_guidance: "Look for words like: headwind, pressure, challenge, headwinds, weakening."

  growth_driver:
    description: "Language describing positive business momentum."
    ai_guidance: "Look for words like: opportunity, tailwinds, growth, momentum, expanding."

  competitive_pressure:
    description: "Language describing competition, substitution, pricing pressure, or market share risk."
    ai_guidance: "Look for words like: competitive, competition, market share, pricing pressure, substitute."

  regulatory_exposure:
    description: "Language describing regulation, litigation, compliance, or legal exposure."
    ai_guidance: "Look for words like: regulatory, litigation, compliance, legal, government, investigation."

  supply_chain:
    description: "Language describing suppliers, manufacturing, logistics, inventory, or production risk."
    ai_guidance: "Look for words like: supplier, supply chain, manufacturing, logistics, inventory, component."

  assumption_support:
    description: "Language that can support a valuation or forecast assumption."
    ai_guidance: "Look for words like: assume, expected to, we anticipate, forecast, guidance."
```

### 6.4 language_signals.yaml

```yaml
signal_types:
  potential_negative:
    description: "Language indicating a possible future negative outcome."
    strength_values: ["strong", "medium", "weak"]

  actual_negative:
    description: "Language confirming a negative outcome has occurred."
    strength_values: ["strong", "medium", "weak"]

  potential_positive:
    description: "Language indicating a possible future positive outcome."
    strength_values: ["strong", "medium", "weak"]

  actual_positive:
    description: "Language confirming a positive outcome has occurred."
    strength_values: ["strong", "medium", "weak"]

  uncertainty:
    description: "Language indicating lack of certainty about an outcome."
    strength_values: ["strong", "medium", "weak"]

  obligation:
    description: "Language indicating a commitment or binding requirement."
    strength_values: ["strong", "medium", "weak"]

  mitigation:
    description: "Language describing steps taken to reduce risk."
    strength_values: ["strong", "medium", "weak"]
```

### 6.5 claim_types.yaml

```yaml
claim_types:
  factual:
    description: "Verifiable factual statement from the filing."
    example: "Services revenue grew 14% year-over-year to $85.2 billion."

  forward_looking:
    description: "Management's expectation or projection about future performance."
    example: "We expect services revenue to continue growing at a double-digit rate."

  risk_assessment:
    description: "Statement characterizing a risk factor or its potential impact."
    example: "Intensifying competition in smartphones could materially reduce our market share."

  strategic:
    description: "Statement about strategic direction, priorities, or investments."
    example: "We are investing heavily in Apple Intelligence across all our products."

  assumption:
    description: "Implicit or explicit assumption underlying a projection."
    example: "We assume stable foreign exchange rates relative to the prior year."
```

### 6.6 risk_categories.yaml

```yaml
risk_categories:
  macro_economic:
    description: "Macroeconomic factors affecting the business."
    examples: ["foreign_exchange", "interest_rate", "inflation", "recession"]

  competitive:
    description: "Competitive dynamics in the market."
    examples: ["market_share_pressure", "pricing_pressure", "new_entrants", "substitution_risk"]

  regulatory:
    description: "Government regulation, litigation, or legal risk."
    examples: ["antitrust", "privacy_regulation", "tax_changes", "trade_restrictions"]

  operational:
    description: "Risks in day-to-day operations."
    examples: ["supply_chain_disruption", "manufacturing_risk", "cybersecurity", "talent_retention"]

  technology:
    description: "Technology-related risks."
    examples: ["rapid_technological_change", "intellectual_property", "ai_risk"]

  financial:
    description: "Financial structure and performance risks."
    examples: ["debt_ levels", "credit_risk", "customer_concentration", "acquisition_risk"]
```

### 6.7 metric_dictionary.yaml

```yaml
schema_version: "0.1.0"

canonical_metrics:
  # ── Revenue/growth (3) ──────────────────────────────
  revenue:
    display_name: Revenue
    category: revenue
    unit: USD
    description: "Total revenue from continuing operations."
    aliases: [total_revenue, net_sales, sales]
    xbrl_tags: [us-gaap:Revenues, us-gaap:SalesRevenueNet]

  revenue_growth:
    display_name: Revenue Growth
    category: revenue
    unit: percent
    description: "Year-over-year change in total revenue."
    aliases: [revenue_growth_rate, sales_growth, net_sales_growth]
    xbrl_tags: []

  segment_revenue:
    display_name: Segment Revenue
    category: revenue
    unit: USD
    description: "Revenue breakdown by business segment."
    aliases: [segment_sales]
    xbrl_tags: []

  # ── Margin/profitability (7) ──────────────────────
  gross_margin:
    display_name: Gross Margin
    category: profitability
    unit: percent
    description: "Gross profit divided by total revenue."
    aliases: [gross_profit_margin]
    xbrl_tags: []

  gross_profit:
    display_name: Gross Profit
    category: profitability
    unit: USD
    description: "Revenue minus cost of revenue."
    aliases: []
    xbrl_tags: [us-gaap:GrossProfit]

  operating_margin:
    display_name: Operating Margin
    category: profitability
    unit: percent
    description: "Operating income divided by total revenue."
    aliases: []
    xbrl_tags: []

  operating_income:
    display_name: Operating Income
    category: profitability
    unit: USD
    description: "Income from core business operations."
    aliases: []
    xbrl_tags: [us-gaap:OperatingIncomeLoss]

  net_income:
    display_name: Net Income
    category: profitability
    unit: USD
    description: "Bottom-line profit after all expenses and taxes."
    aliases: [net_profit]
    xbrl_tags: [us-gaap:NetIncomeLoss]

  net_margin:
    display_name: Net Margin
    category: profitability
    unit: percent
    description: "Net income divided by total revenue."
    aliases: []
    xbrl_tags: []

  eps:
    display_name: Earnings Per Share
    category: profitability
    unit: USD_per_share
    description: "Net income available to common shareholders divided by weighted average shares."
    aliases: [earnings_per_share]
    xbrl_tags: [us-gaap:EarningsPerShareBasic]

  # ── Costs (4) ──────────────────────────────────────
  cost_of_revenue:
    display_name: Cost of Revenue
    category: costs
    unit: USD
    description: "Direct costs attributable to the production of revenue."
    aliases: [cost_of_goods_sold, cogs]
    xbrl_tags: [us-gaap:CostOfGoodsAndServicesSold]

  operating_expense:
    display_name: Operating Expense
    category: costs
    unit: USD
    description: "Total operating expenses."
    aliases: [total_operating_expenses, sg_and_a]
    xbrl_tags: [us-gaap:OperatingExpenses]

  research_and_development:
    display_name: Research and Development
    category: costs
    unit: USD
    description: "R&D expenditure."
    aliases: [rd_expense, research_development_expense]
    xbrl_tags: [us-gaap:ResearchAndDevelopmentExpense]

  selling_general_and_admin:
    display_name: Selling, General and Administrative
    category: costs
    unit: USD
    description: "SG&A expenditure."
    aliases: [sga, selling_general_administrative]
    xbrl_tags: [us-gaap:SellingGeneralAndAdministrativeExpense]

  # ── Cash flow (4) ──────────────────────────────────
  operating_cash_flow:
    display_name: Operating Cash Flow
    category: cash_flow
    unit: USD
    description: "Net cash from operating activities."
    aliases: [cash_from_operations]
    xbrl_tags: [us-gaap:NetCashProvidedByUsedInOperatingActivities]

  capital_expenditures:
    display_name: Capital Expenditures
    category: cash_flow
    unit: USD
    description: "Cash spent on property, plant and equipment."
    aliases: [capex, pp_and_e]
    xbrl_tags: [us-gaap:PaymentsForAcquisitionOfPropertyPlantAndEquipment]

  free_cash_flow:
    display_name: Free Cash Flow
    category: cash_flow
    unit: USD
    description: "Operating cash flow minus capital expenditures."
    aliases: [fcf]
    xbrl_tags: []

  fcf_margin:
    display_name: FCF Margin
    category: cash_flow
    unit: percent
    description: "Free cash flow divided by total revenue."
    aliases: [free_cash_flow_margin]
    xbrl_tags: []

  # ── Balance sheet/leverage (5) ───────────────────
  cash_and_equivalents:
    display_name: Cash and Equivalents
    category: balance_sheet
    unit: USD
    description: "Cash, cash equivalents, and short-term investments."
    aliases: [cash, total_cash]
    xbrl_tags: [us-gaap:CashAndCashEquivalentsAtCarryingValue]

  total_assets:
    display_name: Total Assets
    category: balance_sheet
    unit: USD
    description: "Sum of all assets."
    aliases: []
    xbrl_tags: [us-gaap:Assets]

  total_liabilities:
    display_name: Total Liabilities
    category: balance_sheet
    unit: USD
    description: "Sum of all liabilities."
    aliases: []
    xbrl_tags: [us-gaap:Liabilities]

  total_debt:
    display_name: Total Debt
    category: balance_sheet
    unit: USD
    description: "Short-term debt plus current portion of long-term debt plus long-term debt."
    aliases: [debt]
    xbrl_tags: [us-gaap:ShortTermBorrowings, us-gaap:LongTermDebt]

  shareholders_equity:
    display_name: Shareholders' Equity
    category: balance_sheet
    unit: USD
    description: "Total owners' equity."
    aliases: [total_equity, stockholders_equity]
    xbrl_tags: [us-gaap:StockholdersEquity]

  # ── Efficiency/returns (2) ──────────────────────────
  roe:
    display_name: Return on Equity
    category: efficiency
    unit: percent
    description: "Net income divided by average shareholders' equity."
    aliases: [return_on_equity]
    xbrl_tags: []

  roa:
    display_name: Return on Assets
    category: efficiency
    unit: percent
    description: "Net income divided by average total assets."
    aliases: [return_on_assets]
    xbrl_tags: []

# ── Alias resolution rule ──────────────────────────────
#
# When AI outputs a metric name, resolve as follows:
# 1. Exact match on canonical name → use canonical
# 2. Exact match on alias → use canonical
# 3. Case-insensitive match on canonical or alias → use canonical
# 4. No match → flag as needs_review, do NOT auto-create
#
# `net_debt`, `debt_to_equity`, and `current_ratio` are intentionally not
# canonical v1 metrics. They remain unmapped and should be revisited in v1.1
# metric expansion rather than aliased to a nearby metric.
```

---

## 7. Pipeline Stage Contracts

Each stage has a defined input, output, and failure mode.

### 7.1 resolve_ticker

| Field | Value |
|-------|-------|
| **Input** | `{ticker: str}` |
| **Output** | `{cik: str, company_name: str, ticker: str, exchange: str | None}` where `cik` is zero-padded 10 digits |
| **API** | SEC company tickers JSON cache (`https://www.sec.gov/files/company_tickers.json`) |
| **HTTP policy** | Set a descriptive `User-Agent`; respect SEC request-rate guidance |
| **Failure** | Raise `PipelineStageError` with details |

### 7.2 discover_source_document

| Field | Value |
|-------|-------|
| **Input** | `CIK: str, document_type: str, latest: bool` |
| **Output** | `{accession_number: str, filing_date: str, report_date: str, primary_document: str, source_url: str}` |
| **API** | SEC submissions JSON (`https://data.sec.gov/submissions/CIK##########.json`) |
| **Discovery** | Use recent filings where `form == "10-K"`; skip `10-K/A`; build `source_url` from CIK, accession number without dashes, and `primaryDocument` |
| **10-K/A handling** | If discovered filing is 10-K/A: log `amendment_skipped` to audit log, skip and search for original 10-K |
| **Failure** | Raise `PipelineStageError` |

### 7.3 download_source_document

| Field | Value |
|-------|-------|
| **Input** | `{accession_number: str, source_url: str, output_path: Path}` |
| **Output** | `{raw_html_path: Path, sha256: str, bytes: int}` |
| **API** | `httpx.get(source_url, headers={"User-Agent": settings.sec_user_agent})` — follow redirects |
| **Post** | Compute SHA-256, write to metadata.json.raw_html_sha256 |
| **Failure** | Retry 3x with exponential backoff, then raise |

### 7.4 clean_to_markdown

| Field | Value |
|-------|-------|
| **Input** | `{raw_html_path: Path, output_path: Path}` |
| **Output** | `{clean_md_path: Path, sha256: str}` |
| **Process** | BeautifulSoup → remove script/style/nav/footer → markdownify → post-process tables → write |
| **Table handling** | Convert HTML tables to markdown tables (best-effort). Complex/merged-cell tables: include raw HTML comment marker for later extraction. |
| **Failure** | Raise `PipelineStageError` |

### 7.5 extract_sections

| Field | Value |
|-------|-------|
| **Input** | `{clean_md_path: Path}` |
| **Output** | `{sections: list[{name: str, number: str, start_line: int, end_line: int, text: str, section_detection_confidence: str, section_detection_method: str}]}` |
| **Extraction** | Regex-based section detection with table-of-contents skip heuristic |
| **v1 target sections** | Item 1, Item 1A, Item 1C, Item 7, Item 7A, Item 8 |
| **Other sections** | Best-effort only; do not block v1 if Item 9+ parsing is incomplete |
| **TOC handling** | Detect likely table-of-contents regions; do not use TOC headings as section starts. If the same Item heading appears multiple times, prefer the later body heading with substantial following content. |
| **Empty section** | Missing sections → empty list. No error. Logged in audit: "Sections found: [...]" |
| **Failure** | Raise `PipelineStageError` |

### 7.6 build_source_spans

| Field | Value |
|-------|-------|
| **Input** | `{sections: list, doc_type_key: str, ticker: str, period: str, source_document_id: str}` |
| **Output** | `{spans: list[SourceSpan], output_path: Path}` |
| **Process** | Split section text into overlapping windows of ~500-1200 chars with ~150-char overlap |
| **Target span count** | 500-1500 spans per 10-K |
| **Hashing** | Write `text_hash = "sha256:" + sha256(normalize_newlines(span_text).encode()).hexdigest()` |
| **Failure** | Raise `PipelineStageError` |

### 7.7 extract_xbrl_facts

| Field | Value |
|-------|-------|
| **Input** | `{raw_html_path: Path, ticker: str, period: str, doc_type_key: str, source_document_id: str}` |
| **Output** | `{facts: list[XBRLFact], output_path: Path}` |
| **Process** | Parse XBRL/IXBRL inline documents from HTML. Extract taxonomy tags, values, units, context refs. |
| **ID local_id** | `{safe_taxonomy_tag}:{hash8}` where `hash8 = sha1(context_ref + "|" + unit + "|" + normalized_value)[:8]` |
| **XBRL source** | Inline XBRL (us-gaap namespace) embedded in the 10-K HTML |
| **Failure** | Log missing XBRL data, continue, and set XBRL status to `missing_or_failed`. Numeric validation remains active and is limited to exact quote text when XBRL is unavailable. |

### 7.8 extract_evidence_quotes (AI)

| Field | Value |
|-------|-------|
| **Input** | `{quote_candidates_batch: list[QuoteCandidate], quote_types: list, language_signal_types: list}` |
| **Output** | `{quotes: list[EvidenceQuote]}` |
| **Batch size** | Candidates derived from 10 spans per AI call |
| **Prompt template** | `prompts/quote_extraction.py` |
| **Extraction runtime** | Claude Code Agent SDK via `claude-agent-sdk` |
| **Extraction model** | Claude Sonnet (configurable through `ClaudeAgentOptions`) |
| **Candidate policy** | Code splits `SourceSpan.text` into exact sentence-like `QuoteCandidate` records with `candidate_id`, offsets, and text. AI selects candidate IDs and classifies them; AI does not author `quote_text`. |
| **Validation (inline)** | Exact match: `quote_text` is copied by code from the selected candidate and must be a substring of the source span's `text` field |
| **language_signals** | Extracted as optional field in each quote. Signal classification uses `language_signals.yaml` types. |
| **Deduplication** | By `(normalize_text(quote_text), source_span_id)` hash |
| **Retry** | 3x with exponential backoff in the SDK worker. Persistent parent batch failure → recursively split the span batch in half and retry. Only a final 1-span leaf failure is written to `batch_failures.jsonl`; recovered split batches produce accepted objects normally. |

### 7.9 build_numeric_evidence (CODE)

| Field | Value |
|-------|-------|
| **Input** | `evidence_quotes.jsonl`, `xbrl_facts.jsonl`, `financial_metric_values.jsonl`, `derived_metric_values.jsonl`, `calculated_numeric_support.jsonl` |
| **Output** | `numeric_evidence.jsonl` |
| **Process** | Convert quote/XBRL/metric/code-calculated numbers into structured `NumericEvidence` rows. Classify numeric kind as `amount`, `percent`, or `number`; non-financial structural numbers such as dates, document sections, regulatory codes, product versions, and duration ranges are excluded by code. |
| **Purpose** | `numeric_guard` validates claim/cue/object numbers against this ledger instead of issuer-specific exception rules. Quote-sourced rows are only usable through declared support paths; non-quote XBRL/metric/code rows may support numeric claims globally. |

### 7.10 extract_research_claims (AI)

| Field | Value |
|-------|-------|
| **Input** | `{spans_batch: list[SourceSpan], quotes_for_batch: list[EvidenceQuote], claim_types: list}` |
| **Output** | `{claims: list[ResearchClaim]}` |
| **Batch size** | 15 spans (with their quotes) per AI call |
| **Validation (inline)** | Every claim must have `supported_by_quotes` with at least one valid quote ID |

### 7.11 extract_risks_drivers_headwinds (AI)

| Field | Value |
|-------|-------|
| **Input** | `{all_claims: list[ResearchClaim], all_quotes: list[EvidenceQuote], risk_categories: list, metric_dictionary: dict}` |
| **Output** | `{objects: list[ResearchObject]}` — types: RiskFactor, GrowthDriver, Headwind |
| **Scope** | Claim batches (default 8 claims per AI call). Each batch includes only its supporting quotes. Batch outputs are merged by `(type, normalized name)`. |
| **Validation (inline)** | Every object must have at least one `supported_by_claims` or `supported_by_quotes`; `affects` is optional and only for confident canonical metric mappings |

### 7.12 extract_assumption_candidates (AI)

| Field | Value |
|-------|-------|
| **Input** | `{all_claims: list[ResearchClaim], all_quotes: list[EvidenceQuote]}` |
| **Output** | `{assumptions: list[AssumptionCandidate]}` — reviewable modeling cues, not final investment assumptions |
| **Scope** | Full document |

### 7.13 generate_edges (AI)

| Field | Value |
|-------|-------|
| **Input** | `{all_objects: list[ResearchObject], all_claims: list[ResearchClaim], all_quotes: list[EvidenceQuote], relations_whitelist: list}` |
| **Output** | `{edges: list[Edge]}` |
| **Scope** | Full document |
| **Validation (inline)** | Every edge must match a relation in the whitelist by `from_id` object type, `to_id` object type, `relation_name`, and `relation_id` |

### 7.14 validate_ontology (CODE)

| Field | Value |
|-------|-------|
| **Input** | All JSONL files in the ontology directory |
| **Output** | `{accepted: dict[str, list], rejected: list[object_with_reason]}` |
| **Validators** | schema, exact_match, reference, support, metric, numeric_guard, relation_whitelist (run in order) |
| **Rejection** | Objects with dangling references → `rejected_objects.jsonl`. NOT in accepted artifacts. Retry originating stage. |
| **Output files** | Updated JSONL files (rejected removed), `rejected_objects.jsonl` written, `batch_failures.jsonl` preserved if present, `audit_report.md` updated |

### 7.15 build_indexes (CODE)

| Field | Value |
|-------|-------|
| **Input** | All accepted JSONL files |
| **Output** | `artifact_index.json`, `company_artifact_index.json` |

### 7.16 build_graph_report (CODE)

| Field | Value |
|-------|-------|
| **Input** | `artifact_index.json`, all accepted JSONL files, `rejected_objects.jsonl` |
| **Output** | `graph_report.md`, `audit_report.md` |

---

## 8. Validation Specification

### 8.1 Validation Order

Validators run sequentially. An object failing an earlier validator is not tested by later ones.

```
1. schema_validator     — Pydantic model validation (required fields, types, enums)
2. exact_match          — Quote text substring check against source span
3. reference_validator  — All ID references point to existing objects
4. support_validator    — Research objects and assumptions have claim or quote support
5. metric_validator     — Metric fields map to metric_dictionary.yaml
6. numeric_guard        — Numeric claims have matching XBRL or exact quote support
7. relation_validator   — Edge relations match whitelist
```

### 8.2 exact_match Validator

```python
def validate_exact_match(quote: dict, all_spans: dict[str, dict]) -> bool:
    """quote_text must be an exact substring of the referenced source span's text."""
    span = all_spans.get(quote["source_span_id"])
    if span is None:
        return False  # caught by reference_validator, but early exit here
    return quote["quote_text"] in span["text"]

def validate_exact_match_normalized(quote: dict, all_spans: dict) -> bool:
    """Same but with whitespace normalization."""
    quote_text = normalize_whitespace(quote["quote_text"])
    span_text = normalize_whitespace(all_spans[quote["source_span_id"]]["text"])
    return quote_text in span_text
```

### 8.3 reference_validator

```python
def validate_references(all_objects: dict[str, dict]) -> list[dict]:
    """Find all objects with dangling ID references. Return rejected objects."""
    rejected = []

    for obj_id, obj in all_objects.items():
        dangling_refs = []

        # EvidenceQuote: source_span_id must exist
        if obj["type"] == "EvidenceQuote":
            if obj["source_span_id"] not in all_objects:
                dangling_refs.append(("source_span_id", obj["source_span_id"]))

        # ResearchClaim: supported_by_quotes must exist
        if obj["type"] == "ResearchClaim":
            for qid in obj.get("supported_by_quotes", []):
                if qid not in all_objects:
                    dangling_refs.append(("supported_by_quotes", qid))

        # ResearchObject: supported_by_claims and supported_by_quotes
        if obj["type"] in ("RiskFactor", "GrowthDriver", "Headwind"):
            for cid in obj.get("supported_by_claims", []):
                if cid not in all_objects:
                    dangling_refs.append(("supported_by_claims", cid))
            for qid in obj.get("supported_by_quotes", []):
                if qid not in all_objects:
                    dangling_refs.append(("supported_by_quotes", qid))

        # AssumptionCandidate: supported_by_claims and supported_by_quotes
        if obj["type"] == "AssumptionCandidate":
            for cid in obj.get("supported_by_claims", []):
                if cid not in all_objects:
                    dangling_refs.append(("supported_by_claims", cid))
            for qid in obj.get("supported_by_quotes", []):
                if qid not in all_objects:
                    dangling_refs.append(("supported_by_quotes", qid))

        # LanguageSignal: source_quote_id must exist
        if obj["type"] == "LanguageSignal":
            if obj.get("source_quote_id") not in all_objects:
                dangling_refs.append(("source_quote_id", obj.get("source_quote_id")))

        # Edge: from_id and to_id must exist
        if obj["type"] == "Edge":
            if obj["from_id"] not in all_objects:
                dangling_refs.append(("from_id", obj["from_id"]))
            if obj["to_id"] not in all_objects:
                dangling_refs.append(("to_id", obj["to_id"]))

        if dangling_refs:
            rejected.append({
                **obj,
                "rejection_reason": f"Dangling references: {dangling_refs}",
                "rejection_stage": "reference_validation",
            })

    return rejected
```

`reference_validator` only checks ID-based references (`source_span_id`, quote IDs, claim IDs, `source_quote_id`, `from_id`, `to_id`). It does not validate metric names; metric dictionary checks belong to `metric_validator`.

### 8.4 support_validator

```python
def validate_has_support(obj: dict) -> bool:
    """Research interpretation objects must connect to at least one claim or quote."""
    if obj["type"] not in ("RiskFactor", "GrowthDriver", "Headwind", "AssumptionCandidate"):
        return True
    claims = obj.get("supported_by_claims") or []
    quotes = obj.get("supported_by_quotes") or []
    return bool(claims or quotes)
```

Objects failing this check are rejected and removed from accepted artifacts.

### 8.5 metric_validator

```python
def validate_metric_fields(obj: dict, metric_dictionary: dict) -> dict:
    """Separate confident canonical metrics from unmapped metric mentions."""
    canonical = set(metric_dictionary["canonical_metrics"])
    for field in ("related_metrics", "affects"):
        values = obj.get(field) or []
        unknown = [m for m in values if m not in canonical]
        if unknown:
            obj.setdefault("unmapped_metrics", []).extend(unknown)
            obj[field] = [m for m in values if m in canonical]
            obj["review_status"] = "needs_review"
    return obj
```

Unknown metrics in object fields do not create new metrics automatically. Unknown `Metric` IDs used as graph edge endpoints are rejected by `reference_validator` because edge targets must exist.

### 8.6 numeric_guard Validator

```python
def validate_numeric(obj: dict, all_quotes: dict, xbrl_facts: dict) -> bool:
    """Every numeric token in research text must be supported by quote text or XBRL."""
    import re

    NUMERIC_TEXT_FIELDS = {
        "ResearchClaim": ["claim_text"],
        "AssumptionCandidate": ["assumption_text", "value_hint"],
        "RiskFactor": ["description", "qualitative_impact"],
        "GrowthDriver": ["description", "qualitative_impact"],
        "Headwind": ["description", "qualitative_impact"],
    }

    def extract_numbers(text: str) -> set[str]:
        if not text:
            return set()
        raw_numbers = re.findall(r'[\$]?\d[\d,]*\.?\d*%?', text)
        return {normalize_numeric_token(n) for n in raw_numbers}

    numbers = set()
    for field in NUMERIC_TEXT_FIELDS.get(obj["type"], []):
        numbers |= extract_numbers(obj.get(field, ""))
    if not numbers:
        return True  # no numbers in claim, nothing to validate

    supported_numbers = set()
    for qid in obj.get("supported_by_quotes", []) or obj.get("supporting_quotes", []):
        quote = all_quotes.get(qid)
        if quote:
            supported_numbers |= extract_numbers(quote["quote_text"])

    for fact in xbrl_facts.values():
        supported_numbers.add(normalize_numeric_token(str(fact["value"])))

    return numbers.issubset(supported_numbers)
```

Accepted numeric claims must pass this check. If any number is unsupported, the claim is rejected or left out of accepted artifacts; `needs_review` is not a bypass for unsupported numeric claims.

`AssumptionCandidate.value_hint` defaults to `null` in v1. A non-null numeric `value_hint` is accepted only when every numeric token is directly supported by `quote_text` or XBRL; unsupported numeric hints are rejected, not downgraded to `needs_review`.

If XBRL extraction is `missing_or_failed`, `numeric_guard` still runs. Numeric claims are accepted only when every numeric token appears in supporting `quote_text`; claims that require XBRL support are rejected. `audit_report.md` records: "XBRL unavailable; numeric validation limited to quote text."

### 8.7 relation_validator

```python
def validate_edge(edge: dict, relations_whitelist: list[dict], all_objects: dict[str, dict]) -> bool:
    """Edge endpoints and relation fields must match a whitelist entry."""
    from_type = all_objects[edge["from_id"]]["type"]
    to_type = all_objects[edge["to_id"]]["type"]
    relation_name = edge["relation_name"]
    relation_id = edge["relation_id"]

    for rel in relations_whitelist:
        if (
            rel["id"] == relation_id
            and rel["name"] == relation_name
            and rel["from"] == from_type
            and rel["to"] == to_type
        ):
            return True
    return False
```

---

## 9. Checkpoint System

### 9.1 Checkpoint File

```json
{
  "pipeline_version": "0.1.0",
  "completed_stages": ["resolve_ticker", "discover_source_document"],
  "current_stage": "download_source_document",
  "stage_timestamps": {
    "resolve_ticker": "2025-11-02T10:30:05Z",
    "discover_source_document": "2025-11-02T10:30:12Z"
  },
  "stage_metadata": {
    "discover_source_document": {
      "accession_number": "0000320193-25-000001",
      "filing_date": "2025-11-01"
    }
  }
}
```

**Location:** `{output_dir}/companies/{ticker}/ontology/{doc_type_key}/{period}/.checkpoint.json`

### 9.2 Checkpoint Behavior

```python
class CheckpointManager:
    def __init__(self, checkpoint_path: Path):
        self.path = checkpoint_path
        self.state = self.load()

    def is_stage_complete(self, stage: str) -> bool:
        return stage in self.state["completed_stages"]

    def mark_complete(self, stage: str, metadata: dict = None):
        self.state["completed_stages"].append(stage)
        self.state["current_stage"] = get_next_stage(stage)
        self.state["stage_timestamps"][stage] = now_utc()
        if metadata:
            self.state["stage_metadata"][stage] = metadata
        self.save()

    def save(self):
        atomic_write_json(self.path, self.state)

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"pipeline_version": "0.1.0", "completed_stages": [], "current_stage": "resolve_ticker",
                "stage_timestamps": {}, "stage_metadata": {}}
```

### 9.3 Resume Logic

```python
def run_pipeline(ticker, document_type, ...):
    checkpoint = CheckpointManager(checkpoint_path)

    for stage in PIPELINE_STAGES:
        if checkpoint.is_stage_complete(stage):
            logger.info(f"Skipping completed stage: {stage}")
            continue

        logger.info(f"Running stage: {stage}")
        try:
            result = STAGE_HANDLERS[stage](..., checkpoint=checkpoint)
            checkpoint.mark_complete(stage, metadata=result.get("metadata"))
        except PipelineStageError as e:
            logger.error(f"Stage {stage} failed: {e}")
            raise  # Orchestrator handles retry

        checkpoint.save()  # After every successful stage
```

---

## 10. ExtractionWorker Protocol

### 10.1 Interface

```python
from claude_agent_sdk import ClaudeAgentOptions, query

class ExtractionWorker:
    def __init__(self, model: str, cwd: Path, max_retries: int = 3):
        self.model = model
        self.cwd = cwd
        self.max_retries = max_retries

    async def extract(
        self,
        prompt_template: str,
        input_data: dict,
        output_schema: dict,  # JSON schema for structured output
        stage_name: str,
    ) -> list[dict]:
        """Run extraction with retry and validation.

        Returns list of extracted objects.
        Raises ExtractionError after max retries exhausted.
        """
        ...

    async def _call_with_retry(self, messages: list, stage_name: str) -> str:
        """Agent SDK call with exponential backoff retry."""
        delay = 5  # seconds
        for attempt in range(self.max_retries):
            try:
                prompt = render_messages_as_prompt(messages)
                options = ClaudeAgentOptions(
                    model=self.model,
                    cwd=str(self.cwd),
                    system_prompt="Return only valid JSON matching the requested schema.",
                )
                chunks = []
                async for message in query(prompt=prompt, options=options):
                    text = extract_text_from_sdk_message(message)
                    if text:
                        chunks.append(text)
                return "\n".join(chunks)
            except Exception as e:
                delay *= 2  # exponential backoff
                logger.warning(f"{stage_name} attempt {attempt + 1} failed: {e}. Retrying in {delay}s")
                await asyncio.sleep(delay)
        raise ExtractionError(f"{stage_name} failed after {self.max_retries} attempts")
```

The v1 extraction layer uses `claude-agent-sdk`, not the raw `anthropic` Python API client. Use `query()` for independent batch calls. `ClaudeSDKClient` is deferred unless a future stage needs a long-lived conversation.

### 10.2 Structured Output Parsing

```python
def parse_structured_output(raw: str, output_schema: dict, stage_name: str) -> list[dict]:
    """Parse AI JSON output into validated objects.

    Handles:
    - JSON extraction from markdown code blocks
    - Schema validation against output_schema
    - Malformed output → raise ExtractionError with details
    """
    # Extract JSON from potential markdown code block wrapping
    json_str = raw
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0]

    data = json.loads(json_str)

    if isinstance(data, dict):
        data = [data]  # Normalize single object to list

    validated = []
    for item in data:
        validate_against_schema(item, output_schema)
        validated.append(item)

    return validated
```

---

## 11. artifact_index.json Schema

```json
{
  "ticker": "AAPL",
  "document_type": "10-K",
  "doc_type_key": "10K",
  "period": "FY2025",
  "generated_at": "2025-11-02T11:00:00Z",
  "schema_version": "0.1.0",
  "files": {
    "spans": "companies/AAPL/ontology/10K/FY2025/spans.jsonl",
    "evidence_quotes": "companies/AAPL/ontology/10K/FY2025/evidence_quotes.jsonl",
    "language_signals": "companies/AAPL/ontology/10K/FY2025/language_signals.jsonl",
    "claims": "companies/AAPL/ontology/10K/FY2025/claims.jsonl",
    "risks": "companies/AAPL/ontology/10K/FY2025/risks.jsonl",
    "growth_drivers": "companies/AAPL/ontology/10K/FY2025/growth_drivers.jsonl",
    "headwinds": "companies/AAPL/ontology/10K/FY2025/headwinds.jsonl",
    "assumption_candidates": "companies/AAPL/ontology/10K/FY2025/assumption_candidates.jsonl",
    "edges": "companies/AAPL/ontology/10K/FY2025/edges.jsonl",
    "xbrl_facts": "companies/AAPL/ontology/10K/FY2025/xbrl_facts.jsonl",
    "financial_metric_values": "companies/AAPL/ontology/10K/FY2025/financial_metric_values.jsonl",
    "derived_metric_values": "companies/AAPL/ontology/10K/FY2025/derived_metric_values.jsonl",
    "numeric_evidence": "companies/AAPL/ontology/10K/FY2025/numeric_evidence.jsonl",
    "calculated_numeric_support": "companies/AAPL/ontology/10K/FY2025/calculated_numeric_support.jsonl",
    "rejected_objects": "companies/AAPL/ontology/10K/FY2025/rejected_objects.jsonl",
    "batch_failures": "companies/AAPL/ontology/10K/FY2025/batch_failures.jsonl"
  },
  "counts": {
    "spans": 1247,
    "evidence_quotes": 342,
    "language_signals": 891,
    "claims": 156,
    "risks": 23,
    "growth_drivers": 12,
    "headwinds": 8,
    "assumption_candidates": 7,
    "edges": 489,
    "xbrl_facts": 450,
    "financial_metric_values": 35,
    "derived_metric_values": 8,
    "numeric_evidence": 900,
    "calculated_numeric_support": 0,
    "rejected_objects": 14,
    "batch_failures": 0
  },
  "sources": {
    "raw_html": "companies/AAPL/sources/10K/FY2025/raw.html",
    "clean_md": "companies/AAPL/sources/10K/FY2025/clean.md",
    "metadata": "companies/AAPL/sources/10K/FY2025/metadata.json"
  },
  "reports": {
    "graph_report": "companies/AAPL/ontology/10K/FY2025/graph_report.md",
    "audit_report": "companies/AAPL/ontology/10K/FY2025/audit_report.md",
    "pipeline_config": "companies/AAPL/ontology/10K/FY2025/pipeline_config.json"
  }
}
```

All paths in `artifact_index.json` are workspace-root relative paths. Do not mix artifact-directory-relative paths with workspace-root paths.

---

## 12. rejected_objects.jsonl Schema

Each line is a JSON object — the rejected object plus rejection metadata.

```json
{
  "original_id": "risk:AAPL:FY2025:10K:unsupported-reference",
  "original_type": "RiskFactor",
  "rejection_reason": "Dangling references: [('supported_by_quotes', 'quote:AAPL:FY2025:10K:item1a:9999:001')]",
  "rejection_stage": "reference_validation",
  "rejection_timestamp": "2025-11-02T11:00:00Z",
  "original_object": {
    "id": "risk:AAPL:FY2025:10K:unsupported-reference",
    "type": "RiskFactor",
    "...": "full original object preserved"
  }
}
```

## 12.1 batch_failures.jsonl Schema

Each line records an AI batch execution failure. Batch failures are execution failures, not object review states.

```json
{
  "id": "batch_failure:AAPL:FY2025:10K:extract_evidence_quotes:0007",
  "type": "BatchFailure",
  "ticker": "AAPL",
  "document_type": "10-K",
  "period": "FY2025",
  "source_document_id": "source:AAPL:FY2025:10K",
  "stage": "extract_evidence_quotes",
  "batch_index": 7,
  "input_span_ids": ["span:AAPL:FY2025:10K:item1a:0042"],
  "attempts": 3,
  "error_type": "SDKTimeout",
  "error_message": "SDK timeout after retry",
  "raw_output_path": "companies/AAPL/ontology/10K/FY2025/debug/extract_evidence_quotes_batch_0007_raw.txt",
  "created_at": "2025-11-02T11:00:00Z",
  "schema_version": "0.1.0"
}
```

Policy:
- AI batch failure → write `batch_failures.jsonl`; no accepted objects are produced for that batch.
- Uncertain judgment with valid evidence → `review_status = "needs_review"` and may remain in accepted artifacts.
- Invalid schema, failed exact match, unsupported numeric claim, or dangling reference → `rejected_objects.jsonl` and never accepted artifacts.

---

## 13. Error Handling Specification

### 13.1 Error Hierarchy

```python
class KrwOntologyError(Exception):
    """Base exception for the pipeline."""

class PipelineStageError(KrwOntologyError):
    """A pipeline stage failed after all retries."""

class ExtractionError(KrwOntologyError):
    """AI extraction failed after all retries."""

class ValidationError(KrwOntologyError):
    """Validation found issues with extracted objects."""

class ConfigurationError(KrwOntologyError):
    """Invalid configuration."""
```

### 13.2 AI Stage Error Handling

```python
# Per-batch error handling:
# 1. Retry up to max_retries (3) with exponential backoff
# 2. If still failing: log error, mark batch as failed
# 3. Continue to next batch (pipeline never blocks on a single failure)
# 4. Log failed batches in audit_report.md
# 5. At end of stage, if any batches failed: log summary count

# Per-stage error handling:
# 1. If >50% of batches failed: raise PipelineStageError
#    (something fundamentally wrong with the prompt or data)
# 2. If <50%: complete successfully, log failures in audit
```

### 13.3 API Rate Limit Handling

```python
# Rate limit backoff:
# Initial delay: 5 seconds
# Max delay: 120 seconds
# Strategy: exponential backoff with jitter
# On persistent rate limit: sleep 60s, then retry once more

# API timeout: 120 seconds per call
```

### 13.4 Malformed AI Output

```python
# If AI output is not valid JSON:
# 1. Try to extract JSON from markdown code blocks
# 2. If extraction fails: retry the batch
# 3. If retry fails: log raw output to {stage}_raw_output.txt for debugging
# 4. Mark batch as failed, continue

# If AI output is valid JSON but violates schema:
# 1. Try to fix common issues (missing required fields, wrong enum values)
# 2. If fixable: fix and continue
# 3. If not fixable: log and mark batch as failed
```

---

## 14. Logging Specification

```python
import logging
import json

class StructuredFormatter(logging.Formatter):
    """Format logs as structured JSON for pipeline analysis."""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "stage": getattr(record, "stage", "unknown"),
            "message": record.getMessage(),
        }
        if record.exc_info and not record.exc_text:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

# Per-stage logging:
# - stage: current pipeline stage name
# - Tokens: input/output token counts (for AI stages)
# - duration: wall clock time for the stage
# - cost_estimate_usd: estimated cost based on model pricing (for AI stages)
```

---

## 15. Span Building Rules

### 15.1 Window Parameters

```python
SPAN_TARGET_CHARS_MIN = 500
SPAN_TARGET_CHARS_MAX = 1200
SPAN_OVERLAP_CHARS = 150
SPAN_HARD_MAX_CHARS = 1500
```

### 15.2 Splitting Algorithm

```python
def build_spans(section_text: str, section_name: str, doc_type_key: str,
              ticker: str, period: str, source_document_id: str) -> list[dict]:
    """
    Split section text into overlapping character windows.

    Rules:
    1. Split on sentence boundaries when possible (prefer splitting at '. ')
    2. Each span overlaps with the previous by SPAN_OVERLAP_CHARS characters
    3. Spans smaller than SPAN_TARGET_CHARS_MIN are merged with adjacent spans
    4. Spans larger than SPAN_HARD_MAX_CHARS are force-split at a sentence boundary
    5. Each span gets a sequential index within its section
    6. start_char and end_char are absolute character positions within clean.md
    7. text_hash is SHA-256 over normalized span text
    """
```

---

## 16. Section Detection Patterns

### 16.1 10-K Section Regex

```python
SECTION_PATTERNS = {
    "cover": r"(?i)^part\s+I\s*$|^cover\s*$|^item\s+1\b(?!\.)",
    "item1": r"(?i)^item\s+1\.\s|^business\b",
    "item1a": r"(?i)^item\s+1A\.\s|^risk\s+factor",
    "item1b": r"(?i)^item\s+1B\.\s|^unresolved\s+staff",
    "item1c": r"(?i)^item\s+1C\.\s|^cybersecurity\b",
    "item2": r"(?i)^item\s+2\.\s|^properties",
    "item7": r"(?i)^item\s+7\.\s|^management's\s+discussion",
    "item7a": r"(?i)^item\s+7A\.\s|^quantitative\s+and\s+qualitative",
    "item8": r"(?i)^item\s+8\.\s|^financial\s+statements",
    "item9a": r"(?i)^item\s+9A\.\s|^controls\s+and\s+procedures",
    "item9b": r"(?i)^item\s+9B\.\s|^other\s+information",
    "item10": r"(?i)^item\s+10\.\s|^directors.*officers|executive\s+compensation",
    "item11": r"(?i)^item\s+11\.\s|^executive\s+compensation",
    "item12": r"(?i)^item\s+12\.\s|^security\s+ownership",
    "item13": r"(?i)^item\s+13\.\s|^certain\s+relationships",
    "item14": r"(?i)^item\s+14\.\s|^exhibits\s+and\s+financial",
    "item15": r"(?i)^item\s+15\.\s|^exhibits\s+and\s+financial\s+statement\s+schedules",
    "item16": r"(?i)^item\s+16\.\s|^exhibits",
}
```

---

## 17. Test Plan

### 17.1 Unit Tests

| Test | File | What it validates |
|------|------|-----------------|
| ID normalization | `test_id_utils.py` | `normalize_doc_type`, `generate_id` produce correct strings |
| Schema validation | `test_schema_validator.py` | Required fields, enum constraints, type discrimination |
| Exact match | `test_exact_match.py` | Substring check, whitespace normalization, rejection |
| Reference validation | `test_reference_validator.py` | Dangling IDs detected, rejected output generated |
| Support validation | `test_support_validator.py` | Research objects and assumptions require claim or quote support |
| Metric validation | `test_metric_validator.py` | Canonical metric mapping and unmapped metric handling |
| Numeric guard | `test_numeric_guard.py` | Numeric claims rejected unless every number has exact quote or XBRL support |
| Relation validation | `test_relation_validator.py` | Whitelist enforcement, type matching |
| Checkpoint | `test_checkpoint.py` | Save/load, stage completion detection, resume |
| IO utilities | `test_io.py` | JSONL read/write, atomic write |

### 17.2 Fixture Files

- `mini_10k.html`: ~2KB synthetic 10-K with basic Item 1, Item 1A, Item 7 sections
- `mini_10k_clean.md`: Expected markdown output from HTML cleaning
- `mini_10k_spans.jsonl`: Expected spans from section splitting

### 17.3 Integration Test

- `test_pipeline_mini_10k.py`: Full pipeline run on `mini_10k.html`, validates all output files exist and pass validation

### 17.4 Test Commands

```bash
# All unit tests (fast, no network):
pytest tests/unit/ -v

# All tests including integration (slow marker requires explicit opt-in):
pytest tests/ -v -m "not slow"

# Including slow tests:
pytest tests/ -v --run-slow

# Coverage report:
pytest tests/ --cov=src/krw_ontology --cov-report=term-missing
```

---

## 18. Prompt Template Structure

### 18.1 Quote Extraction Prompt

```python
QUOTE_EXTRACTION_SYSTEM = """You are a financial document analyst selecting evidence quote candidates from SEC 10-K filings.

You will receive pre-split quote candidates. Each candidate is already an exact excerpt from the source document.
Your job is NOT to write quote text. Your job is to choose the most significant candidate_ids and classify them.

Select candidates that:
1. Contain specific, factual information or management statements
2. Can stand alone outside the document context
3. Are useful evidence for company business model, risks, revenue, margin, growth, headwinds, regulation, supply chain, or modeling cues

For each selected candidate, classify:
- quote_type: one of {quote_types}
- language_signals: any language signals present (type, strength, direction, certainty, temporal_scope)

IMPORTANT RULES:
- Return candidate_id only; do not return quote_text
- Do not create new candidate IDs
- Do not paraphrase, summarize, or modify candidate text
- If no significant candidate exists, return an empty array

Output format: JSON array of quote selection objects with fields:
candidate_id, quote_type, section_name, confidence, language_signals.
"""
```

### 18.2 Output Schema for Quote Extraction

```json
{
  "type": "object",
  "properties": {
    "candidate_id": {"type": "string"},
    "quote_type": {"type": "string", "enum": ["risk_language", "business_description", ...]},
    "section_name": {"type": "string"},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "language_signals": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "signal_text": {"type": "string"},
          "signal_type": {"type": "string"},
          "strength": {"type": "string", "enum": ["strong", "medium", "weak"]},
          "direction": {"type": "string", "enum": ["positive", "negative", "neutral", "risk"]},
          "certainty": {"type": "string", "enum": ["observed", "conditional", "expected", "uncertain", "structural"]},
          "temporal_scope": {"type": "string", "enum": ["historical", "current", "future_or_potential", "ongoing"]},
          "exact_match_verified": {"type": "boolean"}
        },
        "required": ["signal_text", "signal_type", "strength", "direction", "certainty", "temporal_scope"]
      }
    }
  },
  "required": ["candidate_id", "quote_type", "section_name", "confidence"]
}
```

---

## 19. init-workspace Command

```python
@app.command()
def init_workspace():
    """Create ontology schema directory with starter YAML configs."""
    schema_dir = Path("ontology/schema")
    schema_dir.mkdir(parents=True, exist_ok=True)

    # Write starter YAML files from embedded templates
    (schema_dir / "objects.yaml").write_text(OBJECTS_YAML_TEMPLATE)
    (schema_dir / "relations.yaml").write_text(RELATIONS_YAML_TEMPLATE)
    (schema_dir / "quote_types.yaml").write_text(QUOTE_TYPES_YAML_TEMPLATE)
    (schema_dir / "language_signals.yaml").write_text(LANGUAGE_SIGNALS_YAML_TEMPLATE)
    (schema_dir / "claim_types.yaml").write_text(CLAIM_TYPES_YAML_TEMPLATE)
    (schema_dir / "risk_categories.yaml").write_text(RISK_CATEGORIES_YAML_TEMPLATE)
    (schema_dir / "metric_dictionary.yaml").write_text(METRIC_DICTIONARY_YAML_TEMPLATE)

    typer.echo("Workspace initialized. Schema configs written to ontology/schema/")
```

---

## 20. Phase 1 Implementation Checklist

When all items below are checked, Phase 1 is complete.

### Foundation (no AI)
- [ ] `pyproject.toml` with all dependencies
- [ ] CLI skeleton with `init-workspace`, `build-evidence-ontology`, `validate`, `build-report` commands
- [ ] `config/settings.py` — PipelineConfig, model config loader
- [ ] `config/constants.py` — DOCUMENT_TYPE mapping, batch sizes, paths
- [ ] `schema/id_utils.py` — normalize_doc_type, generate_id
- [ ] `schema/objects.yaml` — full object type definitions
- [ ] `schema/relations.yaml` — list format (C1 fix)
- [ ] `schema/quote_types.yaml`
- [ ] `schema/language_signals.yaml`
- [ ] `schema/claim_types.yaml`
- [ ] `schema/risk_categories.yaml`
- [ ] `schema/metric_dictionary.yaml` — 25 canonical metrics
- [ ] `utils/io.py` — JSONL read/write, atomic file write
- [ ] `utils/logging.py` — structured JSON logging
- [ ] `pipeline/checkpoint.py` — checkpoint save/load/resume
- [ ] `pipeline/stages/resolve_ticker.py` — SEC EDGAR CIK lookup
- [ ] `pipeline/stages/discover_source.py` — latest 10-K finder
- [ ] `pipeline/stages/download_source.py` — HTML download + SHA-256
- [ ] `pipeline/stages/clean_markdown.py` — HTML → MD + table conversion
- [ ] `pipeline/stages/extract_sections.py` — section detection + splitting
- [ ] `pipeline/stages/build_spans.py` — section → spans.jsonl
- [ ] `pipeline/stages/extract_xbrl.py` — XBRL/IXBRL parsing
- [ ] `pipeline/stages/build_indexes.py` — artifact_index + company_artifact_index skeleton; runs after `validate_ontology` in the pipeline
- [ ] `pipeline/orchestrator.py` — stage orchestration, retry, checkpoint

### Validation
- [ ] `validation/schema_validator.py` — Pydantic validation
- [ ] `validation/exact_match.py` — quote text exact match
- [ ] `validation/reference_validator.py` — ID referential integrity
- [ ] `validation/support_validator.py` — claim/quote support requirement
- [ ] `validation/metric_validator.py` — canonical metric mapping + unmapped metrics
- [ ] `validation/numeric_guard.py` — numeric claim XBRL cross-check
- [ ] `validation/relation_validator.py` — edge relation whitelist

### Tests
- [ ] Fixture files: `mini_10k.html`, `mini_10k_clean.md`, `mini_10k_spans.jsonl`
- [ ] Unit tests: id_utils, schema_validator, exact_match, reference_validator, support_validator, metric_validator, numeric_guard, relation_validator, checkpoint, io
- [ ] Integration test: `test_pipeline_mini_10k.py`
- [ ] Coverage report passes with >80% coverage on `src/krw_ontology`

### Documentation
- [ ] `CLAUDE.md` — query interface instructions
- [ ] `dev_spec_v1.md` — this file is complete and accurate
