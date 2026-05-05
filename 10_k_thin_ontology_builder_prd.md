# PRD: 10-K Evidence Ontology Workspace

> **Document status:** Product intent reference. For v1 implementation details, `dev_spec_v1.md` is the source of truth. If this PRD conflicts with `dev_spec_v1.md`, implement `dev_spec_v1.md`.

## 1. Product Summary

**Product Name:** 10-K Evidence Ontology Workspace  
**Internal Codename:** Evidence-Centered Financial Ontology  
**Product Type:** File-canonical equity research infrastructure  
**Primary Interface:** Claude Code / Codex / local CLI  
**Primary User:** 개인 또는 소규모 주식 리서치 에이전트 빌더, 투자 리서치 자동화 개발자, 애널리스트  

### One-Line Definition

10-K, 10-Q, earnings call 등 원문 문서를 티커·문서종류·기간 단위로 정리하고, 중요한 원문 문구를 `EvidenceQuote`로 보존한 뒤, 그 문구를 `ResearchClaim`, `RiskFactor`, `GrowthDriver`, `Headwind`, `AssumptionCandidate`, `Metric`과 연결해 Claude Code/Codex가 근거 기반으로 검색·질문·답변할 수 있게 하는 파일 기반 리서치 온톨로지 시스템.

---

## 2. Core Product Decision

### 2.1 Chosen Architecture

이 제품은 DB-first 또는 skill-first 구조가 아니다.

**v1의 핵심 선택은 다음과 같다.**

```text
File-canonical
Schema-governed
Quote-first
Validator-enforced
Agent-readable
DB-ready
```

### 2.2 What This Means

- 파일이 canonical source of truth다.
- DB는 v1에서 필수가 아니며, 나중에 검색/그래프 index로 붙인다.
- AI는 원문을 읽고 ontology artifact를 생성한다.
- Python validator가 AI 결과를 검증한다.
- Claude Code/Codex는 생성된 artifact와 index를 읽고 질문에 답한다.
- 스킬은 선택적 wrapper이며, 모든 기능을 스킬 안에 우겨넣지 않는다.

### 2.3 Non-Negotiable Rule

> 모든 중요한 리서치 해석은 원문 문구인 `EvidenceQuote`까지 추적 가능해야 한다.

즉:

```text
SourceDocument
  → SourceSpan
    → EvidenceQuote
      → ResearchClaim
        → RiskFactor / GrowthDriver / Headwind / AssumptionCandidate / Metric
```

---

## 3. Background

주식 리서치에서 숫자도 중요하지만, 10-K/10-Q/earnings call에 쓰인 **문구**는 더 중요할 때가 많다.

예시:

- “may adversely affect our business”
- “materially adversely affect”
- “depends on”
- “substantially all”
- “intense competition”
- “unfavorable impact”
- “is expected to continue”
- “driven primarily by”

이런 문구는 단순 수치보다 더 중요한 리서치 신호가 될 수 있다.

기존 RAG 또는 MD 검색만으로도 질문에 답할 수는 있지만 다음 한계가 있다.

- 원문 문구와 AI 해석이 섞인다.
- 어느 문구가 어떤 claim을 support하는지 추적하기 어렵다.
- 리스크가 어떤 metric이나 DCF assumption에 연결되는지 불안정하다.
- 여러 연도/분기/문서종류 비교가 어렵다.
- Claude Code/Codex가 매번 파일 전체를 뒤지면서 추정해야 한다.

이 제품은 원문 문구를 `EvidenceQuote`라는 1급 객체로 보존하고, 모든 해석 객체를 그 문구에 연결한다.

---

## 4. Problem Statement

사용자는 Claude Code/Codex를 통해 로컬 리서치 저장소에 다음과 같은 질문을 하고 싶다.

- “AAPL FY2025 10-K에서 gross margin에 영향을 줄 수 있는 문구 찾아줘.”
- “FY2025 10-K와 FY2024 10-K에서 supply chain risk 문구가 강해졌는지 비교해줘.”
- “10-Q에서 새로 등장한 headwind만 보여줘.”
- “DCF gross margin downside assumption을 뒷받침할 수 있는 10-K 원문 문구가 있어?”
- “이 리포트의 claim 중 source quote가 없는 것만 찾아줘.”
- “earnings call에서는 긍정적으로 말했는데 10-Q에서는 보수적으로 바뀐 부분 있어?”

단순 파일 검색만으로는 답변이 가능하더라도 신뢰도와 재현성이 낮다. 따라서 원문 문구, 해석 claim, 리서치 객체, 지표, 모델 가정을 구조적으로 연결해야 한다.

---

## 5. Goals

### 5.1 Product Goals

1. 티커별로 SEC filing과 기타 리서치 source를 정리한다.
2. 문서종류별로 10-K, 10-Q, earnings call, investor presentation, research report를 구분한다.
3. 연도/분기/기간 단위로 source document를 나눈다.
4. 원문 문단 또는 semantic chunk를 `SourceSpan`으로 저장한다.
5. 중요한 원문 문구를 `EvidenceQuote`로 exact match 보존한다.
6. AI가 `EvidenceQuote`를 기반으로 `ResearchClaim`을 생성한다.
7. AI가 claim 기반으로 risk, driver, headwind, assumption candidate를 생성한다.
8. 숫자는 XBRL 또는 원문에 명시된 경우에만 사용한다.
9. Python validator가 schema, exact quote, source span, relation, metric, numeric guard를 검증한다.
10. Claude Code/Codex가 artifact index와 graph report를 읽고 근거 기반 답변을 생성한다.
11. 향후 SQLite/Kuzu/Postgres/pgvector/Neo4j로 확장 가능하게 파일 구조와 ID를 설계한다.

