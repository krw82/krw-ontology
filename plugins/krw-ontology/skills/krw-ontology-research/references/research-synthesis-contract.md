# KRW Ontology Research Synthesis Contract

This contract defines pass 1 of the product workflow.

```text
MCP tools -> ResearchSynthesis.canonical_answer -> display planner -> frontend renderer
```

The research skill owns evidence retrieval, validation, trace/chain inspection, analytical judgment, answerability classification, and the final canonical answer content. The display planner owns only grouping and rendering decisions.

## Core Rule

`canonical_answer.units` is the source of truth for what can be shown to the user. If a sentence, number, caveat, or conclusion should appear in the UI, it must exist as a canonical unit first.

The answer-composer/display-planner is not allowed to invent content. It can only reference `canonical_answer.units` by `source_ids`.

## Evidence And Answerability Rule

Retrieval and answerability are different.

```text
Candidate retrieval may be broad.
Direct answerability must be strict.
```

Do not use a returned object as a strong answer merely because it was retrieved. A conclusion is strong only when the result tier and evidence refs support it.

When using `krw_ontology_retrieve`, map retrieval buckets directly into synthesis policy:

```text
direct_evidence -> candidate evidence refs for strong direct units, subject to trace/tier checks
related_context -> contextual or bridge evidence only
rejected_context -> internal diagnostics only; do not use for visible claims
```

Allowed strength levels:

```text
traceable_direct
traceable_metric_lineage
traceable_related
untraced_direct_candidate
broad_related_candidate
no_direct_evidence
not_answerable
```

Visible strong conclusions should use `traceable_direct` or `traceable_metric_lineage`. `traceable_related` can support context. `untraced_direct_candidate` and `broad_related_candidate` must not be phrased as direct evidence.

## Canonical Object Boundary

The research synthesis may use reader-friendly unit types such as risk, driver, headwind, timeline event, or metric. These are presentation-level categories, not necessarily canonical ontology object names.

Internal canonical mapping:

