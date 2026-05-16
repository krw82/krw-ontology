# KRW Ontology Research Synthesis Contract

This contract defines pass 1 of the product workflow.

```text
MCP tools -> ResearchSynthesis.canonical_answer -> display planner -> frontend renderer
```

The research skill owns evidence retrieval, validation, chain inspection, analytical judgment, and the final canonical answer content. The display planner owns only grouping and rendering decisions.

## Core Rule

`canonical_answer.units` is the source of truth for what can be shown to the user. If a sentence, number, caveat, or conclusion should appear in the UI, it must exist as a canonical unit first.

The answer-composer/display-planner is not allowed to invent content. It can only reference `canonical_answer.units` by `source_ids`.

## Required Shape

Use this shape when the runtime asks for structured research handoff:

```json
{
  "synthesis_version": "krw-research-synthesis/v1",
  "question": "Original user question",
  "scope": {
    "tickers": ["VG"],
    "document_types": ["10-K", "10-Q"],
    "periods": ["FY2025", "FY2025Q3"],
    "period_policy": "latest relevant filings",
    "external_premises": []
  },
  "thesis": "One or two sentence analytical conclusion.",
  "canonical_answer": {
    "language": "ko",
    "default_order": ["p1", "m1", "c1"],
    "units": [
      {
        "id": "p1",
        "type": "paragraph",
        "text": "VG는 LNG 가격 상승의 수혜 가능성이 있지만 feed gas cost와 프로젝트 일정 리스크가 같이 존재합니다.",
        "importance": "required",
        "confidence": "indirect",
        "display_policy": "show",
        "evidence_refs": ["ref_1"]
      }
    ]
  },
  "research_facts": {
    "key_metrics": [],
    "business_segments": [],
    "impact_channels": [],
    "risks": [],
    "drivers": [],
    "watch_items": [],
    "timeline_events": [],
    "caveats": []
  },
  "display_guidance": {
    "recommended_blocks": [],
    "do_not_claim": [],
    "required_source_ids": ["p1"]
  },
  "evidence_refs": [],
  "quality": {
    "overall": "medium",
    "warnings": []
  }
}
```

Fields may be empty when not relevant, but `canonical_answer.units` should not be thin. A company overview usually needs business model, economics, drivers, risks, watch items, and caveats in canonical units before display planning.

## Canonical Answer Units

Every unit needs:

- `id`: stable within the synthesis, such as `p1`, `m1`, `r1`
- `type`: one of the allowed canonical unit types
- `importance`: `required`, `supporting`, or `optional`
- `confidence`: `direct`, `indirect`, `inferred`, or `unsupported`
- `display_policy`: `show` or `hidden_by_default`
- `evidence_refs`: internal refs backing the unit

Allowed unit types:

| Unit type | Purpose |
| --- | --- |
| `heading` | Section label that carries no new factual claim |
| `paragraph` | Final prose, interpretation, conclusion, nuance |
| `metric` | Verified reported/derived number or exact date/value |
| `period_delta` | Verified change between periods |
| `business_segment` | Company activity, segment, or business line |
| `premise` | User-provided or external market premise |
| `impact_channel` | Factor -> financial channel -> mechanism |
| `risk_item` | Risk factor or negative exposure |
| `driver_item` | Growth driver or positive exposure |
| `headwind_item` | Headwind or pressure point |
| `watch_item` | Item to monitor |
| `timeline_event` | Project/date/event/maturity milestone |
| `comparison_row` | One row in a ticker/period/segment comparison |
| `caveat` | Limitation or warning that changes interpretation |
| `glossary_term` | Term definition |
| `evidence_note` | Support path for audit/debug or user-requested evidence |

## Unit Examples

### Paragraph

```json
{
  "id": "p1",
  "type": "paragraph",
  "text": "AMZN은 AWS 수익성, 북미 리테일 규모, AI 투자 부담을 함께 봐야 합니다.",
  "importance": "required",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_aws_segment"]
}
```

### Metric

Use only for verified reported or derived values.

```json
{
  "id": "m1",
  "type": "metric",
  "label": "AWS operating income",
  "value": "$45.6B",
  "period": "FY2025",
  "unit": "USD",
  "status": "reported",
  "source_label": "AMZN FY2025 10-K",
  "importance": "supporting",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_metric_aws_op_income"]
}
```

Rules:

- Prefer `FinancialMetricValue`, `DerivedMetricValue`, `NumericEvidence`, or `XBRLFact`.
- Use traced `ResearchClaim` or `EvidenceQuote` only if the value is explicit.
- Do not compute a delta unless both endpoints, units, periods, and sign are clear.
- Omit suspicious values instead of passing them to canonical content.

### Business Segment

```json
{
  "id": "s1",
  "type": "business_segment",
  "name": "AWS",
  "role": "Cloud infrastructure and AI/ML services",
  "economics": "Disproportionate operating income contributor",
  "why_it_matters": "AWS profitability can offset retail and AI infrastructure investment pressure.",
  "importance": "required",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_aws_segment"]
}
```

### Impact Channel

```json
{
  "id": "ic1",
  "type": "impact_channel",
  "factor": "global LNG price",
  "channel": "revenue",
  "direction": "positive",
  "mechanism": "Higher international LNG prices can improve realized sales economics on exposed volumes.",
  "offsets": ["feed gas cost", "project timing", "contract mix"],
  "importance": "required",
  "confidence": "indirect",
  "display_policy": "show",
  "evidence_refs": ["ref_lng_price_exposure"]
}
```

### Risk, Driver, Headwind, Watch Item

```json
{
  "id": "r1",
  "type": "risk_item",
  "label": "AI infrastructure investment burden",
  "analysis": "Demand growth is positive, but capex and depreciation can pressure margins if monetization lags.",
  "severity": "medium",
  "importance": "required",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_ai_capex"]
}
```