### 5.2 User Goals

사용자는 다음을 원한다.

- 10-K/10-Q 원문을 매번 직접 읽지 않고도 중요한 문구를 찾고 싶다.
- AI가 만든 해석이 어느 원문 문구에서 나왔는지 보고 싶다.
- DCF/comps/리포트 가정이 원문 근거와 연결되길 원한다.
- 여러 연도/분기/문서종류 간 문구 변화를 비교하고 싶다.
- Git diff로 AI 산출물을 검토하고 싶다.
- Claude Code/Codex가 로컬 파일을 직접 읽고 답변하게 하고 싶다.

---

## 6. Non-Goals

v1에서 하지 않는 것:

- 10-K以外 문서종류 지원 (10-Q, earnings call 등은 v1.1+)
- 팔란티어 Foundry 수준의 운영 온톨로지 구축
- Neo4j/RDF/OWL/SPARQL 기반 ontology-first 시스템
- DB를 canonical source of truth로 사용
- 모든 기능을 Claude/OpenClaw skill 안에 구현
- 완전 자동 투자 추천
- 숫자 기반 valuation conclusion 자동 생성
- 멀티유저 권한 시스템
- 실시간 데이터 파이프라인
- 모든 SEC filing type 지원
- 모든 XBRL taxonomy 완전 정규화
- earnings call tone analysis 고도화
- graph DB 기반 웹앱 UI

---

## 7. Key Product Principles

### Principle 1: File-Canonical First

모든 핵심 산출물은 사람이 읽고 Git diff 가능한 파일로 저장한다.

```text
MD + JSONL + JSON + YAML
```

DB는 나중에 index/view로 붙인다.

---

### Principle 2: Quote-First Ontology

`EvidenceQuote`가 시스템의 핵심 객체다.

`ResearchClaim`, `RiskFactor`, `AssumptionCandidate`는 source of truth가 아니다. 이들은 원문 문구에 기반한 해석이다.

---

### Principle 3: AI Extracts, Code Validates

AI는 원문에서 quote, claim, risk, driver, assumption candidate를 추출한다.

하지만 검증은 deterministic Python code가 한다.

---

### Principle 4: Schema Governs AI

AI가 자유롭게 ontology를 만들지 않는다.

허용 객체, 허용 관계, quote type, claim type, risk category, metric dictionary는 YAML/schema로 고정한다.

---

### Principle 5: Partition by Ticker / Document Type / Period

모든 source document와 ontology artifact는 다음 기준으로 나눈다.

```text
ticker
source_type / document_type
fiscal_year
fiscal_period
period
filing_date / document_date
source_document_id
```

---

### Principle 6: DB-Ready but Not DB-First

v1 파일 구조는 나중에 SQLite/Kuzu/Postgres/Neo4j로 옮기기 쉽게 설계한다.

---

## 8. Target Users

### 8.1 Primary User

**주식 리서치 에이전트 빌더 / 개인 투자 리서치 자동화 개발자**

특징:

- Claude Code/Codex를 자주 사용한다.
- OpenBB, SEC, EDGAR, XBRL, financial modeling에 관심이 있다.
- 로컬 파일 기반 workflow를 선호한다.
- 빠른 MVP와 장기 확장성을 동시에 원한다.

### 8.2 Secondary User

**애널리스트 / 리서치 어시스턴트**

특징:

- SEC filing에서 중요한 문구를 빠르게 찾고 싶다.
- 리서치 claim의 원문 근거를 추적하고 싶다.
- 10-K/10-Q/earnings call 문구 변화를 비교하고 싶다.

---

## 9. Core Use Cases

### Use Case 1: 최신 10-K 수집 및 정리

```bash
krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest
```

시스템은 최신 10-K를 다운로드하고 다음 산출물을 만든다.

```text
companies/AAPL/sources/10K/FY2025/raw.html
companies/AAPL/sources/10K/FY2025/clean.md
companies/AAPL/sources/10K/FY2025/metadata.json
```

---

### Use Case 2: SourceSpan 생성

`clean.md`를 section과 paragraph/semantic chunk 단위로 나눈다.

```text
companies/AAPL/ontology/10K/FY2025/spans.jsonl
```

각 span은 section, path, offset, text hash를 가진다.

---

### Use Case 3: EvidenceQuote 추출

AI가 source span에서 중요한 원문 문구를 추출한다.

```text
companies/AAPL/ontology/10K/FY2025/evidence_quotes.jsonl
```

단, quote는 반드시 source span 안에 exact match되어야 한다.

---

### Use Case 4: ResearchClaim 생성

AI가 EvidenceQuote를 기반으로 해석 claim을 생성한다.

```text
companies/AAPL/ontology/10K/FY2025/claims.jsonl
```

모든 claim은 최소 1개 quote를 support로 가져야 한다.

---

### Use Case 5: Risk / Driver / Headwind / AssumptionCandidate 생성

AI가 claim을 기반으로 리서치 객체를 생성한다.

