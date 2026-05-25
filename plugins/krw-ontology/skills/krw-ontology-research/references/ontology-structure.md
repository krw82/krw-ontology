# Ontology Structure And Research Packs

Use this reference to understand how canonical ontology objects relate to runtime research packs.

## Canonical ontology objects

The canonical data model includes:

```text
SourceDocument / SourceLocation / SourceSpan / SourceTable / SourceTableCell
EvidenceQuote / LanguageSignal / SupportLink
CanonicalEntity / EntityMention
ResearchClaim
XBRLFact / MetricObservation / Calculation
BusinessActivity / BusinessFactor / ExternalFactorExposure
AssumptionCandidate / AgreementTerm / BusinessEvent
CompanyBusinessProfile / TemporalLink / TrendObservation / ChangeEvent
Edge
```

Canonical objects and support links are evidence/provenance. Serving tables and packs are not canonical truth.

## Runtime research packs

Runtime packs organize evidence for the AI analyst:

```text
MetricObservation / Calculation / XBRLFact -> metric_series_pack
BusinessActivity / CompanyBusinessProfile / ResearchClaim -> business_profile_pack
BusinessFactor / ExternalFactorExposure / ResearchClaim -> risk_mechanism_pack
Metric/topic/projection rows -> comparison_view
ExternalFactorExposure / related topic candidates -> direct_exposure_pack
Valuation stop policy -> scope_guard_pack
SupportLink / Edge / selected objects -> evidence_index and chain_pack
```

Pack names are internal. Do not expose them in normal answers.

## Directness rule

Retrieval has three separate questions:

```text
1. Is the object related?
2. Does it directly match the user premise?
3. Is it traceable to filing evidence or metric lineage?
```

Related evidence is not direct evidence. Strong claims require direct traceable evidence or metric lineage.
