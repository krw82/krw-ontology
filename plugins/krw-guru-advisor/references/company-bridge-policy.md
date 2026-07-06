# Company Bridge Policy

Guru ontology supplies investor lenses. KRW Ontology filing research supplies company facts.

If `research_status=needs_company_evidence` or `research_pack.company_bridge.requires_company_evidence=true`, do not finish a company-specific judgment from Guru data alone.

## Bridge Workflow

For company-specific questions:

```text
1. Build the private internal guru consultation brief.
2. Call krw_guru_query_context with the fixed author_key.
3. Read research_pack.company_bridge and data_needs.
4. If company evidence is required, pass the filing evidence requirements to the existing KRW Ontology filing research path when the runtime supports it.
5. Compose the final answer by separating:
   - guru ontology lens
   - filing-supported company facts
   - missing company or portfolio evidence
   - practical next question
```

This plugin must not call the existing KRW Ontology router skill automatically. Application code owns routing and may explicitly invoke the company research path after the Guru ResearchPack asks for filing evidence.

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

If filing evidence is unavailable in the current runtime, do not invent the company facts. Give the guru-lens answer and state the exact evidence that would be needed to finish the company-specific judgment.

This plugin must not modify the existing KRW company ontology schema, MCP tools, router skill, or company artifacts.