```text
companies/AAPL/ontology/10K/FY2025/risks.jsonl
companies/AAPL/ontology/10K/FY2025/growth_drivers.jsonl
companies/AAPL/ontology/10K/FY2025/headwinds.jsonl
companies/AAPL/ontology/10K/FY2025/assumption_candidates.jsonl
```

---

### Use Case 6: Claude Code/Codex 질의응답

사용자가 묻는다.

> AAPL FY2025 10-K에서 gross margin downside에 중요한 문구 찾아줘.

Claude Code/Codex는 다음 순서로 읽는다.

1. `company_artifact_index.json`
2. 해당 문서의 `artifact_index.json`
3. `graph_report.md`
4. `evidence_quotes.jsonl`
5. `claims.jsonl`
6. `risks/headwinds/assumption_candidates.jsonl`
7. 필요한 경우 `spans.jsonl`와 `clean.md`

답변에는 quote, source path, source span id, confidence, missing data가 포함된다.

---

## 10. Information Architecture

### 10.1 Top-Level Workspace

```text
research-workspace/
  AGENTS.md
  CLAUDE.md

  ontology/
    schema_version.yaml
    objects.yaml
    relations.yaml
    quote_types.yaml
    language_signals.yaml
    claim_types.yaml
    risk_categories.yaml
    metric_dictionary.yaml
    model_use_cases.yaml

  src/
    krw_ontology/
      cli/
      pipeline/
      extraction/
      validation/
      schema/
      utils/

  companies/
    {ticker}/
      company.yaml
      sources/
      ontology/
      reports/
      models/
      indexes/
```

---

### 10.2 Company-Level Structure

```text
companies/
  AAPL/
    company.yaml

    sources/
      10K/
        FY2025/
          raw.html
          clean.md
          metadata.json

      10Q/
        FY2025Q1/
          raw.html
          clean.md
          metadata.json

      earnings_call/
        FY2025Q1/
          clean.md
          metadata.json

    ontology/
      10K/
        FY2025/
          spans.jsonl
          evidence_quotes.jsonl
          language_signals.jsonl
          claims.jsonl
          risks.jsonl
          growth_drivers.jsonl
          headwinds.jsonl
          assumption_candidates.jsonl
          xbrl_facts.jsonl
          edges.jsonl
          rejected_objects.jsonl
          batch_failures.jsonl
          artifact_index.json
          graph_report.md
          audit_report.md
          pipeline_config.json

      10Q/
        FY2025Q1/
          spans.jsonl
          evidence_quotes.jsonl
          claims.jsonl
          risks.jsonl
          growth_drivers.jsonl
          headwinds.jsonl
          assumption_candidates.jsonl
          edges.jsonl
          artifact_index.json
          graph_report.md
          audit_report.md

    indexes/
      company_artifact_index.json
      company_graph_report.md
```

---

## 11. Required Partition Keys

모든 source document, quote, claim, risk, assumption candidate는 다음 메타데이터를 가져야 한다.

```json
{
  "ticker": "AAPL",
  "company_id": "company:AAPL",
  "document_type": "10-K",
  "source_document_id": "source:AAPL:FY2025:10K",
  "fiscal_year": 2025,
  "fiscal_period": "FY",
  "period": "FY2025",
  "filing_date": "2025-10-31",
  "document_date": "2025-09-27",
  "schema_version": "0.1.0",
  "pipeline_run_id": "run:AAPL:FY2025:10K:2026-05-02"
}
```

### 11.1 Period Naming

```text
10-K: FY2025
10-Q Q1: FY2025Q1
10-Q Q2: FY2025Q2
10-Q Q3: FY2025Q3
earnings call Q1: FY2025Q1
```

### 11.2 Document Types

v1 supported (10-K only):

```text
10-K
```

v1.1+ planned:

```text
10-Q              # v1.1
earnings_call     # v1.2
research_report   # v1.3
investor_presentation  # v1.3
```

Future supported:

```text
8-K
DEF14A
S-1
investor_presentation
conference_transcript
press_release
```

---

## 12. ID Conventions

### 12.1 SourceDocument

```text
source:{ticker}:{period}:{document_type}
```

Examples:

```text
source:AAPL:FY2025:10K
source:AAPL:FY2025Q1:10Q
source:AAPL:FY2025Q1:EARNINGS_CALL
```

### 12.2 SourceSpan

```text
span:{ticker}:{period}:{document_type}:{section}:{span_number}
```

Examples:

```text
span:AAPL:FY2025:10K:item1a:0021
span:AAPL:FY2025Q1:10Q:item2:0044
span:AAPL:FY2025Q1:EARNINGS_CALL:qa:0012
```

### 12.3 EvidenceQuote

```text
quote:{ticker}:{period}:{document_type}:{section}:{span_number}:{quote_number}
```

Examples:

```text
quote:AAPL:FY2025:10K:item1a:0021:001
quote:AAPL:FY2025Q1:10Q:item2:0044:001
```

### 12.4 ResearchClaim

```text
claim:{ticker}:{period}:{document_type}:{slug}
```

Example:

```text
claim:AAPL:FY2025:10K:supply-chain-dependency
```

### 12.5 Research Object

v1 uses document-specific objects.

```text
risk:AAPL:FY2025:10K:supply-chain-dependency
headwind:AAPL:FY2025Q1:10Q:fx-pressure
assumption:AAPL:FY2025:10K:gross-margin-downside
```

v2 may introduce canonical cross-period objects.

