"""Observation provider adapters (FMP, FRED with ALFRED vintages, Polygon).

Advisory-only collection plumbing behind the ObservationProvider port. Every
external failure collapses to ``status="unavailable"``; vendor names stay
internal to this layer and are scrubbed from model-facing answers.
"""

from krw_ontology.observation.providers.fmp import FmpHistoryProvider
from krw_ontology.observation.providers.fred import FredProvider
from krw_ontology.observation.providers.polygon import (
    PolygonDailyProvider,
    build_polygon_provider,
)

__all__ = [
    "FredProvider",
    "FmpHistoryProvider",
    "PolygonDailyProvider",
    "build_polygon_provider",
]
