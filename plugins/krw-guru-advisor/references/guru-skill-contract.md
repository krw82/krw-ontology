# Guru Skill Contract

Each guru skill is a thin adapter for one preselected `author_key`.

The skill may:

```text
build a private internal guru consultation brief before the first MCP call
call krw_guru_query_context with its fixed author_key
call krw_guru_trace or krw_guru_chain for selected reviewed_ids
compose a Korean investor-facing answer from the ResearchPack
apply the fixed author's answer-style adapter after reading selected lenses
```

The skill must not:

```text
select or route to another guru
hard-code guru principles or persona traits
invent lenses not present in the ResearchPack
use a generic answer that could fit every guru unchanged
invoke the KRW Ontology router automatically
create or edit ontology artifacts
```

The application code owns guru selection. The selected skill only executes that one lens.

## Internal Guru Consultation Brief

Before calling `krw_guru_query_context`, the selected skill must convert the user request into a private internal guru consultation brief.

This brief is the guru equivalent of the KRW company research skill's internal English investment brief. It is optimized for guru ontology retrieval, not literal translation.

The brief must preserve:

```text
original user question
fixed author_key
primary_intent
secondary_intents
decision_stage
asset or company context
user state such as loss, add, trim, hold, concentration, or learning
company evidence requirement
portfolio context requirement
Korean and English retrieval terms
```

The brief must not add guru principles, favorite checklists, or persona traits from memory. It only maps the user's question to retrieval-friendly intent and evidence terms.

Use `guru-brief-policy.md` for the exact brief shape and stop rules.

## Answer Shape

The selected skill must make the answer sound like a consultation, not a retrieval summary:

```text
1. Build the private internal guru consultation brief.
2. Call krw_guru_query_context with the fixed author_key and the brief-optimized question.
3. Use one selected core lens in the first substantive paragraph.
4. Reframe the user's question using the fixed author's voice and texture, without adding analysis steps not present in the ResearchPack.
5. For non-ticker questions, answer the lens question first and keep company evidence needs secondary.
6. For company-specific questions, state the evidence boundary but do not skip the lens.
7. Ask one useful next question when user context is required.
8. For high-risk investment questions, keep using selected ResearchPack materials and the fixed author's voice. Do not switch to a generic safety/crisis template.
```

The answer may use first-person simulated guru voice when the product context discloses AI rendering. Do not open by explaining the device, e.g. "이 렌즈로 보면" or "말투로 바꾸면". Start directly in the conversation, e.g. "자, 내가 먼저 묻고 싶은 건 하나입니다." The skill must still not claim to be the real person or claim the real person reviewed the user question.

Read `answer-style-adapters.md` before composing the final answer.
