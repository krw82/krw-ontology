"""init-workspace CLI command implementation."""

from __future__ import annotations

from pathlib import Path

import typer


OBJECTS_YAML_TEMPLATE = """\
schema_version: "0.1.0"

types:
  SourceDocument:
    id_pattern: "source:{ticker}:{period}:{doc_type_key}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["SourceDocument"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: false}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      metadata_ref: {type: string, description: "path to metadata.json", required: false}
      schema_version: {type: string, required: true}

  SourceSpan:
    id_pattern: "span:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["SourceSpan"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      section_name: {type: string, required: true}
      section_number: {type: string, required: true}
      span_index: {type: integer, required: true}
      start_char: {type: integer, required: true, description: "Absolute offset within clean.md"}
      end_char: {type: integer, required: true, description: "Absolute offset within clean.md"}
      text: {type: string, required: true}
      text_hash: {type: string, required: true, description: "sha256 hash of normalized span text"}
      char_count: {type: integer, required: true}
      section_detection_confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      section_detection_method: {type: string, required: true}
      schema_version: {type: string, required: true}

  EvidenceQuote:
    id_pattern: "quote:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}:{quote_seq:03d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["EvidenceQuote"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      source_span_id: {type: string, required: true}
      quote_text: {type: string, required: true}
      quote_type: {type: string, enum_ref: "quote_types.yaml", required: true}
      section_name: {type: string, required: true}
      start_char: {type: integer, required: false, description: "Optional offset within SourceSpan.text"}
      end_char: {type: integer, required: false, description: "Optional offset within SourceSpan.text"}
      absolute_start_char: {type: integer, required: false, description: "Optional derived offset within clean.md"}
      absolute_end_char: {type: integer, required: false, description: "Optional derived offset within clean.md"}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      language_signals: {type: array, items: LanguageSignal, required: false, description: "Optional. Embedded during quote extraction."}
      schema_version: {type: string, required: true}

  LanguageSignal:
    id_pattern: "signal:{ticker}:{period}:{doc_type_key}:{section}:{seq:04d}:{signal_seq:03d}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["LanguageSignal"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      source_quote_id: {type: string, required: true}
      signal_text: {type: string, required: true}
      signal_type: {type: string, enum_ref: "language_signals.yaml", required: true}
      strength: {type: string, enum: ["strong", "medium", "weak"], required: true}
      direction: {type: string, enum: ["positive", "negative", "neutral", "risk"], required: true}
      certainty: {type: string, enum: ["observed", "conditional", "expected", "uncertain", "structural"], required: true}
      temporal_scope: {type: string, enum: ["historical", "current", "future_or_potential", "ongoing"], required: true}
      exact_match_verified: {type: boolean, required: true}
      schema_version: {type: string, required: true}

  ResearchClaim:
    id_pattern: "claim:{ticker}:{period}:{doc_type_key}:{slug}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["ResearchClaim"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      claim_text: {type: string, required: true}
      claim_type: {type: string, enum_ref: "claim_types.yaml", required: true}
      supported_by_quotes: {type: array, items: string, required: true, min_items: 1}
      related_metrics: {type: array, items: string, required: false}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}

  ResearchObject:
    description: "Shared schema for RiskFactor, GrowthDriver, Headwind. Type field discriminates."
    id_pattern: "{type_key}:{ticker}:{period}:{doc_type_key}:{slug}"
    type_key: "risk|growth_driver|headwind"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["RiskFactor", "GrowthDriver", "Headwind"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      name: {type: string, required: true}
      category: {type: string, required: true}
      description: {type: string, required: true}
      supported_by_claims: {type: array, items: string, required: false}
      supported_by_quotes: {type: array, items: string, required: false}
      affects: {type: array, items: string, required: false}
      unmapped_impacts: {type: array, items: string, required: false}
      unmapped_metrics: {type: array, items: string, required: false}
      qualitative_impact: {type: string, required: true}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}

  AssumptionCandidate:
    id_pattern: "assumption:{ticker}:{period}:{doc_type_key}:{slug}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["AssumptionCandidate"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      name: {type: string, required: true}
      assumption_text: {type: string, required: true}
      assumption_type: {type: string, enum: ["growth_rate", "margin", "capex", "tax_rate", "wacc", "other"], required: true}
      value_hint: {type: string, required: false}
      supported_by_claims: {type: array, items: string, required: false}
      supported_by_quotes: {type: array, items: string, required: false}
      related_metrics: {type: array, items: string, required: false}
      unmapped_metrics: {type: array, items: string, required: false}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "needs_review"}
      schema_version: {type: string, required: true}

  Metric:
    id_pattern: "metric:{canonical_name}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["Metric"], required: true}
      name: {type: string, required: true}
      category: {type: string, required: true}
      unit: {type: string, required: true}
      description: {type: string, required: true}
      schema_version: {type: string, required: true}

  XBRLFact:
    id_pattern: "xbrl:{ticker}:{period}:{doc_type_key}:{safe_taxonomy_tag}:{hash8}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["XBRLFact"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      taxonomy_tag: {type: string, required: true}
      safe_taxonomy_tag: {type: string, required: true}
      value: {type: number, required: true}
      unit: {type: string, required: true}
      context_ref: {type: string, required: true}
      source_filing_detail: {type: string, required: true}
      decimals: {type: integer, required: false}
      schema_version: {type: string, required: true}

  FinancialMetricValue:
    id_pattern: "financial_metric:{ticker}:{period}:{doc_type_key}:{hash12}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["FinancialMetricValue"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      metric_name: {type: string, required: true}
      value: {type: number, required: true}
      unit: {type: string, required: true}
      fiscal_year: {type: integer, required: false}
      fiscal_period: {type: string, required: false}
      period_type: {type: string, required: false}
      start_date: {type: string, required: false}
      end_date: {type: string, required: false}
      source_xbrl_fact_id: {type: string, required: true}
      source: {type: string, required: true}
      schema_version: {type: string, required: true}

  DerivedMetricValue:
    id_pattern: "derived_metric:{ticker}:{period}:{doc_type_key}:{hash12}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["DerivedMetricValue"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      metric_name: {type: string, required: true}
      value: {type: number, required: true}
      unit: {type: string, required: true}
      fiscal_year: {type: integer, required: false}
      fiscal_period: {type: string, required: false}
      period_type: {type: string, required: false}
      formula: {type: string, required: true}
      input_metric_ids: {type: array, items: string, required: true}
      source: {type: string, required: true}
      schema_version: {type: string, required: true}

  Edge:
    id_pattern: "edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}"
    fields:
      id: {type: string, required: true}
      type: {type: string, enum: ["Edge"], required: true}
      ticker: {type: string, required: true}
      source_document_id: {type: string, required: true}
      document_type: {type: string, required: true}
      period: {type: string, required: true}
      from_id: {type: string, required: true}
      to_id: {type: string, required: true}
      relation_name: {type: string, required: true}
      relation_id: {type: string, required: true}
      confidence: {type: string, enum: ["high", "medium", "low"], required: true}
      review_status: {type: string, enum: ["accepted", "needs_review", "rejected"], default: "accepted"}
      schema_version: {type: string, required: true}
"""