```text
risk_item / driver_item / headwind_item -> BusinessFactor role views
metric -> MetricObservation or Calculation-backed value
impact_channel -> ExternalFactorExposure scenario_effects or traced analyst bridge
timeline_event -> BusinessEvent or ChangeEvent depending on whether it is a business event or disclosure change
contract / obligation / maturity / covenant content -> AgreementTerm
period_delta -> TrendObservation, ChangeEvent, TemporalLink, MetricObservation, or Calculation support
company_discovery -> CompanyTopicProfile / company_topic_index / ticker_summary, then traced source objects
```

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
  "answerability": {
    "direct_answerable": true,
    "related_context_available": true,
    "negative_answer_supported": false,
    "needs_user_clarification": false,
    "recommended_answer_mode": "direct_answer"
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
        "evidence_tier": "traceable_related",
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
    "agreement_terms": [],
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
  },
  "internal_notes": []
}
```

Fields may be empty when not relevant, but `canonical_answer.units` should not be thin. Company overview answers usually need business model, economics, drivers, risks, watch items, and caveats in canonical units before display planning.

## Answerability Object

Use this shape in research handoff whenever the query was direct, negative, scenario-based, or broad discovery:

```json
{
  "direct_answerable": false,
  "related_context_available": true,
  "negative_answer_supported": true,
  "needs_user_clarification": false,
  "recommended_answer_mode": "no_direct_evidence_with_related_context",
  "why": "No evidence mentions semiconductor, GPU, or HBM. Related evidence concerns construction commodities, feed gas, Henry Hub, and LNG price exposure."
}
```

Recommended `recommended_answer_mode` values:

```text
direct_answer
no_direct_evidence
no_direct_evidence_with_related_context
related_context_only
not_answerable
needs_clarification
comparison_answer
scenario_answer
```

## Canonical Answer Units

Every visible unit needs:

```text
id
type
text or structured content
importance
confidence
evidence_tier
display_policy
evidence_refs
```

Recommended `confidence` values:

```text
direct
metric_lineage
derived
related
indirect
uncertain
unsupported
```

Recommended `evidence_tier` values:

```text
traceable_direct
traceable_metric_lineage
traceable_related
untraced_direct_candidate
broad_related_candidate
no_direct_evidence
not_answerable
```

Rules:

- A unit with `evidence_tier="traceable_direct"` may support a strong direct claim.
- A unit with `evidence_tier="traceable_metric_lineage"` may support a strong numeric claim.
- A unit with `evidence_tier="traceable_related"` may provide context or caveat, not direct proof of a narrow premise.
- Units with `untraced_direct_candidate` or `broad_related_candidate` should be hidden, internal, or explicitly caveated unless the user asked for exploration.
- `no_direct_evidence` can be visible when the user asked a direct yes/no or negative-evidence question.

## Evidence Refs

Evidence refs connect canonical units to internal evidence without making raw ontology IDs visible by default.

Recommended shape:

```json
{
  "id": "ref_1",
  "source_label": "VG FY2025 10-K",
  "object_ids": ["claim:...", "quote:..."],
  "support_path": ["EvidenceQuote", "ResearchClaim", "ExternalFactorExposure"],
  "evidence_tier": "traceable_direct",
  "trace_status": "traceable",
  "evidence_chain_count": 2,
  "matched_required_facets": ["SPA termination", "debt acceleration"],
  "matched_related_facets": ["liquidity", "collateral"],
  "missing_required_facets": [],
  "why_tier": "Matched SPA termination and debt acceleration with explicit quote support."
}
```

Rules:

- Do not put raw filing quote text in visible canonical units by default. Keep raw quote text inside `evidence_refs` or hidden/internal units unless the user explicitly asks for raw source text.
- Include trace/tier metadata when it materially affects answerability.
- Use reader-facing `source_label` for visible citation labels.

## Unit Types

### Paragraph

Use for synthesized conclusions supported by one or more evidence refs.

```json
{
  "id": "p1",
  "type": "paragraph",
  "text": "VG의 FY2025 10-K에서는 HBM 또는 GPU 가격 변동에 대한 직접 노출 근거는 확인되지 않습니다.",
  "importance": "required",
  "confidence": "direct",
  "evidence_tier": "no_direct_evidence",
  "display_policy": "show",
  "evidence_refs": ["ref_hbm_negative"]
}
```

### Risk / Driver / Headwind Item

Use for `BusinessFactor` role views only when supported by traceable claim/quote or metric lineage.

```json
{
  "id": "r1",
  "type": "risk_item",
  "label": "Feed gas cost exposure",
  "analysis": "Feed gas costs can affect cost of revenue and margin.",
  "impact_channels": ["cost_of_revenue", "gross_margin"],
  "importance": "required",
  "confidence": "direct",
  "evidence_tier": "traceable_direct",
  "display_policy": "show",
  "evidence_refs": ["ref_feed_gas"]
}
```

Rules:

- Do not show role-view labels such as `BusinessFactor` or `ExternalFactorExposure` in user-facing text unless the user asks for audit/debug details.
- If a factor is retrieved but untraced, keep it internal or label it as exploratory only.

### Metric

Use for `MetricObservation` and `Calculation` backed values.

```json
{
  "id": "m1",
  "type": "metric",
  "label": "Revenue",
  "value": "$215.9B",
  "period": "FY2026",
  "analysis": "Reported revenue for the period.",
  "importance": "required",
  "confidence": "metric_lineage",
  "evidence_tier": "traceable_metric_lineage",
  "display_policy": "show",
  "evidence_refs": ["ref_revenue"]
}
```

Rules:

- A metric does not need quote support if XBRL/table/calculation lineage resolves.
- Do not allow raw unformatted numeric values into visible units unless they have validated unit/scale/period.

### Impact Channel / Scenario

Use for scenario or sensitivity answers.

```json
{
  "id": "s1",
  "type": "impact_channel",
  "external_factor": "Natural gas price",
  "scenario": "factor increases",
  "company_effect": "negative",
  "financial_channel": "cost_of_revenue / gross_margin",
  "analysis": "Higher feed gas costs can pressure margins if not fully passed through.",
  "importance": "required",
  "confidence": "direct",
  "evidence_tier": "traceable_direct",
  "display_policy": "show",
  "evidence_refs": ["ref_natural_gas"]
}
```

Rules:

- Use `ExternalFactorExposure.scenario_effects` when available.
- If scenario effects are inferred rather than directly disclosed, label confidence as `indirect` or `related` and keep the bridge explicit.
- Do not claim metric direction unless the trace supports it or the bridge is clearly labeled.

### Timeline Event

Use for `BusinessEvent`-backed project, regulatory, litigation, financing, guidance, or product events.

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
  "evidence_tier": "traceable_direct",
  "display_policy": "show",
  "evidence_refs": ["ref_cp2_phase1_cod"]
}
```

Rules:

- Use `ChangeEvent`-backed units only when discussing disclosure change across periods.
- Label targeted, expected, completed, delayed, pending, approved, denied, withdrawn, or updated status precisely.

### Agreement Term

Use for contract, debt, lease, covenant, purchase commitment, take-or-pay, pricing formula, termination, renewal, and counterparty terms.

```json
{
  "id": "a1",
  "type": "agreement_term",
  "label": "SPA termination and debt acceleration exposure",
  "term": "Termination or suspension of long-term post-COD SPAs",
  "analysis": "The term is linked to potential acceleration of obligations and collateral consequences.",
  "source_label": "VG FY2025 10-K",
  "importance": "required",
  "confidence": "direct",
  "evidence_tier": "traceable_direct",
  "display_policy": "show",
  "evidence_refs": ["ref_spa_termination"]
}
```

Rules:

- Do not create visible agreement-term units from generic contract language without specific economic, timing, party, or obligation details.

### Caveat

Use caveats only when they materially change interpretation.

