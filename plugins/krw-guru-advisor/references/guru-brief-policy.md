# Internal Guru Consultation Brief Policy

Before calling `krw_guru_query_context`, convert the user's request into a concise internal guru consultation brief.

The brief is private planning text. Do not expose it in the final Korean answer.

## Purpose

The brief makes user language retrieval-friendly without hard-coding guru principles.

It should:

```text
- preserve the user's original question and emotional/investment state
- preserve selected author_key
- map vague retail-investor wording to guru ontology intent families
- surface whether company evidence or portfolio context is needed
- include Korean and English retrieval terms when useful
- keep the answer agent free to write naturally after the ResearchPack returns
```

It must not:

```text
- add guru principles from memory
- decide the answer before MCP returns
- invent facts, prices, company fundamentals, or portfolio data
- route to another guru
- call the KRW Ontology router skill
```

## Brief Shape

Use this internal shape before the first MCP call:

```text
Internal guru consultation brief:
- author_key:
- original_user_question:
- primary_intent:
- secondary_intents:
- decision_stage:
- asset_or_company_context:
- user_state:
- requires_company_evidence:
- requires_portfolio_context:
- retrieval_terms_ko:
- retrieval_terms_en:
- answer_mode_hint:
```

This is not user-facing JSON. It is compact planning text for MCP retrieval.

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

Call the MCP with the fixed selected author and the brief-optimized question.

Recommended form:

```text
krw_guru_query_context(
  author_keys=["<fixed_author_key>"],
  question="<original user question>\n\nInternal guru consultation brief:\n..."
)
```

Do not expose this augmented query to the user.

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
2. Pass `research_pack.company_bridge.filing_evidence_requirements` to the existing KRW Ontology filing research path when the runtime supports it.
3. Keep guru interpretation separate from filing-supported company facts.
4. If filing evidence is not available in the current runtime, say what evidence would be needed instead of inventing it.
```

Do not invoke the existing KRW Ontology router skill automatically. Application code owns routing.

## Examples

User:

```text
버핏한테 묻고 싶어. 원유 투자했는데 손실이 커. 계속 들고 있어야 하나?
```

Internal brief:

```text
author_key=buffett
original_user_question=버핏한테 묻고 싶어. 원유 투자했는데 손실이 커. 계속 들고 있어야 하나?
primary_intent=loss_review
secondary_intents=sell_or_trim, position_sizing, commodity_macro_speculation, thesis_review
decision_stage=holding_review
asset_or_company_context=commodity-linked investment, no named operating company
user_state=large unrealized loss, asks whether to continue holding
requires_company_evidence=false unless a named company/ticker appears
requires_portfolio_context=true
retrieval_terms_ko=손실 점검, 영구 자본 손실, 원자재, 투기, 보유 논리 훼손, 비중, 매도 규율
retrieval_terms_en=loss review, permanent capital loss, commodity exposure, speculation, thesis break, position sizing, sell discipline
answer_mode_hint=first-person simulated advisor voice; use ResearchPack materials only
```
