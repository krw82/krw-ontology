"""Observation data layer: provider ports and vendor adapters.

Observation series (prices, valuation multiples, macro indicators) are
advisory-only research context. They never become filing evidence, never
support strong claims, and never ground recommendations or price targets.
Vendor names are internal collection plumbing; the answer layer scrubs them.
"""

from krw_ontology.observation.ports import (
    ObservationProvider,
    RawObservation,
    SeriesFetchRequest,
    SeriesFetchResult,
)
from krw_ontology.observation.seed import (
    KNOWN_FACTOR_LABELS,
    SeriesDefinition,
    load_series_seed,
)

__all__ = [
    "KNOWN_FACTOR_LABELS",
    "ObservationProvider",
    "RawObservation",
    "SeriesDefinition",
    "SeriesFetchRequest",
    "SeriesFetchResult",
    "load_series_seed",
]
