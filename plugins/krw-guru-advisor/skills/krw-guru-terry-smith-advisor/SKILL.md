---
name: krw-guru-terry-smith-advisor
description: Use only when application code or the user explicitly selected the Terry Smith guru lens. This is not a router and must not select another guru.
---

# KRW Guru Terry Smith Advisor Skill

Fixed `author_key`: `terry_smith`.

This skill is a thin adapter. It must not hard-code Terry Smith principles, persona, favorite questions, or investment rules. All lens and persona content must come from the Guru ResearchPack.

## Required Workflow

```text
1. Build a private internal guru consultation brief from the user's question.
2. Call krw_guru_query_context with author_keys=["terry_smith"] and the brief-optimized question.
3. Read research_status, answerability, selected_lenses, persona_profile, company_bridge, and clarifying_questions.
4. Use krw_guru_trace or krw_guru_chain only for selected reviewed_ids when stronger support is needed.
5. Apply `references/answer-style.md` for voice, texture, and analogy rendering only.
6. Compose a Korean investor-facing answer from the ResearchPack.
```

If company evidence is required, state that the guru lens cannot finish the company-specific judgment without KRW Ontology filing evidence.

Read before answering:

```text
../../references/guru-skill-contract.md
../../references/guru-brief-policy.md
../../references/research-pack-contract.md
../../references/mcp-tool-policy.md
../../references/company-bridge-policy.md
../../references/answer-style-adapters.md
references/answer-style.md
../../references/output-contract.md
```
