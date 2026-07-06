# Guru ResearchPack Contract

The Guru MCP returns a bounded `GuruResearchPack` for one selected guru lens and one investor question. This pack is the only source for persona, decision frame, selected lenses, source confidence, and tool guidance.

Skills must not hard-code guru principles, preferred questions, or persona traits. If the pack does not contain a lens or persona trait, the final answer must not invent it.

## Required Shape

```text
research_context_version
research_status
answerability
intent
agent_autonomy
do_not_call
research_pack
```

`research_pack` contains:

```text
pack_meta
persona_profile
selected_lenses
consultation_moves
data_needs
source_anchors
clarifying_questions
company_bridge
trace_recommendations
warnings
```

## Status Handling

```text
sufficient_lens
- The answer may use selected guru ontology lenses directly.

partial_lens
- The answer may apply the lens, but must state that the direct source match is not strong.

ontology_gap
- Do not fill the gap from memory. Say the current guru ontology did not return enough support.

needs_clarification
- Ask the returned clarifying question before giving a strong consultation.

needs_company_evidence
- Guru lens is available, but company-specific judgment needs filing evidence from KRW Ontology company research.
```

## Evidence Boundary

`direct_source_match=true` means the pack found strong source-grounded support.  
`direct_source_match=false` means the answer must be framed as lens application, not as a direct documented guru view.

Source anchors are metadata only. Do not quote full private text unless a trace tool explicitly returns a compliant excerpt.

