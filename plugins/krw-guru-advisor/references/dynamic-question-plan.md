# Legacy Dynamic Question Plan

`dynamic_question_plan` is the rollback-only company bridge used only when an
operator explicitly sets `GURU_AGENT_GENERATED_BRIEF_ENABLED=0` during an
incident.

The active path is documented in `investigation-brief.md`: the main Guru agent
uses returned ontology philosophy and neutral company context to draft one
company-specific key question, then `krw_guru_company_brief` validates and
seals it as `investigation_brief`.

Do not use this legacy plan in the default Guru run. Do not mix a legacy plan
and an investigation brief in the same company evidence request.
