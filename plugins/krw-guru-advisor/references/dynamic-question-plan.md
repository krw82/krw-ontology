# Dynamic Guru Question Plan

Company-specific Guru answers must not start from a generic company research summary.

For a selected guru and selected company, the first company bridge should create a
dynamic question plan:

```text
Guru ResearchPack
+ runtime company context
+ user question
+ filing topics
-> dynamic_question_plan
```

The plan is internal. Never expose `dynamic_question_plan`, question IDs, payload
field names, or retrieval terms to the user.

## Purpose

The plan turns the selected guru lens into company-specific questions before the
company evidence subagent searches filings.

Good dynamic questions are not generic:

```text
Bad:
- Is this a good business?
- Does the company have a moat?
- Is capital allocation good?

Good:
- For SLB, does Digital/Data Center Solutions materially reduce exposure to
  customer upstream capex cycles, or is the core thesis still cyclical?
- For AAPL, does Services make the business independently recurring, or does it
  still depend on the iPhone installed base?
- For OXY, does cash generation remain investable at lower oil prices, or does
  the thesis require a commodity-price tailwind?
```

If replacing the company with another ticker still sounds natural, the question is
too generic and should be rewritten.

## Required Flow

For company-specific questions:

```text
1. Call krw_guru_query_context.
2. Call krw_guru_company_brief.
3. Read company_filing_brief.dynamic_question_plan.
4. Pass the plan to Agent(subagent_type="company_evidence_researcher").
5. The subagent searches latest 10-Q/current-driver evidence first and latest
   10-K as baseline, selects exact object IDs, and calls
   krw_ontology_verify_evidence once for every question_id.
6. The subagent returns the exact krw-verified-company-evidence/v1 payload
   unchanged.
7. Pass that exact payload to krw_guru_review_company_evidence.
8. Write the final answer as flexible consultation prose.
```

The company evidence subagent must answer the plan. It should not replace the
plan with a broad report, industry overview, price target, or buy/sell conclusion.

## Subagent Output Shape

The subagent does not author a memo. It returns the verifier payload exactly:

```json
{
  "format": "krw-verified-company-evidence/v1",
  "release_id": "...",
  "ticker": "SLB",
  "current_driver": {"period": "...", "document_type": "10-Q"},
  "annual_baseline": {"period": "...", "document_type": "10-K"},
  "evidence_by_question": [
    {
      "question_id": "q_...",
      "evidence": [
        {
          "source_object_id": "...",
          "trace_status": "traceable_direct",
          "usable_for_strong_claim": true,
          "document": {"period": "...", "document_type": "10-Q"},
          "verified_excerpt": "..."
        }
      ],
      "answerability": "verified|interpretation_only|not_verified"
    }
  ],
  "rejected_refs": [],
  "verification_summary": {},
  "pack_hash": "..."
}
```

The exact shape and hash are mandatory. Do not rewrite, summarize, wrap,
reorder, add findings, or remove fields. The IDs and payload remain internal and
must not appear in the final user answer.

## Final Answer

The final Guru answer should use the memo silently.

Do:

```text
- Lead with the core investor tension.
- Translate evidence into interpretation, durability, incentives, risks, and
  change conditions.
- Use only a few exact figures unless the user asks for numeric detail.
- Keep the selected guru's voice and question style.
```

Do not:

```text
- Print the question plan.
- Use fixed report headings.
- Dump all retrieved numbers.
- Say "the subagent found..." or "the plan says..."
- Let one exciting company fact overpower the guru lens.
```
