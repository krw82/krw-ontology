"""Static metric-dictionary alias resolution for the similarity-proposal lane.

``resolve_alias_terms`` bridges colloquial metric vocabulary in free text to
dictionary canonical ids.  The curated ``vocabulary_mismatch`` gold families
(top line, bottom line, operating profit, profit per share, research spending,
debt load) must resolve, while the buyback/repurchase family stays unresolved
until a repurchase canonical exists in the dictionary (its gold anchors are
claims, not metric observations, so Task 2's dictionary-canonical channel
cannot engage it).
"""

from __future__ import annotations

from krw_ontology.agent_index.metric_dictionary import (
    metric_dictionary_catalog,
    resolve_alias_terms,
)


def test_curated_vocabulary_families_resolve() -> None:
    cases = [
        # Curated question phrasings (English).
        ("What was Apple's top line for the year ended September 2025?", {"top line": "revenue"}),
        (
            "How much did Microsoft earn on the bottom line in fiscal year 2025?",
            {"bottom line": "net_income"},
        ),
        (
            "What operating profit did Walmart report for fiscal 2026?",
            {"operating profit": "operating_income"},
        ),
        ("What was Amazon's profit per share in 2025?", {"profit per share": "eps"}),
        (
            "How big was Apple's research spending in fiscal 2025?",
            {"research spending": "research_and_development"},
        ),
        ("How heavy is Meta's debt load as of December 2025?", {"debt load": "total_debt"}),
        # Korean terms for the same families.
        ("매출", {"매출": "revenue"}),
        ("순이익", {"순이익": "net_income"}),
        ("영업이익", {"영업이익": "operating_income"}),
        ("주당순이익", {"주당순이익": "eps"}),
        ("연구개발비", {"연구개발비": "research_and_development"}),
        ("차입금", {"차입금": "total_debt"}),
    ]
    for text, expected in cases:
        assert resolve_alias_terms(text) == expected, text


def test_text_without_aliases_returns_empty() -> None:
    assert resolve_alias_terms("The quick brown fox jumps over the lazy dog.") == {}
    assert resolve_alias_terms("") == {}


def test_multiple_families_return_together() -> None:
    text = "The company's top line grew while its debt load shrank."
    assert resolve_alias_terms(text) == {"top line": "revenue", "debt load": "total_debt"}


def test_longest_alias_wins_over_overlapping_shorter_alias() -> None:
    result = resolve_alias_terms("consolidated earnings per share for the year")
    assert result == {"earnings_per_share": "eps"}
    assert "earnings" not in result
    assert "net_income" not in result.values()

    # Longest-match precedence also applies across Korean aliases.
    assert resolve_alias_terms("연구개발비 지출") == {"연구개발비": "research_and_development"}


def test_matching_is_case_insensitive_and_plural_aware() -> None:
    assert resolve_alias_terms("OPERATING PROFITS") == {"operating profit": "operating_income"}
    assert resolve_alias_terms("Heavy Debt Loads Persist") == {"debt load": "total_debt"}
    # Canonical ids and snake_case aliases match natural spaced text.
    assert resolve_alias_terms("Revenue Growth outlook") == {"revenue_growth": "revenue_growth"}


def test_buyback_family_has_no_dictionary_canonical_yet() -> None:
    # Deliberate pin: the repurchase/buyback vocabulary family has no canonical
    # metric in the dictionary and its gold anchors are claims, so it must NOT
    # resolve through the dictionary-canonical channel (Task 3 recovers it).
    assert resolve_alias_terms("share buybacks and repurchase activity") == {}
    assert resolve_alias_terms("자사주 매수") == {}


def test_resolution_is_deterministic() -> None:
    text = "top line, bottom line, and debt load commentary"
    first = resolve_alias_terms(text)
    second = resolve_alias_terms(text)
    assert first == second
    assert list(first) == sorted(first)


def test_module_level_helper_matches_catalog_method() -> None:
    catalog = metric_dictionary_catalog()
    assert resolve_alias_terms("top line") == catalog.resolve_alias_terms("top line")


def test_every_table_alias_round_trips_to_a_dictionary_canonical() -> None:
    catalog = metric_dictionary_catalog()
    for canonical, entry in catalog.entries.items():
        for written in (canonical, *(entry.get("aliases") or [])):
            resolved = catalog.resolve_alias_terms(written)
            assert resolved == {written: canonical}, written
            # The matcher may only ever return dictionary canonicals: the
            # canonical accepts itself and canonicalize agrees on every alias.
            assert catalog.canonicalize(canonical) == canonical
            assert catalog.canonicalize(written) == canonical
