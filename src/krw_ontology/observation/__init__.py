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

__all__ = [
    "ObservationProvider",
    "RawObservation",
    "SeriesFetchRequest",
    "SeriesFetchResult",
]
