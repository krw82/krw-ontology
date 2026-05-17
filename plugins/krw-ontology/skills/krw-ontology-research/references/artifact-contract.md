# KRW Ontology Artifact Contract v1.0.0-alpha

This contract defines the canonical artifact layout used by the v1.0.0-alpha ontology pipeline and MCP serving layer.

The contract has two non-negotiable goals:

```text
1. Canonical artifacts remain clean, traceable, and rebuildable.
2. Serving/index optimizations may improve retrieval, but they must never become the source of truth.
```

## Core Rule

Canonical JSONL artifacts are the source of truth. `agent_index.sqlite` is a read-optimized serving cache built from registry-declared canonical artifacts.

Do not build the agent index by blindly scanning every JSONL under the root. The index builder must read only artifacts declared in the ontology registry snapshot or manifest. This prevents stale pre-migration files from entering the index.

```text
canonical JSONL = source of truth
agent_index.sqlite = rebuildable serving cache
MCP = read-only retrieval / trace interface
LLM = final synthesis layer, not source of truth
```

## Version Contract

Recommended versions for the current migration line:

```text
schema_version = 1.0.0-alpha
agent_index_schema_version = 1.0.0-alpha.1
ontology_registry_version = 1.0.0-alpha
retrieval_text_builder_version = 1.0.0-alpha.1
support_link_builder_version = 1.0.0-alpha.1
```

`agent_index_schema_version` must be bumped whenever SQLite tables, FTS columns, answerability fields, traceability metadata, or company-topic serving tables change.

## Governance Artifacts

Required for every publishable run:

- `run_manifests.jsonl`: run-level provenance, including schema version, registry version, agent index schema version, pipeline version, model metadata, source hashes, artifact root, index path, and created timestamp.
- `ontology_registry_snapshots.jsonl`: frozen registry of canonical artifact files, text-index fields, filter fields, support fields, edge projection rules, compatibility aliases, compact display fields, trace policy, answer-candidate policy, and quality gates used by the run.
- `validation_reports.jsonl`: validation summary written at least after canonical generation, support/edge generation, and serving-index build.
- `taxonomy_terms.jsonl`: versioned controlled terms used for metrics, factors, channels, roles, event types, agreement types, sectors, aliases, and optional topic/facet metadata.

## Canonical Artifact Files

### Source

- `source_documents.jsonl`
- `source_locations.jsonl`
- `source_spans.jsonl`
- `source_tables.jsonl`
- `source_table_cells.jsonl`

### Evidence And Support

- `evidence_quotes.jsonl`
- `language_signals.jsonl`
- `support_links.jsonl`

`support_links.jsonl` is the evidence/provenance relationship layer. It is separate from semantic graph `edges.jsonl`.

### Entity

- `canonical_entities.jsonl`
- `entity_mentions.jsonl`

### Claims

- `research_claims.jsonl`

### Numeric

- `xbrl_facts.jsonl`
- `metric_observations.jsonl`
- `calculations.jsonl`

### Business Semantics

- `business_activities.jsonl`
- `business_factors.jsonl`
- `external_factor_exposures.jsonl`
- `assumption_candidates.jsonl`
- `agreement_terms.jsonl`
- `business_events.jsonl`

### Company Context

- `company_business_profiles.jsonl`
- `temporal_links.jsonl`
- `trend_observations.jsonl`
- `change_events.jsonl`

### Graph

- `edges.jsonl`

### Serving Cache

- `agent_index.sqlite`

The serving cache is not canonical. It must be rebuildable from canonical artifacts and the registry snapshot.

## Serving-Only Derived Structures

The following are allowed in `agent_index.sqlite` or debug-only index output, but they are not canonical artifacts unless explicitly promoted by a future schema version:

```text
retrieval_text
object_fts multi-column text
traceability metadata
answerability metadata
company_topic_index / CompanyTopicProfile-derived rows
QueryFrame / EvidenceFrame intermediate records
ticker_summary discovery rows
fallback trace candidates
```

