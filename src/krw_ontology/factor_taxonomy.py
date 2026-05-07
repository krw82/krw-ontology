"""Global external factor taxonomy and sector coverage expectations."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass(frozen=True)
class FactorSpec:
    key: str
    category: str
    aliases: tuple[str, ...]
    benchmarks: tuple[str, ...]
    default_channels: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "aliases": list(self.aliases),
            "benchmarks": list(self.benchmarks),
            "channels": list(self.default_channels),
            "default_channels": list(self.default_channels),
        }


@dataclass(frozen=True)
class FactorTaxonomy:
    factors: dict[str, FactorSpec]
    alias_to_key: dict[str, str]
    path: Path


@dataclass(frozen=True)
class SectorExpectations:
    sectors: dict[str, dict[str, Any]]
    alias_to_sector: dict[str, str]
    path: Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


@lru_cache(maxsize=8)
def load_factor_taxonomy(path: str | Path | None = None) -> FactorTaxonomy:
    """Load the global factor taxonomy."""
    taxonomy_path = Path(path) if path else _repo_root() / "ontology" / "taxonomy" / "factors.yaml"
    data = yaml.safe_load(taxonomy_path.read_text()) or {}
    factors: dict[str, FactorSpec] = {}
    alias_to_key: dict[str, str] = {}

    for raw_key, spec in (data.get("factors") or {}).items():
        key = _normalize_key(raw_key)
        aliases = tuple(str(alias) for alias in spec.get("aliases") or [])
        factor = FactorSpec(
            key=key,
            category=str(spec.get("category") or "unknown"),
            aliases=aliases,
            benchmarks=tuple(str(value) for value in spec.get("benchmarks") or []),
            default_channels=tuple(
                str(value) for value in spec.get("default_channels") or ["business_performance"]
            ),
        )
        factors[key] = factor
        for candidate in (key, raw_key, spec.get("name"), *aliases):
            if candidate:
                alias_to_key[_normalize_key(str(candidate))] = key

    return FactorTaxonomy(factors=factors, alias_to_key=alias_to_key, path=taxonomy_path)


@lru_cache(maxsize=8)
def load_sector_expectations(path: str | Path | None = None) -> SectorExpectations:
    """Load sector coverage expectations."""
    expectations_path = (
        Path(path) if path else _repo_root() / "ontology" / "taxonomy" / "sector_expectations.yaml"
    )
    data = yaml.safe_load(expectations_path.read_text()) or {}
    sectors: dict[str, dict[str, Any]] = {}
    alias_to_sector: dict[str, str] = {}

    for raw_sector, spec in (data.get("sectors") or {}).items():
        sector = _normalize_key(raw_sector)
        aliases = [str(alias) for alias in spec.get("aliases") or []]
        expected = [_normalize_key(value) for value in spec.get("expected_factors") or []]
        sectors[sector] = {
            "aliases": aliases,
            "expected_factors": expected,
        }
        for candidate in (sector, raw_sector, *aliases):
            alias_to_sector[_normalize_key(str(candidate))] = sector

    return SectorExpectations(sectors=sectors, alias_to_sector=alias_to_sector, path=expectations_path)


def canonical_factor_key(value: str | None, taxonomy: FactorTaxonomy | None = None) -> str:
    """Return a canonical factor key for a hint or alias.

    Unknown hints are normalized and preserved so evidence-backed novel factors
    are not dropped, but their category remains unknown until the taxonomy is
    extended.
    """
    if not value:
        return ""
    loaded = taxonomy or load_factor_taxonomy()
    normalized = _normalize_key(value)
    return loaded.alias_to_key.get(normalized, normalized)


def factor_spec(factor_key: str, taxonomy: FactorTaxonomy | None = None) -> dict[str, Any]:
    """Return a factor spec dict, falling back to an unknown-category spec."""
    loaded = taxonomy or load_factor_taxonomy()
    normalized = canonical_factor_key(factor_key, loaded)
    spec = loaded.factors.get(normalized)
    if spec:
        return spec.as_dict()
    return {
        "category": "unknown",
        "aliases": [],
        "benchmarks": [],
        "channels": ["business_performance"],
        "default_channels": ["business_performance"],
    }


def factor_aliases(factor_key: str, taxonomy: FactorTaxonomy | None = None) -> list[str]:
    """Return names and aliases useful for evidence checks."""
    loaded = taxonomy or load_factor_taxonomy()
    normalized = canonical_factor_key(factor_key, loaded)
    spec = loaded.factors.get(normalized)
    if not spec:
        return [normalized, normalized.replace("_", " ")]
    return [normalized, normalized.replace("_", " "), *spec.aliases, *spec.benchmarks]


def format_factor_taxonomy_for_prompt(taxonomy: FactorTaxonomy | None = None) -> str:
    """Return a compact canonical factor list for extraction prompts."""
    loaded = taxonomy or load_factor_taxonomy()
    lines: list[str] = []
    for key in sorted(loaded.factors):
        spec = loaded.factors[key]
        aliases = ", ".join(spec.aliases[:8])
        channels = ", ".join(spec.default_channels)
        lines.append(
            f"- {key} | category={spec.category} | channels={channels}"
            + (f" | aliases={aliases}" if aliases else "")
        )
    return "\n".join(lines)


def matching_factor_keys(text: str, taxonomy: FactorTaxonomy | None = None) -> list[str]:
    """Find canonical factors with aliases explicitly present in text."""
    loaded = taxonomy or load_factor_taxonomy()
    text_l = text.lower()
    matches: list[str] = []
    for key, spec in loaded.factors.items():
        candidates = [key.replace("_", " "), *spec.aliases, *spec.benchmarks]
        if any(str(candidate).lower() in text_l for candidate in candidates if candidate):
            matches.append(key)
    return matches


def normalize_sector_hint(value: str | None, expectations: SectorExpectations | None = None) -> str | None:
    """Normalize a raw sector hint for profile and coverage checks."""
    if not value:
        return None
    loaded = expectations or load_sector_expectations()
    normalized = _normalize_key(value)
    return loaded.alias_to_sector.get(normalized, normalized)


def expected_factors_for_sector(
    sector: str | None,
    expectations: SectorExpectations | None = None,
) -> list[str]:
    """Return expected canonical factors for a normalized sector."""
    loaded = expectations or load_sector_expectations()
    normalized = normalize_sector_hint(sector, loaded) or "generic"
    return list((loaded.sectors.get(normalized) or {}).get("expected_factors") or [])