RELATIONS_YAML_TEMPLATE = """\
schema_version: "0.1.0"

relations:
  - id: contains
    name: contains
    from: SourceDocument
    to: SourceSpan

  - id: contains_quote
    name: contains_quote
    from: SourceSpan
    to: EvidenceQuote

  - id: has_signal
    name: has_signal
    from: EvidenceQuote
    to: LanguageSignal

  - id: supports
    name: supports
    from: EvidenceQuote
    to: ResearchClaim

  - id: describes_risk
    name: describes_risk
    from: ResearchClaim
    to: RiskFactor

  - id: describes_driver
    name: describes_driver
    from: ResearchClaim
    to: GrowthDriver

  - id: describes_headwind
    name: describes_headwind
    from: ResearchClaim
    to: Headwind

  - id: affects_risk
    name: affects
    from: RiskFactor
    to: Metric

  - id: affects_growth
    name: affects
    from: GrowthDriver
    to: Metric

  - id: affects_headwind
    name: affects
    from: Headwind
    to: Metric

  - id: supports_assumption
    name: supports_assumption
    from: EvidenceQuote
    to: AssumptionCandidate

  - id: derived_from
    name: derived_from
    from: AssumptionCandidate
    to: ResearchClaim
"""

QUOTE_TYPES_YAML_TEMPLATE = """\
quote_types:
  risk_language:
    description: "Language describing potential or actual downside risk."
    ai_guidance: "Look for words like: risk, may adversely, could negatively, potential loss, exposure, volatility, uncertainty."

  business_description:
    description: "Language describing how the company operates or makes money."
    ai_guidance: "Look for words like: we operate, our business model, revenue streams, customer base, geographic segments."

  revenue_driver:
    description: "Language explaining growth or decline in sales."
    ai_guidance: "Look for words like: grew, increased, revenue, sales, demand, expansion."

  margin_driver:
    description: "Language explaining margin, cost structure, or profitability."
    ai_guidance: "Look for words like: margin, profitability, cost structure, gross profit, operating leverage."

  headwind:
    description: "Language describing pressure on demand, revenue, margin, or operations."
    ai_guidance: "Look for words like: headwind, pressure, challenge, headwinds, weakening."

  growth_driver:
    description: "Language describing positive business momentum."
    ai_guidance: "Look for words like: opportunity, tailwinds, growth, momentum, expanding."

  competitive_pressure:
    description: "Language describing competition, substitution, pricing pressure, or market share risk."
    ai_guidance: "Look for words like: competitive, competition, market share, pricing pressure, substitute."

  regulatory_exposure:
    description: "Language describing regulation, litigation, compliance, or legal exposure."
    ai_guidance: "Look for words like: regulatory, litigation, compliance, legal, government, investigation."

  supply_chain:
    description: "Language describing suppliers, manufacturing, logistics, inventory, or production risk."
    ai_guidance: "Look for words like: supplier, supply chain, manufacturing, logistics, inventory, component."

  assumption_support:
    description: "Language that can support a valuation or forecast assumption."
    ai_guidance: "Look for words like: assume, expected to, we anticipate, forecast, guidance."
"""