Do not store `retrieval_text` inside canonical JSONL. It is a serving/index optimization and must be regenerable from canonical objects, support links, edges, taxonomy, and registry rules.

If debug samples are needed, write them under a non-canonical directory such as:

```text
index_debug/retrieval_text_samples.jsonl
index_debug/company_topic_samples.jsonl
```

## SupportLink Direction Rule

`SupportLink` direction is fixed:

```text
from_id = supporting evidence object
to_id   = supported target object
```

Examples:

```text
EvidenceQuote -> ResearchClaim
ResearchClaim -> BusinessFactor
ResearchClaim -> ExternalFactorExposure
EvidenceQuote -> AgreementTerm
XBRLFact -> MetricObservation
Calculation -> MetricObservation
MetricObservation -> AssumptionCandidate
```

Trace traversal normally walks this direction backward from target to support:

```text
ExternalFactorExposure
<- ResearchClaim
<- EvidenceQuote
<- SourceSpan / SourceLocation
<- SourceDocument
```

Recommended field names when possible:

```text
support_object_id = from_id
target_object_id  = to_id
```

If aliases such as `source_id`, `target_id`, `from_object_id`, or `to_object_id` exist, the registry must define exactly how they normalize to this rule.

## Edge Rule

`Edge` is for semantic, business, graph, temporal, and context relationships.

```text
SupportLink = why the object is supported
Edge        = what the object is related to
```

Do not use `Edge` as a substitute for evidence support in final answer verification. `Edge` may help chain exploration and graph-lift ranking, but final strong evidence must come from explicit `SupportLink` or numeric lineage.

## Compatibility Views

Existing product-facing names remain supported as MCP aliases or generated views. They should not become independent source-of-truth artifacts.

```text
RiskFactor / Risk / risk_factor -> BusinessFactor where factor_roles contains risk
GrowthDriver / Driver / growth_driver -> BusinessFactor where factor_roles contains growth_driver or driver
Headwind / headwind -> BusinessFactor where factor_roles contains headwind
FinancialMetricValue / financial_metric_value -> MetricObservation where source_type is reported or xbrl
DerivedMetricValue / derived_metric_value -> MetricObservation where source_type is derived, with Calculation support
NumericEvidence -> MetricObservation, Calculation, SourceTableCell, EvidenceQuote, or SupportLink depending on source
CalculatedNumericSupport -> Calculation + SupportLink
ProjectMilestone -> BusinessEvent where event_type = project_milestone
RegulatoryProceeding -> BusinessEvent where event_type = regulatory_proceeding
GuidanceItem -> BusinessEvent where event_type = guidance_update
ContractTerm -> AgreementTerm where agreement_type is contract/offtake/supply/etc.
CapitalStructureItem -> AgreementTerm where agreement_type is debt_instrument/credit_facility/etc.
Lease -> AgreementTerm where agreement_type = lease
Covenant -> AgreementTerm where agreement_type = covenant
SegmentPerformance -> MetricObservation with segment/product/geography dimensions
```

If compatibility JSONL files are emitted for legacy consumers, place them in a clearly marked compatibility namespace such as `compatibility_views/` or declare them explicitly as generated non-canonical views in `ontology_registry_snapshots.jsonl`. Do not allow old root-level files such as `risks.jsonl` or `financial_metric_values.jsonl` to be picked up by canonical indexing unless the registry explicitly marks them as compatibility views.

## Agent Index Schema Contract

`agent_index.sqlite` should expose, at minimum:

```text
objects
object_fts
support_links
edges
source_documents
quality_summary
traceability_summary
company_topic_index
index_metadata
```

### Multi-Column FTS

The FTS schema is a breaking index change and requires full index rebuild:

```text
object_fts(
  object_id UNINDEXED,
  object_type UNINDEXED,
  ticker UNINDEXED,
  period UNINDEXED,
  text_self,
  text_support,
  text_related,
  text_entities,
  text_aliases,
  compact_text
)
```

Column meaning:

