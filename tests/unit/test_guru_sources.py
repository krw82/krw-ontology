from __future__ import annotations

import pytest

from krw_ontology.guru.models import AUTHOR_KEYS
from krw_ontology.guru.sources import (
    DEFAULT_AUTHORS,
    DEFAULT_SEED_SOURCES,
    parse_author_keys,
    selected_authors,
    selected_seed_sources,
)


def test_default_guru_authors_are_initial_scope():
    assert [author.author_key for author in DEFAULT_AUTHORS] == list(AUTHOR_KEYS)
    assert [author.author_key for author in DEFAULT_AUTHORS] == [
        "buffett",
        "marks",
        "ackman",
        "flatt",
        "terry_smith",
    ]
    assert len({author.author_key for author in DEFAULT_AUTHORS}) == len(DEFAULT_AUTHORS)
    assert all(author.official_index_url.startswith("https://") for author in DEFAULT_AUTHORS)
    assert all(author.default_rights_policy == "official_link_only_no_fulltext" for author in DEFAULT_AUTHORS)


def test_source_types_stay_in_investor_letter_scope():
    source_types = {
        source_type
        for author in DEFAULT_AUTHORS
        for source_type in author.source_types
    }
    assert source_types <= {"shareholder_letter", "memo"}
    assert selected_authors(["marks"])[0].source_types == ["memo"]


def test_parse_author_keys_accepts_comma_separated_values():
    assert parse_author_keys("buffett, marks,terry-smith") == [
        "buffett",
        "marks",
        "terry_smith",
    ]


def test_selected_authors_rejects_unknown_author():
    with pytest.raises(ValueError, match="Unknown guru author"):
        selected_authors(["munger"])


def test_seed_sources_are_official_direct_documents():
    assert len(DEFAULT_SEED_SOURCES) == 10
    assert {source.author_key for source in DEFAULT_SEED_SOURCES} == {"terry_smith"}
    assert selected_seed_sources(["buffett"]) == []
    terry_seeds = selected_seed_sources(["terry_smith"])
    assert len(terry_seeds) == 10
    assert terry_seeds[0].official_url.startswith("https://www.fundsmith.co.uk/")
    assert terry_seeds[0].source_type == "shareholder_letter"
    assert [source.publication_date for source in terry_seeds[:2]] == ["2025", "2025"]
