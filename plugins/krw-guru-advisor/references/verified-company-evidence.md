# Company Research Context

The default Guru company path does not create or require a
`krw-verified-company-evidence/v1` pack. Its evidence path is:

```text
krw_guru_company_brief
-> sealed investigation_brief and brief_hash
-> company_evidence_researcher
-> query_context, query, and trace filing results (ResearchState v2)
-> runtime-built krw-guru-company-research-context/v1
-> main Guru private analysis
-> krw_guru_review_company_evidence validation
```

The runtime context is hash-bound to the sealed brief and contains only the
returned filing evidence units and source object IDs. The company evidence Agent
does not make a pack, thesis, or final answer. It searches filings; the runtime
selects the compact context without trusting an agent-authored summary.

The main Guru may use that context for private numeric reasoning. Every cited
object ID must come from that context and match the one sealed key question.
Because this is contextual research rather than a verifier certificate, the
main Guru may use only `mixed` or `unresolved` assessment verdicts. The review
tool checks this linkage; it does not replace the Guru's reasoning or write
user-facing prose.

The main Guru may infer investment significance from filing facts, but an
inference is not a new filing fact. Keep the material assumption and the
condition that would weaken the reading in private analysis, then express the
qualitative decision boundary naturally in final prose. No runtime-context
field, exact ID, hash, or internal question is visible to the user.
