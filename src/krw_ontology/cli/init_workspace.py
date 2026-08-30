"""init-workspace CLI command implementation."""

from __future__ import annotations

from pathlib import Path

import typer

from krw_ontology.registry import registry_path


RELATIONS_YAML_TEMPLATE = """\
schema_version: "0.1.0"

relations:
  - id: contains
    name: contains
    from: SourceDocument
    to: SourceSpan
    edge_class: source_structure
    evidence_level: direct

  - id: contains_quote
    name: contains_quote
    from: SourceSpan
    to: EvidenceQuote
    edge_class: evidence
    evidence_level: direct

  - id: has_signal
    name: has_signal
    from: EvidenceQuote
    to: LanguageSignal
    edge_class: evidence
    evidence_level: derived

  - id: supports
    name: supports
    from: EvidenceQuote
    to: ResearchClaim
    edge_class: evidence
    evidence_level: direct

  - id: describes_activity
    name: describes_activity
    from: ResearchClaim
    to: BusinessActivity
    edge_class: interpretation
    evidence_level: derived

  - id: describes_exposure
    name: describes_exposure
    from: ResearchClaim
    to: ExternalFactorExposure
    edge_class: interpretation
    evidence_level: derived

  - id: manifests_as
    name: manifests_as
    from: ExternalFactorExposure
    to: BusinessFactor
    edge_class: exposure_link
    evidence_level: derived

  - id: supports_assumption
    name: supports_assumption
    from: EvidenceQuote
    to: AssumptionCandidate
    edge_class: evidence
    evidence_level: direct

  - id: derived_from
    name: derived_from
    from: AssumptionCandidate
    to: ResearchClaim
    edge_class: interpretation
    evidence_level: derived

  - id: continues_as
    name: continues_as
    from:
      - BusinessActivity
      - ExternalFactorExposure
      - BusinessFactor
    to:
      - BusinessActivity
      - ExternalFactorExposure
      - BusinessFactor
    same_type_required: true
    edge_class: temporal
    evidence_level: inferred

  - id: supports_change_event
    name: supports_change_event
    from: ResearchClaim
    to: ChangeEvent
    edge_class: event
    evidence_level: direct

  - id: affects_object
    name: affects_object
    from: ChangeEvent
    to:
      - BusinessActivity
      - ExternalFactorExposure
      - BusinessFactor
    edge_class: event
    evidence_level: derived

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
    "relations.yaml": RELATIONS_YAML_TEMPLATE,
    "quote_types.yaml": QUOTE_TYPES_YAML_TEMPLATE,
    "language_signals.yaml": LANGUAGE_SIGNALS_YAML_TEMPLATE,
    "claim_types.yaml": CLAIM_TYPES_YAML_TEMPLATE,
    "risk_categories.yaml": RISK_CATEGORIES_YAML_TEMPLATE,
    "metric_dictionary.yaml": METRIC_DICTIONARY_YAML_TEMPLATE,
}


def init_workspace(schema_dir: Path | None = None) -> None:
    """Create ontology schema directory with starter YAML configs.

    registry.yaml is the single artifact-file authority: the workspace gets a
    copy of the project registry instead of a duplicated object-schema file.
    """
    if schema_dir is None:
        schema_dir = Path("ontology/schema")

    schema_dir.mkdir(parents=True, exist_ok=True)
    source_schema_dir = Path(__file__).resolve().parents[3] / "ontology" / "schema"
    source_taxonomy_dir = Path(__file__).resolve().parents[3] / "ontology" / "taxonomy"

    for filename, template in YAML_TEMPLATES.items():
        filepath = schema_dir / filename
        source_path = source_schema_dir / filename
        if source_path.exists():
            filepath.write_text(source_path.read_text())
        else:
            filepath.write_text(template)

    source_registry = registry_path()
    if source_registry.exists():
        (schema_dir.parent / "registry.yaml").write_text(source_registry.read_text())

    if source_taxonomy_dir.exists():
        taxonomy_dir = schema_dir.parent / "taxonomy"
        taxonomy_dir.mkdir(parents=True, exist_ok=True)
        for source_path in sorted(source_taxonomy_dir.glob("*.yaml")):
            (taxonomy_dir / source_path.name).write_text(source_path.read_text())

    typer.echo(f"Workspace initialized. Schema configs written to {schema_dir}/")
