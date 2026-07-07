# Company Bridge Policy

Guru ontology supplies investor lenses. KRW Ontology filing research supplies company facts.

If `research_status=needs_company_evidence` or `research_pack.company_bridge.requires_company_evidence=true`, do not finish a company-specific judgment from Guru data alone.

## Bridge Workflow

For company-specific questions:

```text
1. Build the private internal guru consultation brief.
2. If a ticker/company is identified and company MCP tools are available, read company orientation first with krw_ontology_topic_map or an equivalent compact company profile/context call.
3. Convert that company ontology result into bounded company_context_json. It should contain only runtime context, never hard-coded ticker mappings.
4. Call krw_guru_query_context with the fixed author_key, the brief-optimized question, and company_context_json when available.
5. Read research_pack.company_bridge, company_context, and data_needs.
6. If company evidence is required, call krw_guru_company_brief with the same company_context_json.
7. Application/orchestrator code passes the returned company_research_question_ko/en and required_filing_topics to the existing KRW Ontology filing research path.
8. If company filing evidence is returned, call krw_guru_company_pack with that opaque company evidence payload and the same company_context_json.
9. Compose the final answer from GuruCompanyResearchPack and render_plan by separating:
   - guru ontology lens
   - filing-supported company facts
   - missing company or portfolio evidence
   - practical next question
```

This plugin must not call the existing KRW Ontology router skill automatically. Application code owns routing and may explicitly invoke the company research path after the Guru ResearchPack asks for filing evidence.

## Company Context Prepass

`company_context_json` is the bridge between company ontology orientation and guru lens retrieval. It exists so the guru selector can use company-specific vocabulary from the existing KRW Ontology runtime without learning ticker-specific shortcuts.

Preferred source:

```text
krw_ontology_topic_map(ticker=..., limit=...)
```

Acceptable equivalents:

```text
compact CompanyBusinessProfile / BusinessActivity / ExternalFactorExposure result
compact query_context result that exposes company-specific topics
app-provided company ontology context already read by the product runtime
```

Normalize the result into:

```json
{
  "source": "company_mcp_topic_map",
  "ticker": "OXY",
  "company_name": "Occidental Petroleum",
  "context_terms": ["oil and gas", "commodity price exposure"],
  "business_context_terms": ["upstream assets", "capital intensive production"],
  "risk_context_terms": ["oil price sensitivity", "reserve replacement"],
  "available_company_topics": ["commodity_price_exposure", "capital_intensity", "cash_flow"],
  "confidence": "medium"
}
```

Rules:

```text
- Do not infer sector, industry, products, or exposures from the ticker by memory.
- Do not use company_context_json as final evidence.
- Use it to improve guru lens selection and filing brief construction only.
- Pass the same context to krw_guru_query_context, krw_guru_company_brief, and krw_guru_company_pack.
- If company context is unavailable, continue without it and let krw_guru_company_brief request the missing filing evidence.
```

Company evidence may be needed for:

```text
business model
cash generation
balance sheet
capital allocation
risk factors
segment exposure
management commentary
valuation context
```

Map Guru `company_bridge.filing_evidence_requirements` into company research concepts such as:

```text
business model -> CompanyBusinessProfile, BusinessActivity, ResearchClaim
cash generation -> cash flow, operating cash flow, FCF, capex, working capital
balance sheet -> debt, liquidity, maturity, covenant, interest expense
capital allocation -> share repurchases, dividends, acquisitions, reinvestment, dilution
risk factors -> RiskFactor, ExternalFactorExposure, Headwind, management discussion
valuation context -> filing-supported drivers only, not target price or fair value
```

`GuruCompanyFilingBrief` is the bridge payload for this mapping. It contains:

```text
company_research_question_ko
company_research_question_en
company_context
query_terms
required_filing_topics
candidate_filing_topics
filtered_out_topics
lens_specific_evidence_requests
generic_filing_requirements
recommended_company_mcp_call
```

Use `required_filing_topics` for the immediate company filing query. Treat `candidate_filing_topics` as background context only. Do not put `filtered_out_topics` into the company MCP query unless the user's question explicitly asks for that excluded instrument or topic.

Examples:

```text
ASML monopoly-risk question
-> keep: risk_factors, competitive position, demand_cycle_exposure, margin_pressure
-> filter out: convertible conversion price, coupon, put/call option terms

credit/convertible/debt question
-> allow: maturity, coupon, refinancing, conversion terms
```

`GuruCompanyResearchPack` is the post-company-evidence answer-prep payload. It contains:

```text
guru_pack
company_context
company_filing_brief
company_evidence_pack
evidence_alignment
missing_evidence
judgment_conditions
render_hints
answer_contract
```

If filing evidence is unavailable in the current runtime, do not invent the company facts. Give the guru-lens answer and state the exact evidence that would be needed to finish the company-specific judgment.

This plugin must not modify the existing KRW company ontology schema, MCP tools, router skill, or company artifacts.