```text
text_self     = object-owned fields
text_support  = support claims, quotes, metrics, calculations, table cells, or XBRL lineage
text_related  = related exposure/activity/event/agreement/metric/entity vocabulary
text_entities = canonical entity aliases and entity mentions
text_aliases  = taxonomy aliases and registry-declared compatibility aliases
compact_text  = concise display-safe summary
```

### Traceability Metadata

Every answer-candidate row in `objects` or equivalent serving tables should include:

```text
trace_status
evidence_chain_count
support_depth
support_quote_count
support_claim_count
semantic_neighbor_count
temporal_context_count
metric_lineage_status
answer_candidate
```

Recommended values:

```text
trace_status:
  traceable
  traceable_metric_lineage
  untraced
  weak_inferred
  orphan

metric_lineage_status:
  xbrl_backed
  calculation_backed
  table_backed
  missing_lineage
  not_metric
```

### Answerability / Directness Metadata

MCP `retrieve`, ticker discovery, and comparison responses should be able to expose:

```text
semantic_relevance
trace_status
tier
evidence_chain_count
support_depth
support_quote_count
support_claim_count
matched_required_facets
matched_related_facets
missing_required_facets
why_tier
recommended_answer_mode
```

Canonical JSONL does not need to store these fields unless a future schema version promotes them. For now, they are serving-layer classifications.

## Answerability Tier Contract

Use these tiers consistently across retrieve, discovery, comparison, and synthesis:

```text
traceable_direct
  Question premise and evidence premise directly match, and explicit SupportLink or metric lineage exists.

traceable_related
  Evidence is traceable, but it is broader, indirect, or adjacent to the question premise.

untraced_direct_candidate
  The object appears semantically direct, but explicit evidence chain is missing.

broad_related_candidate
  Broadly related candidate; useful for exploration but not a direct answer.

no_direct_evidence
  Direct evidence for the requested premise was not found; negative answer can be supported when related search coverage is adequate.

not_answerable
  The index lacks enough relevant evidence or the question is under-specified.
```

Final strong claims should use only `traceable_direct` or `traceable_metric_lineage` evidence. `traceable_related` can be used for context. `untraced_direct_candidate` and `broad_related_candidate` must not be promoted into direct conclusions.

## Metric Lineage Exception

`MetricObservation` does not require quote support to be traceable. It is traceable when one of these paths resolves:

```text
MetricObservation -> XBRLFact -> SourceDocument
MetricObservation -> SourceTableCell -> SourceDocument
MetricObservation -> Calculation -> input MetricObservation(s) -> XBRLFact / SourceTableCell / SourceDocument
```

A metric without quote support but with valid XBRL/table/calculation lineage should be classified as:

```text
trace_status = traceable_metric_lineage
```

## Support Backfill Policy

Backfill order:

```text
1. Serving/index layer deterministic backfill.
2. Evaluate pilot results and false-link rate.
3. Only after approval, regenerate canonical support_links.jsonl / edges.jsonl in a clean root.
```

Do not rewrite canonical artifacts in-place in an existing root.

Canonical SupportLinks may be created from explicit deterministic fields such as:

```text
supported_by_claims
supported_by_quotes
source_claim_ids
source_quote_ids
source_fact_ids
source_metric_ids
calculation_id
input_metric_ids
derived_from_claim_ids
generation_source_object_ids
```

Do not create canonical SupportLinks from semantic similarity, FTS hits, topic text similarity, or LLM intuition. Those may be used only as fallback trace candidates and must be labeled non-canonical, weak, or inferred.

## Company Topic Serving Contract

For large ticker universes, do not brute-force every ticker with full queries. Build a serving-only `company_topic_index` from evidence-derived object clusters or high-value source objects.

Suggested columns:

```text
topic_id
ticker
period
document_type
topic_label
topic_summary
topic_text
source_object_ids
dominant_object_types
evidence_strength
trace_status
support_quote_count
support_claim_count
specificity_score
materiality_hint
generic_topic_score
```

Topic rows are for discovery and planning, not final evidence. Final conclusions must trace selected source objects.

