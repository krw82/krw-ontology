from krw_ontology.eval_gold.grader import aggregate, grade_case, normalize_text
from krw_ontology.eval_gold.schema import EvidenceGoldCase, ExpectedEvidence


def _state(units, clause_coverage=None):
    return {
        "evidence_units": units,
        "clause_coverage": clause_coverage if clause_coverage is not None else {"c1": 1},
    }


def _unit(**over):
    base = {
        "evidence_id": "eu-1",
        "object_id": "obj-1",
        "object_type": "metric_observation",
        "ticker": "NVDA",
        "period": "FY2025",
        "title": "Total revenue grew 26% to $130.5B",
        "summary": "NVIDIA total revenue for fiscal 2025",
        "supports_clause_ids": ["c1"],
    }
    base.update(over)
    return base


def test_normalize_text_collapses_ws_and_case():
    assert normalize_text("  Total   REVENUE\n grew ") == "total revenue grew"


def test_object_id_match_ignores_period_when_absent():
    item = ExpectedEvidence(ticker="NVDA", object_ids=["obj-9"])
    from krw_ontology.eval_gold.grader import match_expected
    m = match_expected(item, [_unit(object_id="obj-9", period="FY2024")])
    assert m is not None


def test_period_filter_enforced():
    item = ExpectedEvidence(ticker="NVDA", period="FY2025", object_ids=["obj-1"])
    from krw_ontology.eval_gold.grader import match_expected
    assert match_expected(item, [_unit(period="FY2024")]) is None


def test_period_label_fy_cy_equivalence():
    from krw_ontology.eval_gold.grader import match_expected, normalize_period_label

    # Pure function: bare FY/CY + 4-digit year collapse to the CY twin;
    # every other shape (quarter-suffixed, free text) passes through unchanged.
    assert normalize_period_label("FY2024") == "CY2024"
    assert normalize_period_label("CY2024") == "CY2024"
    assert normalize_period_label("CY2026Q1") == "CY2026Q1"
    assert normalize_period_label("FY2024Q3") == "FY2024Q3"

    # (a) label twins denote the same filing key: item CY2024 matches unit FY2024.
    item = ExpectedEvidence(ticker="NVDA", period="CY2024", object_ids=["obj-1"])
    assert match_expected(item, [_unit(period="FY2024")]) is not None

    # (b) a different year is still not the same label.
    assert match_expected(item, [_unit(period="FY2025")]) is None

    # (c) quarter-suffixed labels stay strict: both forms are pass-through
    # (non-matching regex), so CY2026Q1 vs FY2026Q1 does NOT match.
    strict = ExpectedEvidence(ticker="NVDA", period="CY2026Q1", object_ids=["obj-1"])
    assert match_expected(strict, [_unit(period="FY2026Q1")]) is None

    # (d) absent item period keeps the filter inactive for any unit period.
    no_period = ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])
    assert match_expected(no_period, [_unit(period="FY2024")]) is not None


def test_text_fragment_containment_on_title_summary():
    item = ExpectedEvidence(ticker="NVDA", text_fragments=["revenue grew 26%"])
    from krw_ontology.eval_gold.grader import match_expected
    assert match_expected(item, [_unit()]) is not None


def _case(expected, **over):
    base = dict(
        id="c1",
        question="q",
        search_plan={
            "question": "nvda revenue", "intent": "lookup",
            "clauses": [{
                "clause_id": "c1",
                "retrieval_query": "nvda revenue",
                "required_concepts": ["revenue"],
            }],
            "tickers": ["NVDA"],
        },
        expected=expected,
    )
    base.update(over)
    return EvidenceGoldCase(**base)


def test_grade_case_full_recall_passes():
    case = _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])])
    r = grade_case(case, _state([_unit()]))
    assert r.passed and r.recall == 1.0 and not r.zero_hit


def test_grade_case_partial_recall_fails():
    case = _case([
        ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"]),
        ExpectedEvidence(ticker="AMD", object_ids=["obj-2"]),
    ])
    r = grade_case(case, _state([_unit()]))
    assert not r.passed and r.recall == 0.5


def test_zero_hit_detected_from_clause_coverage():
    case = _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])])
    r = grade_case(case, _state([], clause_coverage={}))
    assert r.zero_hit and not r.passed


def test_not_disclosed_violation():
    case = _case(
        [], expect_not_disclosed=True,
        forbidden_fragments=["quantum revenue"],
        search_plan={
            "question": "nvda quantum revenue", "intent": "lookup",
            "clauses": [{
                "clause_id": "c1",
                "retrieval_query": "nvda quantum revenue",
                "required_concepts": ["quantum revenue"],
            }],
            "tickers": ["NVDA"],
        },
    )
    r = grade_case(case, _state([_unit(summary="NVIDIA quantum revenue breakdown")]))
    assert r.not_disclosed_violation and not r.passed


def test_aggregate_breaks_down_by_stratum():
    r1 = grade_case(
        _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])], strata=["template"]),
        _state([_unit()]),
    )
    r2 = grade_case(
        _case([ExpectedEvidence(ticker="NVDA", object_ids=["obj-1"])], strata=["vocabulary_mismatch"]),
        _state([]),
    )
    agg = aggregate([r1, r2])
    assert agg["overall"]["pass_rate"] == 0.5
    assert agg["strata"]["template"]["pass_rate"] == 1.0
    assert agg["strata"]["vocabulary_mismatch"]["zero_hit_rate"] == 1.0
