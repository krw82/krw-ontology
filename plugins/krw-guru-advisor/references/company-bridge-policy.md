# Company Bridge Policy

Guru ontology supplies investor lenses. KRW Ontology filing research supplies company facts.

If `research_status=needs_company_evidence` or `research_pack.company_bridge.requires_company_evidence=true`, do not finish a company-specific judgment from Guru data alone.

## Bridge Workflow

For company-specific questions:

```text
1. Build the English-first private internal guru consultation brief.
2. If a ticker/company is identified, use app-provided company context when it already exists. Do not call KRW Ontology company MCP tools directly from the main Guru run.
3. Convert only that app-provided runtime context into bounded company_context. It should contain only runtime context, never hard-coded ticker mappings.
4. Call krw_guru_query_context with the fixed author_key, the English-first brief-optimized question, and company_context when available.
5. Read research_pack.company_bridge, company_context, and data_needs.
6. If company evidence is required, call krw_guru_company_brief with the same company_context.
7. Read company_filing_brief.dynamic_question_plan. This plan is the highest-priority contract for company evidence. It converts the selected guru lens into company-specific filing questions.
8. The app-provided company_evidence_researcher subagent passes the dynamic_question_plan, company_research_question_ko/en, and required_filing_topics to the existing KRW Ontology filing research path. It selects exact object IDs, calls krw_ontology_verify_evidence once with every question_id, and returns the complete verifier payload unchanged. It must not replace the plan with a broad company report or model-authored memo.
9. Pass only that exact krw-verified-company-evidence/v1 payload to krw_guru_review_company_evidence with the same company_context. Never pass an empty status note, self-authored findings, a retrieval plan, or company_filing_brief. If review is denied, retry the subagent and preserve the verifier output exactly.
10. Use krw_guru_trace, krw_guru_chain, or krw_guru_evidence only for extra guru-source support after the company evidence path is satisfied; those tools do not replace company filing evidence.
11. Compose the final answer from Guru ResearchPack, dynamic question answers, subagent company evidence, and any available Guru evidence review by separating:
   - guru ontology lens
   - filing-supported company facts
   - missing company or portfolio evidence
   - practical next question
```

This plugin must not call the existing KRW Ontology router skill automatically. The app runtime owns routing and may explicitly invoke the company_evidence_researcher subagent after the Guru ResearchPack asks for filing evidence.

## Company Context Prepass

`company_context` is the bridge between company ontology orientation and guru lens retrieval. It exists so the guru selector can use company-specific vocabulary from the existing KRW Ontology runtime without learning ticker-specific shortcuts.

The company orientation may originate from Korean UI context, but Guru MCP retrieval should still be English-first. Convert the company issue and investor question into English investment language before calling `krw_guru_query_context`; keep Korean wording only as secondary context.

Preferred source when the app runtime provides company orientation:

```text
app-provided compact KRW Ontology company context, such as a topic map, company profile, or scoped query_context result
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
- Do not use company_context as final evidence.
- Use it to improve guru lens selection and filing brief construction only.
- Pass the same context to krw_guru_query_context, krw_guru_company_brief, and krw_guru_review_company_evidence.
- If company context is unavailable, continue without it and let krw_guru_company_brief plus the company_evidence_researcher subagent request the missing filing evidence.
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
dynamic_question_plan
query_terms
required_filing_topics
candidate_filing_topics
filtered_out_topics
lens_specific_evidence_requests
generic_filing_requirements
recommended_company_mcp_call
```

Use `required_filing_topics` for the immediate company filing query. Treat `candidate_filing_topics` as background context only. Do not put `filtered_out_topics` into the company MCP query unless the user's question explicitly asks for that excluded instrument or topic.

`dynamic_question_plan` is the preferred evidence contract for company-specific Guru answers. It should contain company-specific questions such as "Does SLB's data-center exposure actually reduce upstream capex cyclicality?" rather than generic prompts such as "Does the company have a moat?" If replacing the company with another ticker still sounds natural, the question is too generic.

Examples:

```text
ASML monopoly-risk question
-> keep: risk_factors, competitive position, demand_cycle_exposure, margin_pressure
-> filter out: convertible conversion price, coupon, put/call option terms

credit/convertible/debt question
-> allow: maturity, coupon, refinancing, conversion terms
```

`CompanyEvidencePack` is the hash-stable `krw-verified-company-evidence/v1` payload returned by `krw_ontology_verify_evidence` through the app-provided company_evidence_researcher subagent. It contains verified source excerpts, metric lineage, filing anchors, rejected references, and per-question answerability. The subagent must return it unchanged and must not imitate a guru voice or write the final answer.

`GuruCompanyEvidenceReview` is the default post-company-evidence interpretation payload. It contains:

```text
selected lens support / weakness
evidence_alignment
missing_evidence
what_to_emphasize
what_not_to_overstate
change_conditions
answer_contract
```

It gives interpretation guidance only. It must not introduce new guru principles that were absent from the ResearchPack, new company facts absent from CompanyEvidencePack, or a fixed report template.

If filing evidence is unavailable in the current runtime, do not invent the company facts. Give the guru-lens answer and state the exact evidence that would be needed to finish the company-specific judgment.

This workflow does not modify the KRW company ontology schema, index schema, router skill, or company artifacts. Verification is a read-only serving-layer operation over existing trace lineage.
