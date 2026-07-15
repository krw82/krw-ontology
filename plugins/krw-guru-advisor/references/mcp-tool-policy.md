# Guru MCP Tool Policy

Use the Guru MCP as a read-only ontology layer. The main Guru does not call the
KRW filing tools directly; the designated company evidence Agent owns that
work.

## Default Company Workflow

```text
English-first private retrieval brief
-> krw_guru_query_context with fixed author key and light company context
-> main Guru drafts exactly one company-specific key question from philosophy_context
-> krw_guru_company_brief(investigation_questions; runtime attaches selected research pack) seals investigation_brief
-> Agent(company_evidence_researcher, exact sealed brief)
-> query_context, query, and trace return actual ResearchState v2 filing results
-> runtime builds krw-guru-company-research-context/v1
-> private main-Guru agent_analysis
-> krw_guru_review_company_evidence(question, agent_analysis; runtime attaches brief and context)
-> final qualitative answer
```

`krw_guru_company_brief` validates and seals the one main-agent key-question
draft against the
runtime-attached immutable research pack that selected its principles; it does
not re-query or author a question in this default path. `krw_guru_review_company_evidence`
validates source linkage and evidence status; it does not supply a Guru
conclusion or final prose.

For the default review call, the model sends only the user/company
question and its `agent_analysis` (`assessments` plus `overall_judgment`). The
runtime attaches the sealed `investigation_brief`, company research context
built only from actual filing-tool results, ticker, selected author key, and
matching identity hashes. Never copy these runtime-owned fields into the tool
input or reconstruct them from tool output.
If either company-brief or review returns an English `input_correction_required` JSON, repair only
the fields named by `invalid_fields` and make the next allowed call in the same
SDK run; do not restart the workflow or retry unchanged input.

Valid review input:

```json
{
  "question": "Assess AAPL Services durability.",
  "agent_analysis": {
    "assessments": [
      {
        "question_id": "q_services_durability",
        "verdict": "mixed",
        "evidence_object_ids": ["claim:AAPL:q_services_durability"],
        "reasoning": "The available evidence supports recurring demand, but does not fully separate Services from the device ecosystem."
      }
    ],
    "overall_judgment": "Services durability appears improving but remains only partly verified as independent from devices."
  }
}
```

If `agent_analysis` is absent, incomplete, or cites evidence outside its sealed
key question, the review call is not executed. MCP returns an English `isError`
correction payload in the same run; it does not rewrite the analysis or start
another run. For a missing analysis, the payload is:

```json
{
  "status": "input_correction_required",
  "code": "missing_agent_analysis",
  "message": "agent_analysis is required for the sealed key question.",
  "required_change": "Provide one assessment for the sealed key question and a complete overall_judgment.",
  "invalid_fields": ["agent_analysis"],
  "allowed_next_tools": ["krw_guru_review_company_evidence"]
}
```

Do not add `company_payload`, `investigation_brief`, `brief_hash`,
`company_research_context`, `ticker`, or `author_keys` to repair this error.
They are runtime-owned values.
For a question-specific failure, the payload includes `violations`. Correct
only the named assessment; it provides the exact `question_id`, allowed
`verdict` values, and allowed `evidence_object_ids`. For example:

```json
{
  "status": "input_correction_required",
  "code": "unsupported_verdict",
  "message": "Question q_services_durability cannot use verdict 'supported' with answerability=interpretation_only.",
  "required_change": "Choose one of the allowed verdicts for this question without adding new evidence.",
  "invalid_fields": ["agent_analysis.assessments[0].verdict"],
  "violations": [
    {
      "question_id": "q_services_durability",
      "allowed_verdicts": ["mixed", "unresolved"],
      "allowed_evidence_object_ids": ["claim:AAPL:q_services_durability"]
    }
  ],
  "allowed_next_tools": ["krw_guru_review_company_evidence"]
}
```

Never retry the unchanged payload or ask the MCP to infer the intended
analysis. One initial review call and one corrected call are allowed in the
same SDK run.

For the company-brief call, the active generated-brief workflow also returns a
structured correction rather than letting the runtime invent or split a
question. For example:

```json
{
  "status": "input_correction_required",
  "code": "exactly_one_key_question_required",
  "message": "The Guru company workflow accepts exactly one philosophy-shaped key question.",
  "required_change": "Provide investigation_questions as an array with exactly one draft. Keep separate filing proof needs in that draft's evidence_needed field, not as additional questions.",
  "invalid_fields": ["investigation_questions"],
  "allowed_next_tools": ["krw_guru_company_brief"]
}
```

Correct only that call in the same SDK run. The MCP never chooses a substitute
question or turns its proof needs into a fixed checklist.

The evidence subagent must not return a memo, thesis, recommendation, Guru
voice, or synthetic evidence pack. It searches only with `query_context`,
`query`, and `trace`; the runtime extracts the compact research context from
the returned filing results.

The default selected-company path does not call `krw_ontology_verify_evidence`
and does not create a `krw-verified-company-evidence/v1` pack. The runtime attaches the exact
`krw-guru-company-research-context/v1` it built from the subagent's actual
filing-tool results to the subsequent Guru review call.

Use trace or chain only for selected reviewed IDs when a stronger ontology
source is necessary. Treat every selected root as `(ticker, object_id)` and
forward both fields unchanged; never guess a company for a shared object ID.
Inspect chain `response_budget` before treating the returned paths as complete.
Trace and chain never replace filing evidence. Use Guru search only for
fallback discovery or debugging.

A historical comparison may appear in final prose only if the selected Guru
ResearchPack itself contains the documented episode or prior cycle. Phrase it
in third person (for example, “Marks가 과거 신용 사이클에서 반복해 경계한…”),
not as a personal recollection or a claim to be the real investor.

## Emergency Rollback Only

The standard workflow is enabled by default. Setting
`GURU_AGENT_GENERATED_BRIEF_ENABLED=0` is an operational rollback switch for a
production incident, not a model choice or a user-facing mode. In that
exceptional case, the pre-existing `dynamic_question_plan` path may run. The
two paths are mutually exclusive: never send a legacy plan and an
investigation brief in the same run.