LANGUAGE_SIGNALS_YAML_TEMPLATE = """\
signal_types:
  potential_negative:
    description: "Language indicating a possible future negative outcome."
    strength_values: ["strong", "medium", "weak"]

  actual_negative:
    description: "Language confirming a negative outcome has occurred."
    strength_values: ["strong", "medium", "weak"]

  potential_positive:
    description: "Language indicating a possible future positive outcome."
    strength_values: ["strong", "medium", "weak"]

  actual_positive:
    description: "Language confirming a positive outcome has occurred."
    strength_values: ["strong", "medium", "weak"]

  uncertainty:
    description: "Language indicating lack of certainty about an outcome."
    strength_values: ["strong", "medium", "weak"]

  obligation:
    description: "Language indicating a commitment or binding requirement."
    strength_values: ["strong", "medium", "weak"]

  mitigation:
    description: "Language describing steps taken to reduce risk."
    strength_values: ["strong", "medium", "weak"]
"""

CLAIM_TYPES_YAML_TEMPLATE = """\
claim_types:
  factual:
    description: "Verifiable factual statement from the filing."
    example: "Services revenue grew 14% year-over-year to $85.2 billion."

  forward_looking:
    description: "Management's expectation or projection about future performance."
    example: "We expect services revenue to continue growing at a double-digit rate."

  risk_assessment:
    description: "Statement characterizing a risk factor or its potential impact."
    example: "Intensifying competition in smartphones could materially reduce our market share."

  strategic:
    description: "Statement about strategic direction, priorities, or investments."
    example: "We are investing heavily in AI-enabled features across our products."

  assumption:
    description: "Implicit or explicit assumption underlying a projection."
    example: "We assume stable foreign exchange rates relative to the prior year."
"""