```json
{
  "id": "c1",
  "type": "caveat",
  "text": "The filing support is direct for the project target date, but the date is forward-looking.",
  "severity": "warning",
  "importance": "required",
  "confidence": "direct",
  "evidence_tier": "traceable_direct",
  "display_policy": "show",
  "evidence_refs": ["ref_cp2_phase1_cod"]
}
```

## Direct / Negative Answer Policy

For direct exposure questions, do not answer yes from broad related evidence.

Example:

```text
Question: VG는 semiconductor memory price cycle 또는 GPU HBM 가격 변동에 직접 노출되어 있나?
```

Correct visible answer:

```text
VG의 FY2025 10-K 기반 근거에서는 semiconductor memory, GPU, HBM 가격 변동에 대한 직접 노출은 확인되지 않습니다. 다만 VG는 construction commodity, feed gas, Henry Hub, LNG 가격 등 broad commodity/energy 가격 리스크에는 노출되어 있습니다.
```

Incorrect:

```text
VG는 commodity price exposure가 있으므로 HBM/GPU 가격에 직접 노출되어 있습니다.
```

## External Market Report Impact

Canonical answer should separate:

```text
1. external market premise
2. company-specific ontology exposure
3. explicit analyst bridge
4. benefit/risk channels
5. caveats about market data not being native ontology evidence
```

Do not use external premise as if it were filing evidence. Do not claim a company-specific direct exposure unless the ontology evidence is `traceable_direct`.

## Company Discovery / Large Universe

For many tickers, do not brute-force every ticker with full queries.

Use:

```text
question context / QueryFrame
-> company_topic_index or global compact discovery
-> candidate ticker narrowing
-> focused query for shortlisted tickers
-> trace selected objects
-> synthesis
```

Discovery output can be used to choose tickers, but final visible conclusions should be based on traced source objects.

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
    "Do not call a targeted COD an achieved COD.",
    "Do not treat traceable_related evidence as direct exposure."
  ],
  "required_source_ids": ["p1", "c1"]
}
```

The display planner may ignore `recommended_blocks` when a simpler markdown layout is clearer, but it must include all required visible units unless the user explicitly requested a narrower answer.

## Quality Rules

- Use `krw_ontology_trace` for exact values, dates, project milestones, contract terms, debt maturities, covenant terms, and guidance.
- Use `krw_ontology_chain` for conclusions that depend on how evidence, claims, metrics, semantic objects, agreement terms, business events, and temporal context connect.
- Use `krw_ontology_quality` when completeness or reliability matters.
- Direct filing evidence outranks inferred bridges.
- Traceable direct evidence outranks traceable related evidence.
- Do not promote rejected or unsupported objects into conclusions.
- Do not promote `answerable=true` into a direct answer unless `direct_answerable=true` or `tier=traceable_direct`.
- If a topic search fails, record the gap and the alternative search path only in internal notes or warnings.
- Do not put internal quality, coverage, index, rejected-object, validation, registry, or pipeline diagnostics into `canonical_answer.units` with `display_policy="show"`.
- If a quality issue materially weakens a candidate point, omit that point, lower confidence privately, or keep the uncertainty inside hidden/internal fields. Do not add visible generic caveats about evidence availability, missing quantification, extraction status, filing coverage, or quality unless the user explicitly asks for audit/debug/quality details.
- Debuggable details such as object counts, section quality, batch failures, rejected objects, trace IDs, catalog output, registry versions, validation reports, stale artifact flags, and index inventory belong only in `evidence_refs`, `quality`, `internal_notes`, or hidden-by-default units unless the user explicitly requests audit/debug output.

## What Not To Do

- Do not emit `answer_blocks` from the research skill.
- Do not emit `display_plan` from the research skill.
- Do not decide frontend layout, HTML, React, CSS, or mobile rendering.
- Do not paste raw ontology object text into canonical units.
- Do not expose ontology object type names such as `ResearchClaim`, `EvidenceQuote`, `BusinessFactor`, `BusinessActivity`, `ExternalFactorExposure`, `MetricObservation`, `Calculation`, `XBRLFact`, `AgreementTerm`, `BusinessEvent`, `SupportLink`, `Edge`, `CompanyBusinessProfile`, `RiskFactor`, `GrowthDriver`, `Headwind`, `FinancialMetricValue`, `DerivedMetricValue`, `NumericEvidence`, `객체`, or `온톨로지 객체` in visible canonical units unless the user explicitly asks for audit/debug output.
- Do not paste original filing quote text into visible canonical units. Use reader-facing source labels only, and keep quote text inside evidence refs or hidden/internal units by default.
- Do not add customer-facing sections named "quality note", "품질 노트", "coverage note", "debug note", "데이터 커버리지", or similar operational footers unless the user explicitly asks for them.
- Do not let numeric values pass through unless they are validated.
- Do not leave the display planner to infer missing content.
- Do not answer a direct exposure question with broad related evidence as if it were direct.
- Do not hide `no_direct_evidence` when the user asked a yes/no directness question.