## Required Quality Gates

A run is not publishable unless the final post-edge validation report passes these hard gates:

- `EvidenceQuote.quote_text` exact-match validation passes for every accepted quote.
- All object IDs are unique within the artifact root.
- All object references resolve.
- `SupportLink.from_id`, `SupportLink.to_id`, `Edge.from_id`, and `Edge.to_id` resolve to indexed objects.
- `MetricObservation` values have valid unit, scale, period, period type, and dimensions.
- Every derived `MetricObservation` has a resolving `Calculation` or equivalent support path.
- Every `Calculation` input and output metric reference resolves.
- Every `BusinessFactor` has at least one `factor_role`.
- Every publishable `ExternalFactorExposure` has at least one `scenario_effect` unless explicitly marked as incomplete/rejected.
- Every high-materiality object has non-empty `materiality_basis`.
- Rejected or unsupported objects are excluded from normal query results unless `include_rejected=true`.
- Stale compatibility artifacts are not included in the canonical index.
- Final validation reports `total_rejected = 0` for publishable accepted artifacts, or all rejected rows are excluded from accepted serving indexes.

Early v1-alpha warning gates:

```text
orphan_answer_object_count
weakly_linked_object_count
BusinessFactor trace coverage
ExternalFactorExposure trace coverage
AgreementTerm trace coverage
BusinessEvent trace coverage
company_topic_index coverage
directness negative-test failures
```

Initial v1-alpha may report these as warnings. After support generation stabilizes, promote thresholds to fail gates.

Recommended production thresholds after stabilization:

```text
ResearchClaim quote support coverage >= 95%
BusinessFactor trace coverage >= 90%
ExternalFactorExposure trace coverage >= 90%
AgreementTerm trace coverage >= 80%
BusinessEvent trace coverage >= 80%
MetricObservation lineage coverage >= 95%
```

## Clean Root Rebuild Rule

Breaking schema migrations must be rebuilt in a clean root.

Recommended flow:

```text
old root: /path/krw-ontology-data
new root: /path/krw-ontology-data-v1-alpha
```

Run pilot rebuilds in the new root, inspect artifact inventory and validation reports, then publish by switching the serving root or symlink. Do not rely on `--force` in the existing root when artifact names, canonical object types, support policies, answerability metadata, or index schema have changed.

## Minimal Publishable Inventory

For v1.0.0-alpha, the following must exist and be accepted by the registry for a normal SEC filing run:

```text
run_manifests.jsonl
ontology_registry_snapshots.jsonl
validation_reports.jsonl
taxonomy_terms.jsonl
source_documents.jsonl
source_locations.jsonl
source_spans.jsonl
evidence_quotes.jsonl
support_links.jsonl
research_claims.jsonl
xbrl_facts.jsonl
metric_observations.jsonl
calculations.jsonl
business_activities.jsonl
business_factors.jsonl
external_factor_exposures.jsonl
company_business_profiles.jsonl
edges.jsonl
agent_index.sqlite
```

The following may be empty for a specific ticker or filing, but the registry should still know whether they are expected, optional, or unsupported for that run:

```text
source_tables.jsonl
source_table_cells.jsonl
canonical_entities.jsonl
entity_mentions.jsonl
assumption_candidates.jsonl
agreement_terms.jsonl
business_events.jsonl
temporal_links.jsonl
trend_observations.jsonl
change_events.jsonl
```

## Negative / Directness Test Gate

Every pilot should include at least one direct-exposure negative test where broad related evidence exists but direct evidence should be false.

Example:

```text
Question: Is VG directly exposed to semiconductor memory price cycle or GPU HBM price volatility?
Expected: direct_answerable=false, related_context_available=true.
Allowed related context: construction commodities, feed gas, Henry Hub, LNG pricing.
Forbidden conclusion: VG is directly exposed to semiconductor/GPU/HBM pricing.
```

This test protects against broad commodity, margin, exposure, or geopolitical matches being promoted into direct evidence.
