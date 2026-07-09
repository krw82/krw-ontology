# Verified company evidence contract

Named-company Guru consultations use a source-lineage handoff. The company
subagent may search and select candidate ontology objects, but it may not write
its own filing findings or provenance payload.

Required internal sequence:

```text
krw_guru_company_brief
-> exact dynamic_question_plan with question_id values
-> Agent(subagent_type="company_evidence_researcher")
-> company filing search and exact object-id selection
-> one krw_ontology_verify_evidence call
-> exact krw-verified-company-evidence/v1 return
-> krw_guru_review_company_evidence
-> final Guru consultation prose
```

## Subagent contract

- Work English-first for retrieval.
- Keep every `question_id` from the dynamic plan unchanged.
- Select no more than eight unique object IDs in total.
- Prefer `ResearchClaim`, `EvidenceQuote`, `SourceSpan`, or
  `MetricObservation` IDs when a direct fact or exact figure matters. Use
  semantic objects such as `BusinessFactor` only for qualitative framing.
- Call `krw_ontology_verify_evidence` exactly once after candidate selection.
- Pass every dynamic question as `{question_id, object_ids}`. Use an empty
  `object_ids` array when no candidate can be verified.
- Return the verifier's complete JSON exactly as received. Do not summarize,
  wrap, reorder, add findings, remove fields, or recompute the hash.
- Do not write the final user answer or imitate the selected guru.

## Main Guru contract

- Pass only the exact verifier return to `krw_guru_review_company_evidence`.
- Never pass a memo written by the model, a retrieval plan, a coverage note, or
  `company_filing_brief` as company evidence.
- `usable_for_strong_claim=true` may support a direct company claim.
- Interpretation-only evidence may frame uncertainty but is not direct proof.
- `answerability=not_verified` lowers confidence; it must not be filled from
  model memory.
- Exact figures, ratios, thresholds, and forecasts must come from verified
  excerpts or metric lineage. Do not invent a rule such as a margin, leverage,
  payout, or growth threshold.
- Use `current_driver` for the current condition and `annual_baseline` for
  annual mix, long-term structure, and historical risk context.

The IDs, hash, trace grades, payload names, tool names, and verification process
are internal. Never expose them in the user-facing answer.
