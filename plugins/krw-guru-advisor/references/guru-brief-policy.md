# Internal Guru Consultation Brief Policy

Before calling `krw_guru_query_context`, convert the user's request into a concise English-first internal guru consultation brief.

The brief is private planning text. Do not expose it in the final Korean answer.

## Purpose

The brief makes user language retrieval-friendly without hard-coding guru principles. Guru source materials are English-first, so retrieval text sent to Guru MCP must be English-first even when the user asks in Korean.

It should:

```text
- preserve the user's original question and emotional/investment state
- translate the user's investment intent into an English internal investment brief
- preserve selected author_key
- map vague retail-investor wording to guru ontology intent families
- surface whether company evidence or portfolio context is needed
- include English retrieval terms as the primary search terms
- include Korean retrieval terms only as secondary context for preserving user wording
- keep the answer agent free to write naturally after the ResearchPack returns
```

It must not:

```text
- add guru principles from memory
- decide the answer before MCP returns
- invent facts, prices, company fundamentals, or portfolio data
- route to another guru
- call the KRW Ontology router skill
- send Korean-only retrieval text to Guru MCP
```

## Brief Shape

Use this internal shape before the first MCP call:

```text
Internal guru consultation brief:
- author_key:
- original_user_question:
- internal_investment_brief_en:
- primary_intent:
- secondary_intents:
- decision_stage:
- asset_or_company_context:
- user_state:
- requires_company_evidence:
- requires_portfolio_context:
- retrieval_terms_en:
- retrieval_terms_ko:
- answer_mode_hint:
```

This is not user-facing JSON. It is compact planning text for MCP retrieval. `internal_investment_brief_en` and `retrieval_terms_en` are mandatory. `retrieval_terms_ko` is optional and secondary.

## Intent Mapping

Use multi-intent mapping. Retail investor questions often contain more than one intent.

```text
pre_buy
considering_buy
holding_review
loss_review
sell_or_trim
add_to_position
position_sizing
portfolio_concentration
cash_allocation
business_quality_check
valuation_check
capital_allocation_check
cyclical_risk_check
commodity_macro_speculation
thesis_review
contrarian_check
learn_guru_view
```

Examples:

```text
"물렸는데 계속 들고 가도 돼?"
primary_intent=loss_review
secondary_intents=sell_or_trim, thesis_review, position_sizing
decision_stage=holding_review

"좋은 회사 같은데 너무 오른 것 같아. 지금 사도 돼?"
primary_intent=valuation_check
secondary_intents=considering_buy, business_quality_check
decision_stage=pre_buy

"원유 투자했는데 손실이 커"
primary_intent=loss_review
secondary_intents=commodity_macro_speculation, sell_or_trim, position_sizing
decision_stage=holding_review

"비중을 더 실어도 될까?"
primary_intent=position_sizing
secondary_intents=add_to_position, portfolio_concentration, risk_check
decision_stage=portfolio_review
```

## MCP Call

Call the MCP with the fixed selected author and an English-first brief-optimized question.

Recommended form:

```text
krw_guru_query_context(
  author_keys=["<fixed_author_key>"],
  question="English investment brief for Guru retrieval:\n<internal_investment_brief_en>\n\nRetrieval terms: <retrieval_terms_en>\n\nOriginal user question for context only: <original_user_question>\n\nKorean support terms: <retrieval_terms_ko>"
)
```

Do not expose this augmented query to the user. Do not put Korean text first in the MCP query unless the tool specifically asks for the original user wording.

## Stop Rules

After `krw_guru_query_context`:

```text
sufficient_lens:
answer from the ResearchPack without broad search

partial_lens:
answer narrowly and make confidence limits visible in natural language, without a source/disclaimer footer

needs_clarification:
ask the returned clarifying question before a strong consultation

needs_company_evidence:
use the guru lens, but do not finish a company-specific judgment without KRW Ontology filing evidence

ontology_gap:
do not fill from memory; state that the current guru ontology does not provide enough support
```

Use `krw_guru_trace` or `krw_guru_chain` only on selected reviewed_ids when stronger support is materially useful.

Use `krw_guru_search` only for fallback discovery or debugging.

Use `krw_guru_index_context` only for debug/capability checks, never normal answer flow.

## Company Bridge

If the ResearchPack says company evidence is required:

```text
1. Keep the selected guru lens visible.
2. Call krw_guru_company_brief and delegate the resulting company filing brief to the app-provided company_evidence_researcher subagent when the runtime supports it.
3. Keep guru interpretation separate from filing-supported company facts.
4. If filing evidence is not available in the current runtime, say what evidence would be needed instead of inventing it.
```

Do not invoke the existing KRW Ontology router skill automatically. Application runtime owns routing and the company_evidence_researcher subagent.

## Examples

User:

```text
버핏한테 묻고 싶어. 원유 투자했는데 손실이 커. 계속 들고 있어야 하나?
```

Internal brief:

```text
author_key=buffett
original_user_question=버핏한테 묻고 싶어. 원유 투자했는데 손실이 커. 계속 들고 있어야 하나?
internal_investment_brief_en=The user has a loss in a commodity-linked oil investment and wants to know whether continuing to hold is justified. Map the question to capital preservation, permanent capital loss, speculation versus business ownership, thesis break, position sizing, and sell discipline. Do not assume facts about a specific company because no ticker is named.
primary_intent=loss_review
secondary_intents=sell_or_trim, position_sizing, commodity_macro_speculation, thesis_review
decision_stage=holding_review
asset_or_company_context=commodity-linked investment, no named operating company
user_state=large unrealized loss, asks whether to continue holding
requires_company_evidence=false unless a named company/ticker appears
requires_portfolio_context=true
retrieval_terms_en=loss review, permanent capital loss, commodity exposure, speculation, thesis break, position sizing, sell discipline
retrieval_terms_ko=손실 점검, 영구 자본 손실, 원자재, 투기, 보유 논리 훼손, 비중, 매도 규율
answer_mode_hint=first-person simulated advisor voice; use ResearchPack materials only
```
