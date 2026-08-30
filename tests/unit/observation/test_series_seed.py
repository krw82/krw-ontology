"""Unit tests for the P1 observation series seed and its metric registrations.

The seed is the static contract between collection (B1 adapters) and the
observation builder (B3): series families, their canonical metrics, and the
provisional factor labels.  Everything here is fixture/local-file only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

from krw_ontology.agent_index.metric_dictionary import metric_dictionary_catalog
from krw_ontology.observation.seed import (
    KNOWN_FACTOR_LABELS,
    SeriesDefinition,
    load_series_seed,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The P1 FRED set from the task brief, verbatim provider series ids.
_EXPECTED_FRED_IDS = {
    "macro_cpi_yoy": "CPIAUCSL",
    "macro_core_cpi_yoy": "CPILFESL",
    "macro_core_pce_yoy": "PCEPILFE",
    "macro_fed_funds_rate": "FEDFUNDS",
    "macro_fed_funds_target_upper": "DFEDTARU",
    "macro_dgs2_yield": "DGS2",
    "macro_dgs10_yield": "DGS10",
    "macro_dgs30_yield": "DGS30",
    "macro_treasury_10y2y_spread": "T10Y2Y",
    "macro_breakeven_inflation_10y": "T10YIE",
    "macro_unemployment_rate": "UNRATE",
    "macro_nonfarm_payrolls": "PAYEMS",
    "macro_initial_jobless_claims": "ICSA",
    "macro_nominal_gdp": "GDP",
    "macro_real_gdp": "GDPC1",
    "macro_industrial_production": "INDPRO",
    "macro_retail_sales": "RSAFS",
    "macro_housing_starts": "HOUST",
    "macro_vix": "VIXCLS",
    "macro_sp500": "SP500",
}

_PER_TICKER_FAMILIES = ("price_close", "trailing_pe_ttm", "price_to_book_ttm", "polygon_ohlcv")


def test_every_seed_metric_is_in_metric_dictionary():
    seed = load_series_seed()
    catalog = metric_dictionary_catalog()
    for series_key, definition in seed.items():
        assert catalog.canonicalize(definition.canonical_metric) is not None, series_key


def test_fred_seed_has_vintage_domain_and_factor_label():
    seed = load_series_seed()
    cpi = seed["macro_cpi_yoy"]
    assert cpi.provider == "fred" and cpi.domain == "macro"
    assert cpi.factor in KNOWN_FACTOR_LABELS


def test_seed_covers_the_brief_fred_set_exactly():
    seed = load_series_seed()
    fred_series = {
        key: definition for key, definition in seed.items() if definition.provider == "fred"
    }
    assert {key: definition.provider_series_id for key, definition in fred_series.items()} == (
        _EXPECTED_FRED_IDS
    )
    for definition in fred_series.values():
        assert definition.domain == "macro"
        assert definition.is_per_ticker is False
        assert definition.ticker is None
        assert definition.factor in KNOWN_FACTOR_LABELS


def test_per_ticker_families_are_flagged_templates():
    seed = load_series_seed()
    for family in _PER_TICKER_FAMILIES:
        definition = seed[family]
        assert isinstance(definition, SeriesDefinition)
        assert definition.is_per_ticker is True, family
        assert definition.ticker is None, family
        assert definition.domain in {"price", "valuation"}, family

    price_close = seed["price_close"]
    assert price_close.provider == "fmp"
    assert price_close.canonical_metric == "last_price"

    trailing_pe = seed["trailing_pe_ttm"]
    assert trailing_pe.provider == "fmp" and trailing_pe.domain == "valuation"
    assert trailing_pe.canonical_metric == "trailing_pe_ttm"

    price_to_book = seed["price_to_book_ttm"]
    assert price_to_book.provider == "fmp" and price_to_book.domain == "valuation"
    assert price_to_book.canonical_metric == "price_to_book_ttm"

    polygon_ohlcv = seed["polygon_ohlcv"]
    assert polygon_ohlcv.provider == "polygon" and polygon_ohlcv.domain == "price"


def test_market_snapshot_vocabulary_aligns_into_dictionary():
    """Existing market-snapshot field names canonicalize to the seed metrics."""
    catalog = metric_dictionary_catalog()
    assert catalog.canonicalize("last_price") == "last_price"
    assert catalog.canonicalize("trailing_pe") == "trailing_pe_ttm"
    assert catalog.canonicalize("price_to_book") == "price_to_book_ttm"
    assert catalog.canonicalize("pe_ttm") == "trailing_pe_ttm"
    assert catalog.canonicalize("pbr") == "price_to_book_ttm"


def test_korean_macro_aliases_canonicalize():
    catalog = metric_dictionary_catalog()
    assert catalog.canonicalize("소비자물가") == "cpi_yoy"
    assert catalog.canonicalize("기준금리") == "fed_funds_rate"
    assert catalog.canonicalize("실업률") == "unemployment_rate"
    assert catalog.canonicalize("종가") == "last_price"


def test_pyproject_force_include_resolves_to_real_seed_file():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    source = "ontology/observation/series_seed.yaml"
    target = force_include[source]
    # The wheel target must match the loader's packaged fallback layout
    # (<package>/resources/observation/series_seed.yaml), taxonomy-style.
    assert target == "krw_ontology/resources/observation/series_seed.yaml"
    assert (PROJECT_ROOT / source).is_file()
    # And the raw YAML at that path parses with at least one series entry.
    payload = yaml.safe_load((PROJECT_ROOT / source).read_text())
    assert isinstance(payload.get("series"), dict) and payload["series"]


def test_loader_rejects_unknown_keys_and_bad_factor(tmp_path: Path):
    bad_seed = tmp_path / "series_seed.yaml"
    bad_seed.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "series": {
                    "macro_cpi_yoy": {
                        "domain": "macro",
                        "provider": "fred",
                        "provider_series_id": "CPIAUCSL",
                        "canonical_metric": "cpi_yoy",
                        "unit": "percent",
                        "frequency": "monthly",
                        "adjustment": "seasonal",
                        "factor": "inflation",
                        "surprise_field": "not allowed",
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError, match="unknown"):
        load_series_seed(bad_seed)

    bad_factor = tmp_path / "bad_factor.yaml"
    bad_factor.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "series": {
                    "macro_cpi_yoy": {
                        "domain": "macro",
                        "provider": "fred",
                        "provider_series_id": "CPIAUCSL",
                        "canonical_metric": "cpi_yoy",
                        "unit": "percent",
                        "frequency": "monthly",
                        "adjustment": "seasonal",
                        "factor": "not_a_factor",
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError, match="factor"):
        load_series_seed(bad_factor)
