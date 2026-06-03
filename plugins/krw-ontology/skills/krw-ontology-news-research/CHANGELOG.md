# KRW Ontology Skill Docs Revision Notes

## Summary

Updated the uploaded MCP/skill reference documents to align with the v1.0.0-alpha canonical ontology structure.

## Main Changes

- Replaced canonical references to `RiskFactor`, `GrowthDriver`, and `Headwind` with `BusinessFactor` role views.
- Replaced canonical references to `FinancialMetricValue`, `DerivedMetricValue`, `NumericEvidence`, and `CalculatedNumericSupport` with `MetricObservation`, `Calculation`, `XBRLFact`, source-table support, and `SupportLink` as appropriate.
- Clarified that compatibility names are aliases or generated views, not independent source-of-truth artifacts.
- Added stronger `SupportLink` versus `Edge` separation:
  - `SupportLink` = evidence/provenance lineage.
  - `Edge` = semantic/temporal/business graph relationship.
- Added explicit `trace` versus `chain` tool distinction.
- Added unambiguous event alias rule:
  - `event` / `business_event` -> `BusinessEvent`
  - `change` / `change_event` / `disclosure_change` -> `ChangeEvent`
- Added clean-root rebuild and stale artifact prevention rules to the artifact contract.
- Expanded canonical artifact inventory to include source, evidence, entity, claim, numeric, business semantic, company context, graph, and governance artifacts.
- Added stronger quality gates, including quote exact match, reference resolution, metric validation, scenario effects, and stale artifact detection.
- Updated `ExternalFactorExposure` guidance to emphasize `scenario_effects`.
- Added `AgreementTerm` and `BusinessEvent` usage guidance.
- Updated research synthesis contract so user-facing risk/driver/headwind units are presentation categories backed by `BusinessFactor`, not canonical legacy object types.
- Updated tool documentation so MCP query guidance prefers canonical object types.

## Files Written

- `SKILL.md`
- `references/artifact-contract.md`
- `references/ontology-structure.md`
- `references/research-synthesis-contract.md`
- `references/tools.md`
