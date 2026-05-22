"""Sector factor packs used by deterministic business/exposure stages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SectorPack:
    """Loaded sector pack definition."""

    sector: str
    business_activities: dict[str, dict[str, Any]]
    factors: dict[str, dict[str, Any]]
    path: Path


def load_sector_packs(root: Path | None = None) -> list[SectorPack]:
    """Load all YAML sector packs bundled with the project."""
    packs_dir = root or _sector_packs_dir()
    packs: list[SectorPack] = []
    for path in sorted(packs_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        packs.append(
            SectorPack(
                sector=str(data.get("sector") or path.stem),
                business_activities=data.get("business_activities") or {},
                factors=data.get("factors") or {},
                path=path,
            )
        )
    return packs


def _sector_packs_dir() -> Path:
    repo_dir = Path(__file__).resolve().parents[2] / "ontology" / "sector_packs"
    if repo_dir.exists():
        return repo_dir
    return Path(__file__).resolve().parent / "resources" / "sector_packs"


def choose_sector_pack(
    text: str,
    packs: list[SectorPack] | None = None,
    preferred_sector: str | None = None,
) -> SectorPack:
    """Choose the best sector pack by alias hits, falling back to generic."""
    available = load_sector_packs() if packs is None else packs
    if not available:
        raise FileNotFoundError("No sector pack YAML files found")
    generic = next((pack for pack in available if pack.sector == "generic"), available[0])
    if preferred_sector:
        preferred = next((pack for pack in available if pack.sector == preferred_sector), None)
        if preferred is not None:
            return preferred
    text_l = text.lower()
    best_pack = generic
    best_score = -1
    for pack in available:
        if pack.sector == "generic":
            continue
        score = 0
        for spec in [*pack.business_activities.values(), *pack.factors.values()]:
            for alias in spec.get("aliases") or []:
                if str(alias).lower() in text_l:
                    score += 1
        if score > best_score:
            best_score = score
            best_pack = pack
    return best_pack if best_score > 0 else generic


def merged_pack_for_text(
    text: str,
    packs: list[SectorPack] | None = None,
    preferred_sector: str | None = None,
) -> SectorPack:
    """Return generic pack merged with the best specialized pack for the text."""
    available = load_sector_packs() if packs is None else packs
    generic = next((pack for pack in available if pack.sector == "generic"), None)
    chosen = choose_sector_pack(text, available, preferred_sector=preferred_sector)
    if generic is None or chosen.sector == "generic":
        return chosen
    return SectorPack(
        sector=chosen.sector,
        business_activities={**generic.business_activities, **chosen.business_activities},
        factors={**generic.factors, **chosen.factors},
        path=chosen.path,
    )
