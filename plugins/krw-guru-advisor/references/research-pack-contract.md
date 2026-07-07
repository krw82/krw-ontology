# Guru ResearchPack Contract

The Guru MCP returns a bounded `GuruResearchPack` for one selected guru lens and one investor question. This pack is the only source for persona, decision frame, selected lenses, source confidence, and tool guidance.

Skills must not hard-code guru principles, preferred questions, or persona traits. If the pack does not contain a lens or persona trait, the final answer must not invent it.

Before requesting the pack, the skill should build a private internal guru consultation brief. The brief improves retrieval. It does not become evidence and does not override the returned ResearchPack.

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
company_context
source_anchors
clarifying_questions
company_bridge
trace_recommendations
warnings
```

## Company-Aware Serving Contracts

For company-specific questions, the Guru ResearchPack is not the final answer payload. Use the serving-layer bridge objects:

```text
company ontology orientation from krw_ontology_topic_map or equivalent
-> company_context_json
-> GuruResearchPack
-> GuruCompanyFilingBrief
-> KRW Ontology company filing evidence from application/orchestrator
-> GuruCompanyResearchPack
-> GuruAnswerRenderPlan
```

`company_context_json` is bounded runtime context from existing company ontology reads. It improves retrieval and filing-brief construction, but it is not final company evidence and must not be created from ticker hard-coding.

`GuruCompanyFilingBrief` converts guru lens/data needs and company_context_json into a company filing research question. It does not contain company facts.

`GuruCompanyResearchPack` combines the Guru ResearchPack, company_context_json, and opaque company filing evidence supplied by the application/orchestrator. It is not a database schema and must not expand the KRW company ontology.

`GuruAnswerRenderPlan` provides answer structure and author voice posture. It must not introduce new guru principles that were absent from the ResearchPack.

`missing_evidence` is an internal completeness signal. It helps the answer agent know where the company-specific judgment is still thin. It is not a required final-answer section. In normal user answers, mention it only as one concise next check when it changes the practical judgment. Do not turn it into a report-style "data limitation" block or invent a long metric checklist from general finance memory.

## Answer Style Inputs

Answer style must come from:

```text
persona_profile
selected_lenses
consultation_moves
data_needs
source_anchors
```

The author adapter may shape tone and question order, but it must not override the ResearchPack. If the pack does not return a supporting lens, the answer should say the current guru ontology has weak support instead of filling from memory.

The private brief may guide which query terms were used. It must not add final-answer content unless the ResearchPack returned supporting lenses, consultation moves, or data needs.

## Status Handling

```text
sufficient_lens
- The answer may use selected guru ontology lenses directly.

partial_lens
- The answer may apply the returned materials, but must keep confidence limits natural and conversational. Do not add a source/disclaimer footer.

ontology_gap
- Do not fill the gap from memory. Say the current guru ontology did not return enough support.

needs_clarification
- Ask the returned clarifying question before giving a strong consultation.

needs_company_evidence
- Guru lens is available, but company-specific judgment needs filing evidence from KRW Ontology company research. Call krw_guru_company_brief with company_context_json when available; use krw_guru_company_pack after company evidence is supplied.
```

## Evidence Boundary

`direct_source_match=true` means the pack found strong source-grounded support.  
`direct_source_match=false` is an internal confidence signal. In the final answer, handle it with natural phrases such as "내가 지금 볼 수 있는 자료만 놓고는..." or "아직 단정하면 안 되는 부분은..." without saying "AI lens", "source match", "direct documented guru view", or adding a footer disclaimer.

Source anchors are metadata only. Do not quote full private text unless a trace tool explicitly returns a compliant excerpt.