### Timeline Event

```json
{
  "id": "t1",
  "type": "timeline_event",
  "label": "CP2 Phase 1 targeted COD",
  "date_or_period": "late 2029",
  "status": "targeted",
  "analysis": "This is a target, not a completed milestone.",
  "source_label": "VG FY2025 10-K",
  "importance": "required",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_cp2_phase1_cod"]
}
```

### Caveat

```json
{
  "id": "c1",
  "type": "caveat",
  "text": "The filing support is direct for the project target date, but the date is forward-looking.",
  "severity": "warning",
  "importance": "required",
  "confidence": "direct",
  "display_policy": "show",
  "evidence_refs": ["ref_cp2_phase1_cod"]
}
```

## Research Facts

`research_facts` may duplicate or normalize facts used by canonical units. It is useful for audit, debugging, and later analytics. It is not the frontend source of truth for display.

If a fact should be visible, put it into `canonical_answer.units`. Do not assume the display planner will inspect `research_facts`.

## Display Guidance

`display_guidance` can guide the display planner without giving it authority to rewrite content.

```json
{
  "recommended_blocks": [
    {
      "block_type": "metric_grid",
      "source_ids": ["m1", "m2"],
      "reason": "Several verified metrics are easier to scan together."
    }
  ],
  "do_not_claim": [
    "Do not call a targeted COD an achieved COD."
  ],
  "required_source_ids": ["p1", "c1"]
}
```

The display planner may ignore `recommended_blocks` when a simpler markdown layout is clearer, but it must include all required visible units unless the user explicitly requested a narrower answer.

## Evidence Refs

Evidence refs connect canonical units to internal evidence without making raw ontology IDs visible by default.

```json
{
  "id": "ref_cp2_phase1_cod",
  "source_label": "VG FY2025 10-K",
  "object_type": "ResearchClaim",
  "evidence_grade": "direct",
  "trace_id": "internal-only",
  "chain_summary": {
    "has_quote": true,
    "has_claim": true,
    "has_semantic_object": true,
    "has_temporal_context": false
  },
  "quote_text": null
}
```

Rules:

- `id` should be stable within the synthesis payload.
- `trace_id` or raw object IDs are internal-only.
- `quote_text` should be `null` unless the user explicitly asked for raw evidence, audit output, or export citations.
- Use `evidence_grade`: `direct`, `indirect`, `derived`, or `unsupported`.

## Minimum Coverage By Question Type

### Company overview

Canonical answer should cover:

- what the company does
- business segments or activities
- economics and key metrics when verified
- growth drivers
- risks/headwinds
- watch items
- material caveats

Do not reduce a company overview to a few cards or a metric table. The prose must exist as canonical units.

### Exact factual lookup

Canonical answer should cover:

- direct answer first
- source label
- whether it is reported, targeted, expected, estimated, guided, or inferred
- caveat if forward-looking or unsupported
- contradiction check result when relevant

### Scenario or sensitivity

Canonical answer should cover:

- external factor
- company exposure
- financial channels
- offsets
- confidence level
- what evidence is direct versus inferred

### External market report impact

Canonical answer should cover:

- market premise
- company-specific ontology exposure
- explicit analyst bridge
- benefit channels
- risk or offset channels
- caveats about market data not being native ontology evidence

### Comparison

Canonical answer should cover:

- common comparison axis
- each ticker's differentiated exposure
- confidence and evidence depth
- caveats for missing coverage or non-comparable periods

## Quality Rules

- Use `krw_ontology_trace` for exact values, dates, project milestones, contract terms, and guidance.
- Use `krw_ontology_chain` for conclusions that depend on how evidence, claims, semantic objects, and temporal context connect.
- Use `krw_ontology_quality` when completeness or reliability matters.
- Direct filing evidence outranks inferred bridges.
- Do not promote rejected or unsupported objects into conclusions.
- If a topic search fails, record the gap and the alternative search path only in internal notes or warnings.
- Do not put internal quality, coverage, index, rejected-object, or pipeline diagnostics into `canonical_answer.units` with `display_policy="show"`.
- If a quality issue materially weakens a candidate point, omit that point, lower confidence privately, or keep the uncertainty inside hidden/internal fields. Do not add visible generic caveats about evidence availability, missing quantification, extraction status, filing coverage, or quality unless the user explicitly asks for audit/debug/quality details.
- Debuggable details such as object counts, section quality, batch failures, rejected objects, trace IDs, catalog output, and index inventory belong only in `evidence_refs`, `quality`, `internal_notes`, or hidden-by-default units unless the user explicitly requests audit/debug output.

## What Not To Do

- Do not emit `answer_blocks` from the research skill.
- Do not emit `display_plan` from the research skill.
- Do not decide frontend layout, HTML, React, CSS, or mobile rendering.
- Do not paste raw ontology object text into canonical units.
- Do not expose ontology object type names such as `ResearchClaim`, `EvidenceQuote`, `RiskFactor`, `GrowthDriver`, `Headwind`, `BusinessActivity`, `ExternalFactorExposure`, `NumericEvidence`, `XBRLFact`, `CompanyBusinessProfile`, `객체`, or `온톨로지 객체` in visible canonical units unless the user explicitly asks for audit/debug output.
- Do not paste original filing quote text into visible canonical units. Use reader-facing source labels only, and keep quote text inside evidence refs or hidden/internal units by default.
- Do not add customer-facing sections named "quality note", "품질 노트", "coverage note", "debug note", "데이터 커버리지", or similar operational footers unless the user explicitly asks for them.
- Do not let numeric values pass through unless they are validated.
- Do not leave the display planner to infer missing content.
