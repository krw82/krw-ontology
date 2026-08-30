"""Observation data layer: provider ports and vendor adapters.

Observation series (prices, valuation multiples, macro indicators) are
advisory-only research context. They never become filing evidence, never
support strong claims, and never ground recommendations or price targets.
Vendor names are internal collection plumbing; the answer layer scrubs them.
"""

from krw_ontology.observation.builder import (
    ObservationsBuildResult,
    build_observations_store,
    collect_observations,
)
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
from krw_ontology.observation.store import (
    OBSERVATIONS_BUILDER_VERSION,
    OBSERVATIONS_SCHEMA_VERSION,
    ObservationsStore,
    verify_observations_schema,
)

__all__ = [
    "KNOWN_FACTOR_LABELS",
    "OBSERVATIONS_BUILDER_VERSION",
    "OBSERVATIONS_SCHEMA_VERSION",
    "ObservationProvider",
    "ObservationPoint",
    "ObservationsBuildResult",
    "ObservationsStore",
    "RawObservation",
    "SeriesDefinition",
    "SeriesFetchRequest",
    "SeriesFetchResult",
    "build_observations_store",
    "collect_observations",
    "load_series_seed",
    "verify_observations_schema",
]