```text
canonical_risk:AAPL:supply-chain-dependency
```

### 12.6 ID Normalization Convention

`document_type` has two representations:

| Context | Value | Example |
|---------|-------|---------|
| Human-readable (metadata, CLI display) | Display value | `10-K` |
| Path-safe (IDs, directory names) | Key value | `10K` |

```python
DOCUMENT_TYPE_DISPLAY = "10-K"   # metadata, CLI output
DOCUMENT_TYPE_KEY = "10K"         # IDs, directory paths

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
- `document_type` field in JSONL objects stores display value
- Normalization function lives in shared utils, not duplicated per module

---

## 13. Data Model

### 13.1 SourceDocument

```json
{
  "id": "source:AAPL:FY2025:10K",
  "type": "SourceDocument",
  "ticker": "AAPL",
  "company_id": "company:AAPL",
  "document_type": "10-K",
  "source_type": "sec_filing",
  "fiscal_year": 2025,
  "fiscal_period": "FY",
  "period": "FY2025",
  "filing_date": "2025-10-31",
  "document_date": "2025-09-27",
  "accession_number": "0000320193-25-000079",
  "raw_path": "companies/AAPL/sources/10K/FY2025/raw.html",
  "clean_path": "companies/AAPL/sources/10K/FY2025/clean.md",
  "schema_version": "0.1.0"
}
```

---

### 13.2 SourceSpan

```json
{
  "id": "span:AAPL:FY2025:10K:item1a:0021",
  "type": "SourceSpan",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "section": "Item 1A",
  "section_title": "Risk Factors",
  "path": "companies/AAPL/sources/10K/FY2025/clean.md",
  "start_char": 183920,
  "end_char": 185210,
  "text_hash": "sha256:...",
  "text": "Full source span text here...",
  "section_detection_confidence": "high",
  "section_detection_method": "regex_with_toc_skip",
  "schema_version": "0.1.0",
  "pipeline_run_id": "run:AAPL:FY2025:10K:2026-05-02"
}
```

---

### 13.3 EvidenceQuote

```json
{
  "id": "quote:AAPL:FY2025:10K:item1a:0021:001",
  "type": "EvidenceQuote",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "source_span_id": "span:AAPL:FY2025:10K:item1a:0021",
  "document_type": "10-K",
  "period": "FY2025",
  "section": "Item 1A",
  "quote_text": "may adversely affect our business, results of operations, and financial condition",
  "quote_type": "risk_language",
  "importance": "high",
  "start_char": 42,
  "end_char": 117,
  "exact_match_verified": true,
  "extraction_method": "ai",
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0",
  "pipeline_run_id": "run:AAPL:FY2025:10K:2026-05-02"
}
```

---

### 13.4 LanguageSignal

```json
{
  "id": "signal:AAPL:FY2025:10K:item1a:0021:001",
  "type": "LanguageSignal",
  "ticker": "AAPL",
  "source_quote_id": "quote:AAPL:FY2025:10K:item1a:0021:001",
  "signal_text": "may adversely affect",
  "signal_type": "potential_negative",
  "direction": "negative",
  "strength": "medium",
  "certainty": "conditional",
  "temporal_scope": "future_or_potential",
  "exact_match_verified": true,
  "schema_version": "0.1.0"
}
```

---

### 13.5 ResearchClaim

```json
{
  "id": "claim:AAPL:FY2025:10K:supply-chain-dependency",
  "type": "ResearchClaim",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "claim_text": "Apple has supply chain dependency risk that could affect operations and financial results.",
  "claim_type": "risk_claim",
  "supported_by_quotes": [
    "quote:AAPL:FY2025:10K:item1a:0021:001"
  ],
  "supported_by_spans": [
    "span:AAPL:FY2025:10K:item1a:0021"
  ],
  "related_metrics": [
    "gross_margin",
    "revenue_growth"
  ],
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

---

### 13.6 RiskFactor

```json
{
  "id": "risk:AAPL:FY2025:10K:supply-chain-dependency",
  "type": "RiskFactor",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "name": "Supply chain dependency",
  "category": "supply_chain",
  "description": "The company depends on external manufacturing and logistics partners, creating operational dependency risk.",
  "supported_by_claims": [
    "claim:AAPL:FY2025:10K:supply-chain-dependency"
  ],
  "supported_by_quotes": [
    "quote:AAPL:FY2025:10K:item1a:0021:001"
  ],
  "affects": [
    "gross_margin",
    "revenue_growth",
    "inventory"
  ],
  "qualitative_impact": "Potential disruption could pressure product availability, cost structure, and financial results.",
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

---

### 13.7 AssumptionCandidate

```json
{
  "id": "assumption:AAPL:FY2025:10K:gross-margin-downside",
  "type": "AssumptionCandidate",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "name": "Supply chain margin downside risk",
  "assumption_text": "This evidence can support a qualitative DCF gross margin downside scenario.",
  "assumption_type": "margin",
  "value_hint": null,
  "supported_by_claims": [
    "claim:AAPL:FY2025:10K:supply-chain-dependency"
  ],
  "supported_by_quotes": [
    "quote:AAPL:FY2025:10K:item1a:0021:001"
  ],
  "confidence": "medium",
  "review_status": "needs_review",
  "schema_version": "0.1.0"
}
```

---

### 13.8 GrowthDriver

GrowthDriver shares the same schema structure as RiskFactor (and Headwind). The `type` field discriminates. This is intentional — reduces schema surface area and allows shared validation logic. A single `ResearchObject` schema with type discrimination is the implementation approach.

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
  "qualitative_impact": "Services growth could improve revenue mix and margin profile.",
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

---

### 13.9 Headwind

Headwind shares the same schema structure as RiskFactor and GrowthDriver.

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
  "qualitative_impact": "Foreign exchange pressure may reduce reported international revenue and margins.",
  "confidence": "medium",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

---

### 13.10 Metric (Canonical)

Metrics are canonical (not per-ticker, not per-period). They live at `ontology/schema/metric_dictionary.yaml` and are referenced by name from ResearchObject.affects and ResearchClaim.related_metrics.

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

---

### 13.11 XBRLFact

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

---

### 13.12 Edge

```json
{
  "id": "edge:AAPL:FY2025:10K:000001",
  "type": "Edge",
  "ticker": "AAPL",
  "source_document_id": "source:AAPL:FY2025:10K",
  "document_type": "10-K",
  "period": "FY2025",
  "from_id": "quote:AAPL:FY2025:10K:item1a:0021:001",
  "to_id": "claim:AAPL:FY2025:10K:supply-chain-dependency",
  "relation_name": "supports",
  "relation_id": "supports",
  "confidence": "high",
  "review_status": "accepted",
  "schema_version": "0.1.0"
}
```

---

## 14. Global Ontology Config

### 14.1 objects.yaml

```yaml
schema_version: "0.1.0"

objects:  # v1 — defined and implemented
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

future_objects:  # defined as placeholders, not implemented in v1
  - KeyPhrase          # v1.5 — key phrase extraction for search
  - ResearchReport     # v2 — research report as source
  - ValuationModel     # v4 — model integration
  - ModelAssumption    # v4 — model assumption tracking
  - Event              # v2 — corporate event extraction
```

---

### 14.2 relations.yaml

```yaml
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

**Note:** The `affects` relation appears 3 times (RiskFactor→Metric, GrowthDriver→Metric, Headwind→Metric). Standard YAML map keys would cause silent data loss with duplicates. Restructured to a list format with unique `id` fields (`affects_risk`, `affects_growth`, `affects_headwind`). Validation code matches on `from` type + `name` to resolve the correct relation entry.

---

### 14.3 quote_types.yaml

```yaml
quote_types:
  risk_language:
    description: "Language describing potential or actual downside risk."

  business_description:
    description: "Language describing how the company operates or makes money."

  revenue_driver:
    description: "Language explaining growth or decline in sales."

  margin_driver:
    description: "Language explaining margin, cost structure, or profitability."

  headwind:
    description: "Language describing pressure on demand, revenue, margin, or operations."

  growth_driver:
    description: "Language describing positive business momentum."

  competitive_pressure:
    description: "Language describing competition, substitution, pricing pressure, or market share risk."

  regulatory_exposure:
    description: "Language describing regulation, litigation, compliance, or legal exposure."

  supply_chain:
    description: "Language describing suppliers, manufacturing, logistics, inventory, or production risk."

  assumption_support:
    description: "Language that can support a valuation or forecast assumption."
```

---

### 14.4 language_signals.yaml

```yaml
language_signals:
  potential_negative:
    examples:
      - "may adversely affect"
      - "could adversely affect"
      - "may negatively impact"
    direction: negative
    strength: medium
    certainty: conditional

  material_negative:
    examples:
      - "materially adversely affect"
      - "significant adverse effect"
      - "substantial adverse impact"
    direction: negative
    strength: high
    certainty: conditional

  actual_negative:
    examples:
      - "has adversely affected"
      - "negatively impacted"
      - "declined due to"
    direction: negative
    strength: high
    certainty: observed

  continuing_pressure:
    examples:
      - "is expected to continue"
      - "may continue to"
      - "continues to impact"
    direction: negative
    strength: medium
    certainty: ongoing

  positive_growth:
    examples:
      - "increased due primarily to"
      - "driven by growth in"
      - "benefited from"
    direction: positive
    strength: medium
    certainty: observed

  dependency:
    examples:
      - "depends on"
      - "relies on"
      - "substantially all"
      - "limited number of"
    direction: risk
    strength: high
    certainty: structural
```

---

## 15. Functional Requirements

### FR1. Workspace Initialization

The system shall create the required workspace folder structure and global ontology YAML files.

Command:

```bash
krw-ontology init-workspace
```

Acceptance criteria:

- Creates `ontology/` config files.
- Creates `AGENTS.md` and `CLAUDE.md` templates.
- Creates empty `companies/` directory.

---

### FR2. Ticker / CIK Resolution

The system shall resolve a ticker to company metadata.

Acceptance criteria:

- Stores ticker, company name, CIK, exchange if available.
- Caches mapping in `company.yaml`.
- Fails gracefully for invalid ticker.

---

### FR3. Filing Discovery

The system shall find the target filing.

v1 support:

```text
latest 10-K
specific period 10-K
```

v1.1+ support:

```text
latest 10-Q
specific period 10-K/10-Q
```

Acceptance criteria:

- Stores accession number.
- Stores filing date and document date.
- Distinguishes 10-K from 10-K/A.
- v1 defaults to non-amended filing unless explicitly requested.

---

### FR4. Source Download and Cleaning

The system shall download raw filing and create clean markdown.

Outputs:

```text
companies/{ticker}/sources/{document_type}/{period}/raw.html
companies/{ticker}/sources/{document_type}/{period}/clean.md
companies/{ticker}/sources/{document_type}/{period}/metadata.json
```

Acceptance criteria:

- Raw file is preserved.
- Clean markdown is readable.
- Metadata includes source document id.

---

### FR5. SourceSpan Generation

The system shall split clean markdown into source spans.

Output:

```text
companies/{ticker}/ontology/{document_type}/{period}/spans.jsonl
```

Acceptance criteria:

- Each span has unique ID.
- Each span has section, path, text hash, and text.
- Span IDs include ticker, period, document type, section.

---

### FR6. EvidenceQuote Extraction

The system shall use AI to extract important exact quotes from source spans.

Output:

```text
evidence_quotes.jsonl
```

Acceptance criteria:

- Every quote has source_span_id.
- Every quote has quote_text.
- Every quote_text exact matches its source span text.
- Failed exact match becomes validation error.

---

### FR7. ResearchClaim Extraction

The system shall generate research claims from evidence quotes.

Output:

```text
claims.jsonl
```

Acceptance criteria:

- Every claim has supported_by_quotes.
- Claims are interpretations, not source text.
- Claims without quotes are invalid or `needs_review`.

---

### FR8. Research Object Extraction

The system shall generate risks, growth drivers, headwinds, and assumption candidates.

Outputs:

```text
risks.jsonl
growth_drivers.jsonl
headwinds.jsonl
assumption_candidates.jsonl
```

Acceptance criteria:

- Every object links to claim or quote.
- Every object has confidence and review_status.
- AssumptionCandidate must not invent numeric values.
- AssumptionCandidate defaults to `needs_review`.

---

### FR9. XBRL Fact Extraction

The system shall extract numeric financial facts from XBRL or structured data when available.

Output:

```text
xbrl_facts.jsonl
```

Acceptance criteria:

- LLM must not be the source of financial facts.
- Facts include taxonomy tag, value, unit, period.
- Canonical metrics are defined in `ontology/schema/metric_dictionary.yaml`; v1 does not emit a separate metrics artifact.
- Unmapped metric references are marked for review and are not auto-created.

---

### FR10. Edge Generation

The system shall create explicit graph edges.

Output:

```text
edges.jsonl
```

Acceptance criteria:

- Every relation uses an allowed relation from `relations.yaml`.
- Edge source is recorded.
- Edge confidence is recorded.

---

### FR11. Validation

The system shall validate all generated ontology artifacts.

Command:

```bash
krw-ontology validate AAPL --document-type 10-K --period FY2025
```

Acceptance criteria:

- Validates JSONL parseability.
- Validates required fields.
- Validates object type whitelist.
- Validates relation whitelist.
- Validates source span existence.
- Validates quote exact match.
- Validates claim supported_by_quotes.
- Validates metric dictionary mapping.
- Validates numeric guard.
- Produces audit report.

---

### FR12. Agent Index Generation

The system shall generate indexes and graph report for Claude Code/Codex.

Outputs:

```text
artifact_index.json
graph_report.md
company_artifact_index.json
```

Acceptance criteria:

- `artifact_index.json` lists all artifact paths for the document.
- `company_artifact_index.json` lists all documents for the ticker.
- `graph_report.md` summarizes main quotes, claims, risks, drivers, and assumptions.
- Claude Code/Codex can answer without scanning raw markdown first.

---

## 16. Validation Rules

### 16.1 Required Validator Checks

```text
1. JSONL parse validation
2. Schema required field validation
3. Object type whitelist validation
4. Relation whitelist validation
5. Source span existence validation
6. EvidenceQuote exact match validation
7. ResearchClaim supported_by_quotes validation
8. Metric dictionary mapping validation
9. Numeric value guard
10. Review status / confidence consistency validation
```

### 16.2 Exact Quote Rule

```python
def validate_exact_quote(quote, source_span):
    assert quote["quote_text"] in source_span["text"]
```

If this fails, the quote is invalid.

---

### 16.3 Claim Support Rule

Every `ResearchClaim` must have at least one valid `EvidenceQuote`.

Invalid:

```json
{
  "claim_text": "Apple has severe supply chain risk.",
  "supported_by_quotes": []
}
```

---

### 16.4 Numeric Guard

Numeric claims are only allowed if:

1. The value appears in the exact quote text, or
2. The value is backed by XBRL/structured data.

Otherwise the claim is invalid or `needs_review`.

---

### 16.5 Assumption Candidate Rule

`AssumptionCandidate` must not produce a final numeric assumption.

Allowed:

```text
This quote can support a gross margin downside scenario.
```

Not allowed:

```text
Therefore gross margin should be reduced by 120 bps.
```

Unless supported by model/human-reviewed assumption.

---

## 17. Claude Code / Codex Rules

### 17.1 AGENTS.md / CLAUDE.md Search Order

```text
1. companies/{ticker}/indexes/company_artifact_index.json
2. companies/{ticker}/ontology/{doc_type_key}/{period}/artifact_index.json
3. companies/{ticker}/ontology/{doc_type_key}/{period}/graph_report.md
4. evidence_quotes.jsonl
5. claims.jsonl
6. risks/headwinds/growth_drivers/assumption_candidates.jsonl
7. spans.jsonl
8. clean.md only when exact source context is required
```

### 17.2 Required Answer Format

For research answers, Claude Code/Codex should include:

```text
1. Direct answer
2. Important quote_text
3. Interpretation
4. Related claim/risk/driver/assumption
5. Source path
6. Source span ID
7. Confidence
8. Missing data / caveats
```

### 17.3 Forbidden Agent Behavior

```text
- Do not invent quotes.
- Do not paraphrase a quote as if it were exact text.
- Do not answer from memory when ontology artifacts exist.
- Do not treat ResearchClaim as source truth.
- Do not treat AssumptionCandidate as final model input.
- Do not create numeric claims without XBRL or exact source support.
- Do not modify raw source files unless explicitly asked.
```

---

## 18. CLI Requirements

`dev_spec_v1.md` defines the public v1 CLI. This PRD keeps only product intent and does not define additional public commands.

### 18.1 Public v1 Commands

```bash
krw-ontology init-workspace
```

```bash
krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest
```

```bash
krw-ontology validate AAPL --document-type 10-K --period FY2025
```

```bash
krw-ontology build-report AAPL --document-type 10-K --period FY2025
```

Stage-level commands for ingest, span building, extraction, and index building are deferred/internal and are not public v1 CLI commands.

### 18.2 Integrated Command

```bash
krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest
```

Expected output:

```text
✅ Filing downloaded
✅ Clean markdown generated
✅ Source spans generated: 1,284
✅ XBRL facts extracted: 87
✅ Evidence quotes extracted: 173
✅ Language signals extracted: 91
✅ Research claims extracted: 64
✅ Risks extracted: 38
✅ Growth drivers extracted: 18
✅ Headwinds extracted: 8
✅ Assumption candidates extracted: 14
✅ Edges generated: 312
✅ Exact quote validation passed: 173/173
⚠️ Needs review: 9 objects
✅ Graph report generated
✅ Audit report generated
```

---

## 19. Pipeline Architecture

### 19.1 Pipeline Stages

```text
1. resolve_ticker
2. discover_source_document
3. download_source_document
4. clean_to_markdown
5. extract_sections
6. build_source_spans
7. extract_xbrl_facts
8. extract_evidence_quotes
9. extract_research_claims
10. extract_risks_drivers_headwinds
11. extract_assumption_candidates
12. generate_edges
13. validate_ontology
14. build_indexes
15. build_graph_report
```

### 19.2 Checkpoint and Retry

Checkpoint after every stage. If stage N fails, resume from stage N. AI stages produce intermediate files that survive pipeline restart. Code stages are idempotent.

```python
PIPELINE_STAGES = [
    "resolve_ticker",
    "discover_source_document",
    "download_source_document",
    "clean_to_markdown",
    "extract_sections",
    "build_source_spans",
    "extract_xbrl_facts",          # CODE
    "extract_evidence_quotes",    # AI, embeds language signal candidates
    "extract_research_claims",     # AI
    "extract_risks_drivers_headwinds",  # AI
    "extract_assumption_candidates",      # AI
    "generate_edges",              # AI
    "validate_ontology",           # CODE
    "build_indexes",               # CODE
    "build_graph_report",          # CODE
]
```

Retry logic for AI stages:
- Retry up to 3 times with exponential backoff.
- On persistent batch failure: write `batch_failures.jsonl`, produce no accepted objects for that batch, continue to next batch.
- Pipeline never blocks on a single extraction failure.

---

### 19.3 Span-Batch Extraction Strategy

10-K is 80K–150K words. Cannot fit entire document into a single AI call.

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

Batch sizing:

```python
QUOTE_EXTRACTION_BATCH = 20  # spans per AI call for quote extraction
CLAIM_EXTRACTION_BATCH = 15   # spans (with quotes) per AI call for claim extraction
OBJECT_EXTRACTION_BATCH = "full_document"  # claims are smaller, fit more
MAX_CONTEXT_TOKENS = 180000  # Claude context window safety margin
```

---

### 19.4 AI vs Code Responsibility

AI:

```text
- EvidenceQuote extraction
- LanguageSignal classification
- ResearchClaim generation
- Risk/driver/headwind classification
- AssumptionCandidate generation
```

Code:

```text
- Downloading
- Markdown cleaning
- Section splitting
- Source span creation
- XBRL fact extraction
- Schema validation
- Exact quote validation
- Source span validation
- Metric mapping validation
- Numeric guard
- Index generation
```

---

## 20. Indexes and Reports

### 20.1 artifact_index.json

```json
{
  "ticker": "AAPL",
  "document_type": "10-K",
  "period": "FY2025",
  "source_document_id": "source:AAPL:FY2025:10K",
  "artifacts": {
    "clean_markdown": "companies/AAPL/sources/10K/FY2025/clean.md",
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
    "rejected_objects": "companies/AAPL/ontology/10K/FY2025/rejected_objects.jsonl",
    "batch_failures": "companies/AAPL/ontology/10K/FY2025/batch_failures.jsonl",
    "audit_report": "companies/AAPL/ontology/10K/FY2025/audit_report.md",
    "pipeline_config": "companies/AAPL/ontology/10K/FY2025/pipeline_config.json"
  }
}
```

---

### 20.2 company_artifact_index.json

```json
{
  "ticker": "AAPL",
  "documents": [
    {
      "source_document_id": "source:AAPL:FY2025:10K",
      "document_type": "10-K",
      "period": "FY2025",
      "artifact_index": "companies/AAPL/ontology/10K/FY2025/artifact_index.json"
    },
    {
      "source_document_id": "source:AAPL:FY2025Q1:10Q",
      "document_type": "10-Q",
      "period": "FY2025Q1",
      "artifact_index": "companies/AAPL/ontology/10Q/FY2025Q1/artifact_index.json"
    }
  ]
}
```

---

### 20.3 graph_report.md

The graph report is a human/agent-readable summary of a document’s evidence graph.

Required sections:

```text
# Graph Report: AAPL FY2025 10-K

## Source Document
## Top Evidence Quotes
## Major Research Claims
## Risk Factors
## Growth Drivers
## Headwinds
## Assumption Candidates
## Metric Links
## Low Confidence Items
## Unsupported / Needs Review Items
## Suggested Follow-up Questions
```

---

## 21. Quality Metrics

### 21.1 Extraction Quality

- EvidenceQuote exact match rate
- ResearchClaim quote coverage rate
- Risk/driver/headwind supported-by-quote rate
- AssumptionCandidate review rate
- Unmapped metric count
- Numeric guard violation count

### 21.2 Agent Utility

- % of answers including quote_text
- % of answers including source_span_id
- % of answers avoiding raw markdown scan
- Time to answer common research query
- Cross-document comparison success rate

### 21.3 v1 Minimum Quality Bar

```text
- 100% EvidenceQuote exact match validation for accepted quotes
- 95%+ valid JSONL parse rate
- 90%+ ResearchClaims with valid quotes
- 0 accepted numeric claims without XBRL or exact quote support
- All AssumptionCandidates default to needs_review unless human-approved
```

---

## 22. Expansion Plan

### v1: File-Canonical MVP (10-K only)

```text
MD + JSONL + YAML
AI extraction (Claude Agent SDK)
Python validator
Claude Code/Codex rules
Git diff review
Checkpoint/resume pipeline
```

### v1.1: 10-Q Support

```text
+ 10-Q document type
+ Quarterly period handling
```

### v1.2: Earnings Call Support

```text
+ earnings_call document type
+ Transcript parsing
```

### v1.3: Additional Source Types

```text
+ research_report document type
+ investor_presentation document type
```

### v1.5: Search Index

```text
SQLite FTS for quote_text and claim_text
quote index
claim index
company-level artifact index
```

### v2: Graph Query Layer

```text
Kuzu as derived graph index
EvidenceQuote → Claim → Risk → Metric traversal
cross-year and cross-document query
```

### v3: Product Backend

```text
Postgres
pgvector
API server
web UI
multi-user support
research report integration
```

### v4: Research Operating Layer

```text
model assumptions
report workflow
audit trail
human approval
valuation model linkage
team collaboration
```

---

## 23. DB Strategy

### 23.1 v1 Decision

No DB is required for v1.

Canonical source:

```text
clean.md
spans.jsonl
evidence_quotes.jsonl
claims.jsonl
risks.jsonl
edges.jsonl
YAML schemas
```

### 23.2 Future DBs

Recommended order:

```text
1. SQLite FTS
   - exact/keyword quote search
   - claim search

2. Kuzu
   - property graph traversal
   - local embedded graph index

3. Postgres + pgvector
   - product backend
   - semantic search
   - multi-user support

4. Neo4j
   - optional enterprise graph UI / complex traversal
```

DBs are derived views. Files remain source of truth unless a future migration explicitly changes that.

---

## 24. Risks and Mitigations

### Risk 1: AI Invents Quotes

Mitigation:

- Exact match validation.
- Invalid quotes rejected.

### Risk 2: AI Over-Interprets Claims

Mitigation:

- Every claim must link to quote.
- Confidence and review_status required.
- Unsupported claims flagged.

### Risk 3: Schema Changes Frequently

Mitigation:

- File-canonical JSONL.
- schema_version on every object.
- Migration scripts later.

### Risk 4: Folder Structure Becomes Too Complex

Mitigation:

- Standard partition: `{ticker}/{document_type}/{period}`.
- Company-level artifact index.
- Document-level artifact index.

### Risk 5: Numeric Hallucination

Mitigation:

- XBRL/structured source required.
- Quote text numeric exact match allowed only if present.
- Numeric guard validator.

### Risk 6: Claude/Codex Reads Raw Files Inefficiently

Mitigation:

- artifact_index and graph_report first.
- AGENTS.md / CLAUDE.md search order.
- quote/claim indexes.

---

## 25. MVP Acceptance Criteria

The MVP is complete when:

1. User can run:

```bash
krw-ontology build-evidence-ontology AAPL --document-type 10-K --latest
```

2. The system creates:

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

3. Validator confirms:

```text
- all accepted quotes exact match source spans
- all accepted claims have quote support
- all accepted relations are allowed
- no accepted numeric claims without source support
```

4. User can ask Claude Code/Codex:

> AAPL FY2025 10-K에서 gross margin downside에 중요한 문구 찾아줘.

5. Claude Code/Codex responds with:

```text
- direct answer
- exact quote_text
- interpretation
- source path
- source span ID
- confidence
- missing data
```

---

## 26. Final Product Definition

**10-K Evidence Ontology Workspace is a file-canonical research system that lets AI extract important source language from filings and transcripts, validates every quote and claim against the original text, and gives Claude Code/Codex a structured evidence graph for equity research questions.**
