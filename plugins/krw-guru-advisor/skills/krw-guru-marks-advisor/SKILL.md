---
name: krw-guru-marks-advisor
description: Use only when application code or the user explicitly selected the Howard Marks guru lens. This is not a router and must not select another guru.
---

# KRW Guru Marks Advisor Skill

Fixed `author_key`: `marks`.

This skill is a thin adapter. It must not hard-code Howard Marks principles, persona, favorite questions, or investment rules. All lens and persona content must come from the Guru ResearchPack.

## Required Workflow

```text
1. Call krw_guru_query_context with author_keys=["marks"].
2. Read research_status, answerability, selected_lenses, persona_profile, company_bridge, and clarifying_questions.
3. Use krw_guru_trace or krw_guru_chain only for selected reviewed_ids when stronger support is needed.
4. Compose a Korean investor-facing answer from the ResearchPack.
```

If company evidence is required, state that the guru lens cannot finish the company-specific judgment without KRW Ontology filing evidence.

Read before answering:

```text
../../references/guru-skill-contract.md
../../references/research-pack-contract.md
../../references/mcp-tool-policy.md
../../references/company-bridge-policy.md
../../references/output-contract.md
```

