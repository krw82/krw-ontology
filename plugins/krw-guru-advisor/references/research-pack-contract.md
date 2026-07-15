# Guru ResearchPack Contract

The Guru MCP returns a bounded `GuruResearchPack` for the fixed selected author
and investor question. It is the only source for ontology-backed decision rules,
source confidence, and tool guidance. A skill must not add a remembered
principle, preferred question, or personality trait.

Before calling the MCP, the main agent may build an English-first private
retrieval brief. That brief helps matching only; it is not evidence and cannot
override the returned pack.

## Relevant Fields

```text
research_status
answerability
intent
research_pack.philosophy_context
research_pack.selected_lenses
research_pack.consultation_moves
research_pack.data_needs
research_pack.company_context
research_pack.company_bridge
research_pack.clarifying_questions
```

`philosophy_context` is the authoritative object for the agent-generated brief.
Each principle includes a selected reviewed ID, source-grounded statement,
applicability, evidence hooks, and source anchors. Use it to decide which
company-specific questions are worth investigating. It does not authorize a
conclusion before relevant filing evidence is retrieved.

`company_context` is bounded runtime orientation. It must come from existing
application company metadata, never ticker hard-coding, and is not company
evidence.

## Company Evidence Flow

```text
light company context + selected philosophy
-> main Guru drafts one philosophy-shaped key investigation question
-> krw_guru_company_brief seals investigation_brief
-> company_evidence_researcher
-> runtime-built company research context from actual filing-tool results
-> private agent_analysis
-> krw_guru_review_company_evidence linkage validation
-> final qualitative consultation
```

The runtime-built company research context is the sole source of company object
IDs available to the default Guru review. The subagent does not author that
context. The main Guru owns the analysis; the review tool only validates it
against the sealed key question and exact returned filing evidence.

`needs_company_evidence` requires this path. A missing or rejected evidence
context is a reason to narrow the answer, not to invent a company fact.

## Rendering Boundary

Selected ontology philosophy may change the questions, inference priorities,
and the virtual advisor's narrative stance. It must not be rendered as the
real author speaking, claiming personal experience, or using fabricated quotes.
The selected principles and consultation moves must make the final answer feel
different from a generic analyst summary. Historical comparisons are allowed
only when the documented episode is actually present in the selected
ResearchPack, and must be phrased in third person. Final answer rules,
including the use of material filing figures, are defined by
`output-contract.md`.
