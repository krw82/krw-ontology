"""Tests for numeric guard validation."""

from __future__ import annotations

from krw_ontology.validators.numeric_guard import validate_numeric


class TestNumericGuard:
    def _make_quote(self, quote_id: str, text: str) -> dict:
        return {"id": quote_id, "type": "EvidenceQuote", "quote_text": text}

    def test_number_in_quote_passes(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue grew 14% year-over-year.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Revenue grew 14% year-over-year.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected pass, got: {reason}"

    def test_number_in_xbrl_passes(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue was 85200000000.",
            "supported_by_quotes": [],
        }
        xbrl = {"xbrl:1": {"value": 85200000000}}
        ok, reason = validate_numeric(obj, {}, xbrl)
        assert ok, f"Expected pass, got: {reason}"

    def test_number_nowhere_fails(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue was $99.9 billion.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Revenue increased year-over-year.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert not ok

    def test_no_numbers_passes(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "The company faces competitive pressure.",
            "supported_by_quotes": [],
        }
        ok, reason = validate_numeric(obj, {}, {})
        assert ok

    def test_xbrl_unavailable_quote_has_number(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Gross margin was 46.2%.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Gross margin was 46.2%.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected pass, got: {reason}"

    def test_risk_factor_description(self):
        obj = {
            "type": "RiskFactor",
            "description": "Affects approximately 60% of revenue.",
            "qualitative_impact": "Significant",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Affects approximately 60% of revenue.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected pass, got: {reason}"

    def test_non_numeric_type_passes(self):
        obj = {"type": "EvidenceQuote", "quote_text": "No numbers here."}
        ok, reason = validate_numeric(obj, {}, {})
        assert ok

    def test_assumption_candidate_value_hint(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": "We assume stable growth.",
            "value_hint": "5.2%",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Growth of 5.2% expected.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected pass, got: {reason}"

    def test_claim_to_quote_support_path(self):
        """RiskFactor -> supported_by_claims -> claim -> supported_by_quotes -> quote with number."""
        claim = {
            "id": "claim:1",
            "type": "ResearchClaim",
            "claim_text": "Revenue grew 14%.",
            "supported_by_quotes": ["quote:1"],
        }
        quote = self._make_quote("quote:1", "Revenue grew 14% year-over-year.")
        risk = {
            "type": "RiskFactor",
            "description": "Revenue growth of 14% may not continue.",
            "qualitative_impact": "Significant",
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": [],
        }
        all_lookup = {"claim:1": claim, "quote:1": quote}
        ok, reason = validate_numeric(risk, all_lookup, {})
        assert ok, f"Expected pass via claim->quote path, got: {reason}"

    def test_claim_to_quote_missing_number_fails(self):
        """RiskFactor number not in any quote reachable via claims."""
        claim = {
            "id": "claim:1",
            "type": "ResearchClaim",
            "claim_text": "Some claim.",
            "supported_by_quotes": ["quote:1"],
        }
        quote = self._make_quote("quote:1", "No numbers here.")
        risk = {
            "type": "RiskFactor",
            "description": "Revenue was $50 billion.",
            "qualitative_impact": "High",
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": [],
        }
        all_lookup = {"claim:1": claim, "quote:1": quote}
        ok, reason = validate_numeric(risk, all_lookup, {})
        assert not ok

    def test_money_suffix_m_matches_million_wording(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Services net sales were $109,158M.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Services net sales were $109,158 million.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected $M suffix to match million support, got: {reason}"

    def test_currency_symbols_are_financial_amounts(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "The company was fined €500 million.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "The fine was 500 million euros.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected euro amount to be supported, got: {reason}"

    def test_non_financial_context_numbers_are_ignored(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The cybersecurity leader has over 25 years of experience; "
                "Apple appealed an Article 5(4) decision; fiscal years span 52 or 53 weeks."
            ),
            "supported_by_quotes": [],
        }
        ok, reason = validate_numeric(obj, {}, {})
        assert ok, f"Expected non-financial context numbers to be ignored, got: {reason}"

    def test_company_neutral_product_and_version_numbers_are_ignored(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The company discussed Windows 11 adoption, RTX 5090 demand, "
                "Model 3 production, M4 performance, and version 2 migration."
            ),
            "supported_by_quotes": [],
        }
        ok, reason = validate_numeric(obj, {}, {})
        assert ok, f"Expected product/version identifiers to be ignored, got: {reason}"

    def test_financial_numbers_near_company_neutral_words_are_still_checked(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue from Windows products increased 11%.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Revenue from Windows products increased.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert not ok

    def test_table_bare_number_can_support_amount_in_millions(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Total net sales were $416,161M.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "| Total net sales | | | $ | 416,161 | | |",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected table number to support $M amount, got: {reason}"

    def test_table_bare_number_can_support_percent_cell(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Services net sales grew 14%.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "| Services | | | 109,158 | | | 14 | | % |",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected table percent cell to support 14%, got: {reason}"

    def test_xbrl_metric_value_supports_amount_in_millions(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Apple reported $416,161 million in net sales.",
            "supported_by_quotes": [],
        }
        facts = {
            "financial_metric:AAPL:FY2025:10K:revenue": {
                "type": "FinancialMetricValue",
                "metric_name": "revenue",
                "value": 416_161_000_000,
                "unit": "USD",
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected financial metric value to support amount, got: {reason}"

    def test_derived_metric_value_supports_calculated_percent(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue grew 6.4% year-over-year.",
            "supported_by_quotes": [],
        }
        facts = {
            "derived_metric:AAPL:FY2025:10K:revenue_growth": {
                "type": "DerivedMetricValue",
                "metric_name": "revenue_growth",
                "value": 6.42,
                "unit": "percent",
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected derived metric value to support calculated percent, got: {reason}"

    def test_financial_metric_values_support_rounded_yoy_growth(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "R&D expenses increased 10% in 2025 compared to 2024.",
            "supported_by_quotes": [],
        }
        facts = {
            "financial_metric:AAPL:FY2025:10K:rd_2025": {
                "type": "FinancialMetricValue",
                "metric_name": "research_and_development",
                "value": 34_550_000_000,
                "unit": "USD",
                "fiscal_year": 2025,
            },
            "financial_metric:AAPL:FY2025:10K:rd_2024": {
                "type": "FinancialMetricValue",
                "metric_name": "research_and_development",
                "value": 31_370_000_000,
                "unit": "USD",
                "fiscal_year": 2024,
            },
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected rounded code-calculated YoY growth to support 10%, got: {reason}"

    def test_financial_metric_values_support_rounded_yoy_decrease(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Operating income decreased 6% year-over-year.",
            "supported_by_quotes": [],
        }
        facts = {
            "financial_metric:AAPL:FY2025:10K:op_2025": {
                "type": "FinancialMetricValue",
                "metric_name": "operating_income",
                "value": 94_000_000_000,
                "unit": "USD",
                "fiscal_year": 2025,
            },
            "financial_metric:AAPL:FY2025:10K:op_2024": {
                "type": "FinancialMetricValue",
                "metric_name": "operating_income",
                "value": 100_000_000_000,
                "unit": "USD",
                "fiscal_year": 2024,
            },
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected absolute code-calculated YoY decrease to support 6%, got: {reason}"

    def test_financial_metric_values_do_not_support_unrelated_percent(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "R&D expenses increased 15% in 2025 compared to 2024.",
            "supported_by_quotes": [],
        }
        facts = {
            "financial_metric:AAPL:FY2025:10K:rd_2025": {
                "type": "FinancialMetricValue",
                "metric_name": "research_and_development",
                "value": 34_550_000_000,
                "unit": "USD",
                "fiscal_year": 2025,
            },
            "financial_metric:AAPL:FY2025:10K:rd_2024": {
                "type": "FinancialMetricValue",
                "metric_name": "research_and_development",
                "value": 31_370_000_000,
                "unit": "USD",
                "fiscal_year": 2024,
            },
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert not ok

    def test_billion_claim_matches_million_quote_with_rounding(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Americas net sales reached $178.35 billion in 2025.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Americas net sales were $178,353 million in 2025.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected $178.35B to match $178,353M support, got: {reason}"

    def test_billion_claim_matches_raw_xbrl_fact_with_rounding(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Services net sales reached $109.16 billion in 2025.",
            "supported_by_quotes": [],
        }
        facts = {
            "xbrl:AAPL:FY2025:10K:ServicesRevenue:1": {
                "type": "XBRLFact",
                "value": 109_158_000_000,
                "unit": "usd",
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected rounded billion claim to match raw XBRL fact, got: {reason}"

    def test_billion_rounding_tolerance_does_not_mask_wrong_amount(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Americas net sales reached $178.35 billion in 2025.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Americas net sales were $178,500 million in 2025.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert not ok

    def test_operating_system_version_number_is_ignored(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The company launched a new product lineup and iOS 26 operating system "
                "during fiscal 2025."
            ),
            "supported_by_quotes": [],
        }
        ok, reason = validate_numeric(obj, {}, {})
        assert ok, f"Expected operating system version number to be ignored, got: {reason}"

    def test_quote_amounts_support_rounded_yoy_percent(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Capital expenditures increased 35% to $12.7 billion from $9.4 billion.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Payments for acquisition of property, plant and equipment were 12,715 and 9,447.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected source table amounts to support rounded YoY percent, got: {reason}"

    def test_quote_amounts_support_rounded_ratio_percent(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "$55.4 billion represented over 98% of $56.2 billion in obligations.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Manufacturing purchase obligations were $56.2 billion, with $55.4 billion payable within 12 months.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected source amounts to support rounded ratio percent, got: {reason}"

    def test_quote_amounts_support_difference_amount(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Total liabilities decreased by approximately $22.5 billion.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Total liabilities were 285,508 and 308,030.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected source amounts to support rounded difference amount, got: {reason}"

    def test_k_suffix_amounts_are_not_partially_parsed(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Outstanding shares decreased by approximately 343.5 million shares.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Shares outstanding were 15,116,786K and 14,773,260K.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected K-suffix table values to support rounded share reduction, got: {reason}"

    def test_duration_numbers_are_ignored(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "$8.8 billion was payable within 12 months.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "$8.8 billion was payable within 12 months.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected duration numbers to be ignored, got: {reason}"

    def test_xbrl_segment_facts_support_rounded_yoy_percent(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Greater China net sales decreased 4% year-over-year.",
            "supported_by_quotes": [],
        }
        facts = {
            "xbrl:2025": {
                "type": "XBRLFact",
                "safe_taxonomy_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "value": 64_377_000_000,
                "unit": "usd",
                "context": {
                    "fiscal_year": 2025,
                    "period_type": "duration",
                    "dimensions": ["aapl:GreaterChinaSegmentMember"],
                },
            },
            "xbrl:2024": {
                "type": "XBRLFact",
                "safe_taxonomy_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
                "value": 66_952_000_000,
                "unit": "usd",
                "context": {
                    "fiscal_year": 2024,
                    "period_type": "duration",
                    "dimensions": ["aapl:GreaterChinaSegmentMember"],
                },
            },
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected matching XBRL segment facts to support rounded YoY percent, got: {reason}"

    def test_calculated_percent_still_fails_without_source_numbers(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue increased 35% year-over-year.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {"quote:1": self._make_quote("quote:1", "Revenue increased year-over-year.")}
        ok, reason = validate_numeric(obj, quotes, {})
        assert not ok

    def test_calculated_numeric_support_can_support_amount(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Term debt decreased by $5.984 billion.",
            "supported_by_quotes": [],
        }
        facts = {
            "calculated_numeric:AAPL:FY2025:10K:debt-diff": {
                "type": "CalculatedNumericSupport",
                "value": 5_984_000_000,
                "unit": "USD",
                "calculation_type": "difference",
                "formula": "96662000000 - 90678000000",
                "input_object_ids": [],
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected CalculatedNumericSupport to support amount, got: {reason}"

    def test_numeric_evidence_metric_row_supports_amount(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue was $416.16 billion.",
            "supported_by_quotes": [],
        }
        facts = {
            "numeric_evidence:AAPL:FY2025:10K:revenue": {
                "id": "numeric_evidence:AAPL:FY2025:10K:revenue",
                "type": "NumericEvidence",
                "numeric_kind": "amount",
                "source_method": "financial_metric_value",
                "value": 416_161_000_000,
                "raw_text": "416161000000",
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert ok, f"Expected NumericEvidence metric row to support amount, got: {reason}"

    def test_quote_sourced_numeric_evidence_requires_support_path(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue was $416.16 billion.",
            "supported_by_quotes": [],
        }
        facts = {
            "numeric_evidence:AAPL:FY2025:10K:quote-row": {
                "id": "numeric_evidence:AAPL:FY2025:10K:quote-row",
                "type": "NumericEvidence",
                "numeric_kind": "amount",
                "source_method": "quote_text",
                "source_quote_id": "quote:1",
                "value": 416_161_000_000,
                "raw_text": "$416,161 million",
            }
        }
        ok, reason = validate_numeric(obj, {}, facts)
        assert not ok

    def test_supported_quote_numeric_evidence_is_used_before_reparsing(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "Revenue was $416.16 billion.",
            "supported_by_quotes": ["quote:1"],
        }
        all_objects = {
            "quote:1": self._make_quote("quote:1", "Revenue was reported in the table."),
            "numeric_evidence:AAPL:FY2025:10K:quote-row": {
                "id": "numeric_evidence:AAPL:FY2025:10K:quote-row",
                "type": "NumericEvidence",
                "numeric_kind": "amount",
                "source_method": "quote_text",
                "source_quote_id": "quote:1",
                "value": 416_161_000_000,
                "raw_text": "$416,161 million",
            },
        }
        ok, reason = validate_numeric(obj, all_objects, {})
        assert ok, f"Expected supported quote NumericEvidence to support amount, got: {reason}"

    def test_accounting_parentheses_table_cell_supports_positive_million_claim(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The Impact of the State Aid Decision reduced Apple's provision "
                "for income taxes by $486 million in 2025."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "A reconciliation of the provision for income taxes is as follows "
                    "(dollars in millions):\n"
                    "|  | 2025 | 2024 | 2023 |\n"
                    "| Impact of the State Aid Decision | (486) | 10,246 | — |"
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected accounting table (486) to support $486M, got: {reason}"

    def test_accounting_parentheses_support_federal_deferred_tax_benefits(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "Federal deferred tax was a benefit of $1,804 million in 2025 "
                "compared to $3,080 million in 2024 and $3,644 million in 2023."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The provision for income taxes consisted of the following "
                    "(in millions):\n"
                    "| Federal | 2025 | 2024 | 2023 |\n"
                    "| Current | $ | 11,487 | $ | 5,571 | $ | 9,445 |\n"
                    "| Deferred | (1,804) | (3,080) | (3,644) |"
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected deferred tax table benefits to support claim, got: {reason}"

    def test_accounting_currency_parentheses_in_claim_and_quote_match(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "Apple's total cost of sales was $(220,960) million in FY2025, "
                "up from $(210,352) million in FY2024."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The following tables show information by reportable segment "
                    "for 2025 and 2024 (in millions):\n"
                    "|  | 2025 | 2024 |\n"
                    "| Cost of sales | (220,960) | (210,352) |"
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected accounting cost-of-sales amounts to match, got: {reason}"

    def test_month_day_in_value_hint_is_ignored(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "Apple's total liabilities decreased from $308,030M to $285,508M."
            ),
            "value_hint": "$285,508 million total liabilities (Sept 27, 2025)",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "As of September 27, 2025 and September 28, 2024 "
                    "(in millions): total liabilities were 285,508 and 308,030."
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected date day in value_hint to be ignored, got: {reason}"

    def test_country_count_is_ignored_but_employee_counts_still_checked(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "As of the end of fiscal year 2026, the company had approximately "
                "42,000 employees in 38 countries, with 31,000 engaged in research "
                "and development and 11,000 in sales and administrative positions."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The company had approximately 42,000 employees in 38 countries; "
                    "31,000 were engaged in research and development and 11,000 were "
                    "engaged in sales and administrative positions."
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected country count to be ignored and employee counts supported, got: {reason}"

    def test_regulatory_code_suffix_numbers_are_ignored(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "Updated licensing requirements for Country Groups D:1, D:4, and D:5 "
                "could constrain exports of letter-prefixed accelerator models."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Exports to Country Groups D:1, D:4, and D:5 require updated licensing.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected regulatory group code numbers to be ignored, got: {reason}"

    def test_lease_term_range_numbers_are_ignored(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "The company expects to commence leases with future obligations of "
                "$22.7 billion, with lease terms of 1.8 to 20 years."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The company expects to commence leases with future obligations of "
                    "$22.7 billion, with lease terms of 1.8 to 20 years."
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected lease term duration range to be ignored, got: {reason}"

    def test_parenthesized_year_before_word_start_is_not_million_amount(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "The grant-date fair value increased from $150.87 (2023) to "
                "$173.78 (2024) to $226.68 (2025) may signal future expense growth."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The weighted-average grant-date fair value was $226.68 in 2025, "
                    "$173.78 in 2024, and $150.87 in 2023."
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected parenthesized years not to parse as amount suffixes, got: {reason}"

    def test_table_currency_cell_uses_billion_context(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The company purchased 5.6 million shares at an average price of "
                "$198.89 per share, with approximately $61.1 billion remaining "
                "under the repurchase program."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "| Period | Shares Purchased (In millions) | Average Price Paid per Share | "
                    "Approximate Dollar Value Remaining Under the Program (In billions) |\n"
                    "| October 27 to November 23, 2025 | 5.6 | $198.89 | $61.1 |"
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected table billion context to support $61.1B, got: {reason}"

    def test_table_subtotal_can_be_supported_by_source_components(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "Cash paid for income taxes included $2,090 million to state "
                "jurisdictions and $1,443 million to foreign jurisdictions."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                (
                    "The amount of cash paid for income taxes is as follows (In millions):\n"
                    "| Federal | $ | 16,755 |\n"
                    "| California | 1,049 |\n"
                    "| Other state | 1,041 |\n"
                    "| Israel | 1,287 |\n"
                    "| Other foreign | 156 |\n"
                    "| Total income taxes paid | $ | 20,288 |"
                ),
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected source components to support tax subtotals, got: {reason}"

    def test_supported_claim_text_can_support_modeling_cue_numbers(self):
        claim = {
            "id": "claim:1",
            "type": "ResearchClaim",
            "claim_text": (
                "The effective tax rate increased from 13.3% to 15.1% while "
                "remaining below the 21.0% statutory rate."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quote = self._make_quote(
            "quote:1",
            "The effective tax rates were 15.1% and 13.3%, below the 21.0% statutory rate.",
        )
        cue = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "The effective tax rate increased from 13.3% to 15.1%, suggesting "
                "possible scaling toward the 21.0% statutory rate."
            ),
            "supported_by_claims": ["claim:1"],
            "supported_by_quotes": [],
        }
        ok, reason = validate_numeric(cue, {"claim:1": claim, "quote:1": quote}, {})
        assert ok, f"Expected accepted claim text to support modeling cue numbers, got: {reason}"

    def test_large_share_count_can_match_table_count_in_thousands(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": "The company repurchased a total of 89,498,000 shares.",
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "| Total shares purchased | 89,498 |",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected large share count to match table count in thousands, got: {reason}"

    def test_parenthesized_list_markers_are_not_numeric_requirements(self):
        obj = {
            "type": "ResearchClaim",
            "claim_text": (
                "The company identified two obligations: (1) hardware delivered "
                "at sale and (2) bundled services delivered over time."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "The company identified hardware and bundled services obligations.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected prose list markers to be ignored, got: {reason}"

    def test_plus_year_range_is_not_numeric_requirement(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "Debt maturities include obligations due in one to five years, "
                "five to ten years, and 10+ years."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "Debt maturities are grouped by maturity bucket.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected maturity range labels to be ignored, got: {reason}"

    def test_bare_employee_count_is_not_financial_numeric_requirement(self):
        obj = {
            "type": "AssumptionCandidate",
            "assumption_text": (
                "The company has approximately 6,000 employees in the region, "
                "which could affect operations."
            ),
            "supported_by_quotes": ["quote:1"],
        }
        quotes = {
            "quote:1": self._make_quote(
                "quote:1",
                "The company has a large employee base in the region.",
            )
        }
        ok, reason = validate_numeric(obj, quotes, {})
        assert ok, f"Expected employee count to be ignored by numeric guard, got: {reason}"
