from krw_ontology.validators.numeric_guard import validate_numeric


def _claim(text: str) -> dict:
    return {
        "id": "claim:1",
        "type": "ResearchClaim",
        "claim_text": text,
        "supported_by_quotes": ["quote:1"],
    }


def _quote(text: str) -> dict:
    return {"id": "quote:1", "type": "EvidenceQuote", "quote_text": text}


def test_ordinal_and_multiple_are_not_partially_parsed() -> None:
    ok, reason = validate_numeric(
        _claim("The product ranked 220th and traded at 8.27x EBITDA."),
        {},
        {},
    )

    assert ok is True
    assert reason is None


def test_mismatched_currency_amount_is_not_hidden_by_large_tolerance() -> None:
    ok, reason = validate_numeric(
        _claim("Revenue was $1.25 million."),
        {"quote:1": _quote("Revenue was $900,000.")},
        {},
    )

    assert ok is False
    assert "1.25" in str(reason)


def test_explicit_negative_amount_does_not_match_positive_amount() -> None:
    ok, _reason = validate_numeric(
        _claim("Cash flow was -$100 million."),
        {"quote:1": _quote("Cash flow was $100 million.")},
        {},
    )

    assert ok is False


def test_unrelated_quote_values_do_not_create_arithmetic_support() -> None:
    ok, _reason = validate_numeric(
        _claim("Free cash flow was $23,478."),
        {"quote:1": _quote("Cash flow was $285,508 and capex was $308,030.")},
        {},
    )

    assert ok is False


def test_explicit_xbrl_amount_support_allows_normal_rounding() -> None:
    ok, reason = validate_numeric(
        _claim("Revenue was $1.25 million."),
        {},
        {
            "xbrl:revenue": {
                "id": "xbrl:revenue",
                "type": "XBRLFact",
                "value": 1_250_000,
                "unit": "USD",
                "context": {},
            }
        },
    )

    assert ok is True
    assert reason is None
