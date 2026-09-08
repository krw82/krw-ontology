"""Curated-stratum gold: hand-built cases for template-resistant failure modes.

The curated file targets what the template generator cannot produce:
vocabulary bridging (colloquial terms with no canonical metric in the plan),
calendar-vs-fiscal period framing, multi-span questions whose answer lives in
different objects/sections, absence checks for plausible-but-undisclosed
metrics, and multi-period trends anchored per filing period.

Object-id existence against the source release is deliberately NOT asserted
here: the release lives outside the repo (read-only data tree), and the task-5
brief authorizes schema-level validation only for this suite. Anchor
verification against the 20260830_193811 shards was done at curation time
(see .superpowers/sdd/2026-09-08-evidence-gold-harness/task-5-report.md).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from krw_ontology.eval_gold.schema import load_evidence_gold

GOLD_PATH = Path(__file__).resolve().parents[2] / "benchmarks" / "evidence_gold_curated_v1.json"
SOURCE_RELEASE_ID = "20260830_193811"

MINIMUM_COUNTS = {
    "vocabulary_mismatch": 8,
    "fiscal_offset": 4,
    "multi_span": 4,
    "not_disclosed": 4,
    "multi_period": 4,
}


def _gold():
    return load_evidence_gold(GOLD_PATH)


def test_gold_loads_and_validates():
    gold = _gold()
    assert gold.format_version == "krw-ontology-evidence-gold/v1"
    assert gold.cases, "curated gold must not be empty"


def test_source_release_binding():
    assert _gold().source_release["release_id"] == SOURCE_RELEASE_ID


def test_case_ids_unique_and_prefixed():
    ids = [case.id for case in _gold().cases]
    assert len(ids) == len(set(ids))
    assert all(case_id.startswith("curated_") for case_id in ids)


def test_every_case_tagged_curated():
    for case in _gold().cases:
        assert "curated" in case.strata, f"case {case.id} missing 'curated' tag"


def test_per_stratum_minimum_counts():
    counts = Counter(stratum for case in _gold().cases for stratum in case.strata)
    for stratum, minimum in MINIMUM_COUNTS.items():
        assert counts[stratum] >= minimum, (
            f"stratum {stratum}: {counts[stratum]} cases < minimum {minimum}"
        )


def test_positive_cases_have_anchored_expected_items():
    for case in _gold().cases:
        if case.expect_not_disclosed:
            continue
        assert case.expected, f"case {case.id}: positive case without expected items"
        for item in case.expected:
            assert item.object_ids or item.text_fragments, (
                f"case {case.id}: expected item for {item.ticker} has no anchor"
            )


def test_not_disclosed_cases_empty_expected_with_forbidden_fragments():
    for case in _gold().cases:
        if "not_disclosed" not in case.strata:
            continue
        assert case.expect_not_disclosed is True, (
            f"case {case.id}: not_disclosed stratum without expect_not_disclosed"
        )
        assert case.expected == [], f"case {case.id}: not_disclosed with expected items"
        assert case.forbidden_fragments, (
            f"case {case.id}: not_disclosed without forbidden_fragments"
        )


def test_vocabulary_mismatch_plans_do_not_name_canonical_metrics():
    """The stratum measures bridging WITHOUT the plan naming the metric.

    If a clause carried ``metrics``, retrieval would go through the metric
    lookup channel and the case would degrade to a template case.
    """
    for case in _gold().cases:
        if "vocabulary_mismatch" not in case.strata:
            continue
        for clause in case.search_plan["clauses"]:
            assert not clause.get("metrics"), (
                f"case {case.id}: vocabulary_mismatch clause {clause.get('clause_id')} "
                "names canonical metrics"
            )


def test_fiscal_offset_plans_leave_periods_unfiltered():
    """Calendar framing must not be hard-coded as a filing-period filter.

    The whole point of the stratum is that the question speaks calendar while
    the shard speaks fiscal (e.g. NVDA calendar-2025 lives in the FY2026
    observation under filing period CY2026); pinning plan periods would give
    the answer away or make the correct anchor unreachable.
    """
    for case in _gold().cases:
        if "fiscal_offset" not in case.strata:
            continue
        assert case.search_plan.get("periods") in ([], None), (
            f"case {case.id}: fiscal_offset plan pins periods {case.search_plan.get('periods')}"
        )


def test_multi_period_cases_span_multiple_expected_periods():
    for case in _gold().cases:
        if "multi_period" not in case.strata:
            continue
        periods = {item.period for item in case.expected}
        assert len(periods) >= 2, (
            f"case {case.id}: multi_period with expected periods {sorted(periods)}"
        )


def test_multi_period_historical_items_accept_comparative_row_provenance():
    """Historical-year items accept the latest filing's comparative row.

    Retrieval legitimately returns the latest filing's comparative (restated)
    row for a historical-year observation, so each expected item other than
    the case's latest year carries >= 2 object ids: the year's own filing
    object first (primary anchor), then the comparative-row alternative
    (any-of semantics), with the curation rationale recorded in notes.
    Existence of the ids against the source release is not re-asserted here
    (see module docstring — anchors were shard-verified at curation time).
    """
    for case in _gold().cases:
        if "multi_period" not in case.strata:
            continue
        assert "comparative-row provenance accepted" in case.notes, (
            f"case {case.id}: notes do not record the comparative-row curation rationale"
        )
        periods = [item.period for item in case.expected if item.period]
        latest_period = max(periods)
        for item in case.expected:
            if not item.object_ids:
                continue
            assert f":{item.period}:10K:" in item.object_ids[0], (
                f"case {case.id}: first anchor for {item.period} is not the own-filing object"
            )
            if item.period == latest_period:
                continue
            assert len(item.object_ids) >= 2, (
                f"case {case.id}: historical item {item.period} carries no comparative-row anchor"
            )
