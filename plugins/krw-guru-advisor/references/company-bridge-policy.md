# Company Bridge Policy

The company bridge protects a strict separation of responsibilities.

```text
main Guru: select philosophy, draft one key question, analyze evidence
company brief tool: validate and seal the draft
company evidence Agent: search filings through query_context, query, and trace
runtime: build company research context from returned filing evidence
review tool: validate analysis-to-evidence links
final renderer: express only qualitative user-facing judgment
```

The main Guru is given a neutral `GuruLightCompanyContext` from application
metadata. It contains only trusted company descriptors and anchor IDs. It is
not a thesis and cannot be used as evidence.

For the one key-question draft, the company brief tool receives the runtime-attached
immutable Guru research pack that selected the cited principles. It checks that
the cited principle is selected by that immutable result and that every cited
context anchor exists in the supplied light context. It derives a stable sealed question ID and
`brief_hash`. No participant may subsequently alter the sealed content.

Evidence belongs only to the matching sealed key question. A principle is
unresolved when contextual filing research lacks appropriate evidence; a
contextual result cannot be upgraded to a `supported` claim. The review tool
rejects cross-question citations, fabricated object IDs, mismatched hashes, and
analysis that claims more support than the runtime context provides.

The model authors the analysis content only: `assessments` and
`overall_judgment`. Before `krw_guru_review_company_evidence` executes, the
runtime deterministically attaches the sealed brief, company research context,
ticker, author key, and matching identity hashes. This keeps copied tool
payloads out of model input and prevents a formatting retry from changing the
evidence contract. The default review accepts only `mixed` or `unresolved`
verdicts, with object IDs present in that exact runtime context.

The legacy dynamic plan is a feature-flag rollback path only. It is not an
alternative input to a sealed investigation brief.