RISK_CATEGORIES_YAML_TEMPLATE = """\
risk_categories:
  macro_economic:
    description: "Macroeconomic factors affecting the business."
    examples: ["foreign_exchange", "interest_rate", "inflation", "recession"]

  competitive:
    description: "Competitive dynamics in the market."
    examples: ["market_share_pressure", "pricing_pressure", "new_entrants", "substitution_risk"]

  regulatory:
    description: "Government regulation, litigation, or legal risk."
    examples: ["antitrust", "privacy_regulation", "tax_changes", "trade_restrictions"]

  operational:
    description: "Risks in day-to-day operations."
    examples: ["supply_chain_disruption", "manufacturing_risk", "cybersecurity", "talent_retention"]

  technology:
    description: "Technology-related risks."
    examples: ["rapid_technological_change", "intellectual_property", "ai_risk"]

  financial:
    description: "Financial structure and performance risks."
    examples: ["debt_levels", "credit_risk", "customer_concentration", "acquisition_risk"]
"""

METRIC_DICTIONARY_YAML_TEMPLATE = """\
schema_version: "0.1.0"

canonical_metrics:
  # -- Revenue/growth (3) --
  revenue:
    display_name: Revenue
    category: revenue
    unit: USD
    description: "Total revenue from continuing operations."
    aliases: [total_revenue, net_sales, sales]
    xbrl_tags: [us-gaap:Revenues, us-gaap:SalesRevenueNet]

  revenue_growth:
    display_name: Revenue Growth
    category: revenue
    unit: percent
    description: "Year-over-year change in total revenue."
    aliases: [revenue_growth_rate, sales_growth, net_sales_growth]
    xbrl_tags: []

  segment_revenue:
    display_name: Segment Revenue
    category: revenue
    unit: USD
    description: "Revenue breakdown by business segment."
    aliases: [segment_sales]
    xbrl_tags: []

  # -- Margin/profitability (7) --
  gross_margin:
    display_name: Gross Margin
    category: profitability
    unit: percent
    description: "Gross profit divided by total revenue."
    aliases: [gross_profit_margin]
    xbrl_tags: []

  gross_profit:
    display_name: Gross Profit
    category: profitability
    unit: USD
    description: "Revenue minus cost of revenue."
    aliases: []
    xbrl_tags: [us-gaap:GrossProfit]

  operating_margin:
    display_name: Operating Margin
    category: profitability
    unit: percent
    description: "Operating income divided by total revenue."
    aliases: []
    xbrl_tags: []

  operating_income:
    display_name: Operating Income
    category: profitability
    unit: USD
    description: "Income from core business operations."
    aliases: []
    xbrl_tags: [us-gaap:OperatingIncomeLoss]

  net_income:
    display_name: Net Income
    category: profitability
    unit: USD
    description: "Bottom-line profit after all expenses and taxes."
    aliases: [net_profit]
    xbrl_tags: [us-gaap:NetIncomeLoss]

  net_margin:
    display_name: Net Margin
    category: profitability
    unit: percent
    description: "Net income divided by total revenue."
    aliases: []
    xbrl_tags: []

  eps:
    display_name: Earnings Per Share
    category: profitability
    unit: USD_per_share
    description: "Net income available to common shareholders divided by weighted average shares."
    aliases: [earnings_per_share]
    xbrl_tags: [us-gaap:EarningsPerShareBasic]

  # -- Costs (4) --
  cost_of_revenue:
    display_name: Cost of Revenue
    category: costs
    unit: USD
    description: "Direct costs attributable to the production of revenue."
    aliases: [cost_of_goods_sold, cogs]
    xbrl_tags: [us-gaap:CostOfGoodsAndServicesSold]

  operating_expense:
    display_name: Operating Expense
    category: costs
    unit: USD
    description: "Total operating expenses."
    aliases: [total_operating_expenses, sg_and_a]
    xbrl_tags: [us-gaap:OperatingExpenses]

  research_and_development:
    display_name: Research and Development
    category: costs
    unit: USD
    description: "R&D expenditure."
    aliases: [rd_expense, research_development_expense]
    xbrl_tags: [us-gaap:ResearchAndDevelopmentExpense]

  selling_general_and_admin:
    display_name: Selling, General and Administrative
    category: costs
    unit: USD
    description: "SG&A expenditure."
    aliases: [sga, selling_general_administrative]
    xbrl_tags: [us-gaap:SellingGeneralAndAdministrativeExpense]

  # -- Cash flow (4) --
  operating_cash_flow:
    display_name: Operating Cash Flow
    category: cash_flow
    unit: USD
    description: "Net cash from operating activities."
    aliases: [cash_from_operations]
    xbrl_tags: [us-gaap:NetCashProvidedByUsedInOperatingActivities]

  capital_expenditures:
    display_name: Capital Expenditures
    category: cash_flow
    unit: USD
    description: "Cash spent on property, plant and equipment."
    aliases: [capex, pp_and_e]
    xbrl_tags: [us-gaap:PaymentsForAcquisitionOfPropertyPlantAndEquipment]

  free_cash_flow:
    display_name: Free Cash Flow
    category: cash_flow
    unit: USD
    description: "Operating cash flow minus capital expenditures."
    aliases: [fcf]
    xbrl_tags: []

  fcf_margin:
    display_name: FCF Margin
    category: cash_flow
    unit: percent
    description: "Free cash flow divided by total revenue."
    aliases: [free_cash_flow_margin]
    xbrl_tags: []

  # -- Balance sheet/leverage (5) --
  cash_and_equivalents:
    display_name: Cash and Equivalents
    category: balance_sheet
    unit: USD
    description: "Cash, cash equivalents, and short-term investments."
    aliases: [cash, total_cash]
    xbrl_tags: [us-gaap:CashAndCashEquivalentsAtCarryingValue]

  total_assets:
    display_name: Total Assets
    category: balance_sheet
    unit: USD
    description: "Sum of all assets."
    aliases: []
    xbrl_tags: [us-gaap:Assets]

  total_liabilities:
    display_name: Total Liabilities
    category: balance_sheet
    unit: USD
    description: "Sum of all liabilities."
    aliases: []
    xbrl_tags: [us-gaap:Liabilities]

  total_debt:
    display_name: Total Debt
    category: balance_sheet
    unit: USD
    description: "Short-term debt plus current portion of long-term debt plus long-term debt."
    aliases: [debt]
    xbrl_tags: [us-gaap:ShortTermBorrowings, us-gaap:LongTermDebt]

  shareholders_equity:
    display_name: "Shareholders' Equity"
    category: balance_sheet
    unit: USD
    description: "Total owners' equity."
    aliases: [total_equity, stockholders_equity]
    xbrl_tags: [us-gaap:StockholdersEquity]

  # -- Efficiency/returns (2) --
  roe:
    display_name: Return on Equity
    category: efficiency
    unit: percent
    description: "Net income divided by average shareholders' equity."
    aliases: [return_on_equity]
    xbrl_tags: []

  roa:
    display_name: Return on Assets
    category: efficiency
    unit: percent
    description: "Net income divided by average total assets."
    aliases: [return_on_assets]
    xbrl_tags: []
"""

YAML_TEMPLATES = {
    "objects.yaml": OBJECTS_YAML_TEMPLATE,
    "relations.yaml": RELATIONS_YAML_TEMPLATE,
    "quote_types.yaml": QUOTE_TYPES_YAML_TEMPLATE,
    "language_signals.yaml": LANGUAGE_SIGNALS_YAML_TEMPLATE,
    "claim_types.yaml": CLAIM_TYPES_YAML_TEMPLATE,
    "risk_categories.yaml": RISK_CATEGORIES_YAML_TEMPLATE,
    "metric_dictionary.yaml": METRIC_DICTIONARY_YAML_TEMPLATE,
}


def init_workspace(schema_dir: Path | None = None) -> None:
    """Create ontology schema directory with starter YAML configs."""
    if schema_dir is None:
        schema_dir = Path("ontology/schema")

    schema_dir.mkdir(parents=True, exist_ok=True)

    for filename, template in YAML_TEMPLATES.items():
        filepath = schema_dir / filename
        filepath.write_text(template)

    typer.echo(f"Workspace initialized. Schema configs written to {schema_dir}/")
