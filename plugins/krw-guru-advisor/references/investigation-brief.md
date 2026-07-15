# Guru Investigation Brief

This contract makes a selected Guru ontology philosophy drive both the evidence
search and a distinct author-inspired virtual-advisor interpretation, without
turning the user experience into a chatbot, a generic template, or a real
person identity simulation.

## Inputs

The main Guru agent receives only:

```text
selected author key
GuruResearchPack.philosophy_context and selected_lenses
app-provided GuruLightCompanyContext
the user's question
```

`GuruLightCompanyContext` is neutral orientation only. Its trusted anchors can
describe the company, business, products, sector, revenue logic, and filing
availability. It is not filing evidence and must not contain an investment
thesis, a valuation view, or a risk conclusion.

`philosophy_context` contains selected reviewed ontology objects, their source
grounded decision statements, applicability, evidence hooks, and source anchor
IDs. The main agent must not use principles not present in that response.

## Draft And Seal

The main Guru drafts exactly one company-specific key investigation question.
That question may combine several decision dimensions, but it must state one
central investment tension. Its distinct filing proof needs belong in
`evidence_needed`, not in extra questions. The one draft must contain:

```text
question text tied to this company and this user question
one or more selected principle reviewed IDs
one or more trusted light-context anchor IDs
why the filing evidence could strengthen, weaken, or leave the principle unresolved
`decision_role`: `main_tension`
```

Replacing the company with another issuer must make the question materially
worse. A generic question such as “does it have a moat?” is invalid.

The server accepts exactly one draft. If `decision_role` is omitted, it applies
the structural role `main_tension`; it never writes the question, narrows its
meaning, or supplies a conclusion. The role is sealed with the question so the
final answer can remain philosophy-shaped without pre-writing an investment
answer for the model.

The agent then calls `krw_guru_company_brief` with the trusted company context
and `investigation_questions`. The runtime attaches the immutable research-pack
projection from the preceding Guru query, so the model never has to copy a large
tool result. This prevents the brief tool from re-ranking the philosophy and
sealing a different principle set. The tool validates the selected principle
IDs and trusted context anchors, derives stable question IDs, and returns a
sealed `investigation_brief` with `brief_hash`. The server does not write or
improve the questions in this path.

After sealing, no caller may modify the questions, IDs, principle linkage,
context linkage, or hash. The sealed brief is the only research assignment for
the filing evidence subagent.

## Evidence And Analysis

```text
sealed investigation_brief
-> company_evidence_researcher
-> one to three complementary atomic SearchPlan v2 clauses for the sealed key question
-> query_context, query, and trace return actual ResearchState v2 filing results
-> runtime-built krw-guru-company-research-context/v1
-> private main-Guru agent_analysis
-> krw_guru_review_company_evidence(question + agent_analysis; runtime-attached validation state)
-> final answer
```

The company subagent searches filings and does not provide a Guru conclusion.
The runtime, rather than the model, builds the company research context from
the actual ResearchState v2 evidence units and their source object IDs. The
main Guru may reason over numbers privately, but its analysis must cite only
those runtime-owned source IDs for the matching sealed question. The review
tool validates those links and rejects unsupported or cross-question claims; it
does not draw the final conclusion.

`evidence_needed` is a menu of possible filing tests, not a checklist that must
all be searched. The company subagent turns the one sealed key question into
one to three complementary, independently verifiable clauses. Those clauses
may cover metric, relationship, and countercase proof needs, but they never
become new investigation questions or trigger a broad company sweep. A key
question with no direct filing support remains `unresolved`.

For the review call, the model supplies only the question and analysis content:
non-empty `assessments` plus an `overall_judgment`. Each assessment contains
`question_id`, `verdict` (`mixed` or `unresolved`), `evidence_object_ids`, and
`reasoning`. The runtime attaches the sealed brief, exact company research
context, ticker, author key, and identity hashes. Contextual filing results can
inform an interpretation but do not by themselves authorize a `supported`
verdict. The model must not copy, serialize, or recreate that runtime-owned
state.

The review may return English `input_correction_required` JSON. This is a
single-call correction contract, not a retry loop: use the supplied
`question_id`, allowed verdicts, and allowed evidence IDs to make at most one
corrected review call in the same SDK run.

## Final Rendering Boundary

The final answer must not expose the investigation brief, tool names, evidence
IDs, or internal analysis. It must render a distinct author-inspired virtual
advisor from the selected principle statements and consultation moves, without
claiming to be the selected person, using that person's first person, invented
personal experience, fabricated quotes, or signature catchphrases. It may use
figures, dates, periods, ratios, ranges, and other quantitative evidence when
they are present in the runtime company research context and material to the
answer. A short comparison to a documented historical episode or prior cycle
is allowed only when it appears in the selected Guru ResearchPack. State it in
third person as the author's documented history, never as the advisor's
personal memory. Explain business meaning, uncertainty, and change conditions
through the sealed key question without inventing precision.
