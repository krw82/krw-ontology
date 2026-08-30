# Shard schema v3 parity report

- Date: 2026-08-31
- Candidate release: `20260830_193811` vs prod: `20260813_121609`
- Verdict: **PASS** (11/12 plans identical + 1 growth-attributed; see Round 2 below — Round 1 documents the initial FAIL against the dirty build `20260830_160657`)
- Addendum: the B8 e2e/matrix addendum lives in the SDD workspace at `krw-agnet/.superpowers/sdd/2026-08-30-observation-p1-and-schema-v3/task-B8-report.md` (untracked scratch).

---

# Task A5 — Quality Parity Gate: shard schema v3 candidate vs prod

- Date: 2026-08-30
- Candidate: `~/krw-ontology-data/releases/v2-dev/dev/20260830_160657` (10,506,122 objects, 356 shards, spine-projection/v7, company-shard/v3)
- Baseline: `~/krw-ontology-data/releases/prod/current -> 20260813_121609` (18,392,007 objects, 355 shards, spine-projection/v6, company-shard/v2)
- Serving pairing used for parity: **prod release served by prod code (`~/krw-ontology`, main @ 44a1484)** vs **v2 release served by v2 code (`~/krw-ontology-v2/krw-ontology`, feat/observation-v3-bundle @ 58e4b56)**. The v2 code fail-fast refuses the prod release (`manifest_builder_binding_mismatch: spine_projection_version, company_shard_schema_version, metric_dictionary_*`), so cross-serving was not possible; this is the same pairing production uses.
- Harness: `/tmp/a5_parity/` (`plans.json`, `runner.py`, `compare.py`, `rank_probe.py`, `compare_traces.py`); outputs in `/tmp/a5_parity/out_prod/` and `/tmp/a5_parity/out_v2/`. Both releases were opened strictly read-only (`mode=ro&immutable=1`).

## GATE VERDICT: **FAIL**

Structural check fails (v2 spine locator still holds 1,306,151 SupportLink rows — spec requires 0; 118 of 356 shards were built with SupportLink still materialized in `objects`), and 1 of the gold plans is not byte-parity on its evidence object_id set. Gold gates, trace parity, answerability, computed values, and 11/12 plan parity are green. The serving path itself is healthy (SupportLink never appears in DEFAULT_QUERY_TYPES evidence; the single parity diff is a 1-unit selection swap near the 12-unit budget, not lost evidence).

---

## 1. Structural

`verify/release_verify.json`: `ok: true`, `errors: []`, `file_count: 361`, format `krw-ontology-release-verify/v1` — the release's own verification is green (it does not check the SupportLink locator invariant).

Spine locator SupportLink count (the gate query):

```
SELECT COUNT(*) FROM global_object_locator WHERE object_type='SupportLink'
-- v2: 1,306,151   (REQUIRED: 0)  -> FAIL
```

Root cause: SupportLink demotion (commit 87957ae, "demote SupportLink to derived shard table (schema v3), 51% object reduction") landed correctly in only 238/356 shards. 118 shards contain BOTH the derived `support_links` table AND SupportLink rows still in `objects` (e.g., GRMN: 41,622 SupportLink rows in `objects` + only 9,375 rows in `support_links`; WEC: 113,314 in `objects`). The spine locator is projected from shard `objects`, so those 118 shards contribute 1,306,151 SupportLink locator rows. Test docstring in `tests/unit/test_shard_schema_v3.py` states SupportLink must "no longer inflate the objects table … or the spine locator". Dirty-shard list (118 tickers) saved at `/tmp/a5_parity/dirty_shards_118.txt`.

Top object_type counts, v2 locator (full count) vs prod locator (full count; the task's audit figures match prod exactly):

| object_type | v2 count | prod count | delta |
|---|---:|---:|---:|
| SupportLink | 1,306,151 | 9,386,337 | -8,080,186 (incomplete demotion) |
| XBRLFact | 2,471,827 | 2,427,652 | +44,175 |
| EvidenceQuote | 945,608 | 923,569 | +22,039 |
| ResearchClaim | 930,898 | 907,460 | +23,438 |
| SourceLocation | 789,513 | 775,404 | +14,109 |
| SourceSpan | 789,513 | 775,404 | +14,109 |
| LanguageSignal | 614,051 | 599,147 | +14,904 |
| BusinessFactor | 546,228 | 530,405 | +15,823 |
| AssumptionCandidate | 474,470 | 462,444 | +12,026 |
| EntityMention | 465,520 | 459,201 | +6,319 |
| ExternalFactorExposure | 336,783 | 329,369 | +7,414 |
| MetricObservation | 50,656 | 49,377 | +1,279 |

Reconciliation: 18,392,007 (prod) + 194,301 (corpus growth: 34 new documents + new ticker AA between 2026-08-13 and 2026-08-30) - 8,080,186 (SupportLink actually removed) = 10,506,122 (v2). The task's expectation "v2 = prod minus SupportLink minus tiny governance deltas" does not hold: the deltas are not tiny because the source corpus itself grew (34 new docs: 11× 10-K, 22× 10-Q, 1× COMPANY for new ticker AA; all periods CY2026 or later). "Expected-v3" count if demotion were complete: 9,199,971.

Shard-level schema v3 facts confirmed: v2 shards have `support_links` derived table, dead `object_text` table removed (prod has `object_text`), 356 shards in manifest.

## 2. Gold gates

`uv run --extra dev pytest tests/unit/test_router_benchmark_gate.py -v` → **21 passed in 2.04s** (all synthetic gold-contract tests).

source_manifest_hash binding: NOT bound. The checked-in gold (`router_planned_gold_v2.json`, `router_gold_v2.json`) binds `sha256:61846e49…` (release 20260711_121636). v2's `source_manifest.json` `manifest_hash` = `sha256:5c2f0c14…` (also recorded in v2 spine metadata); prod's = `sha256:22d3d3b5…`. The binding rule (`scripts/benchmark_mcp_candidate.py::_release_binding`) raises on mismatch, so the gold files cannot be hash-bound to this candidate (nor to current prod) without re-authoring/re-accepting the gold source binding. The 21 gate tests validate contract structure only and pass regardless — the gold source binding for this release is unverified.

## 3. Parity harness (SearchPlan v2 → ResearchState v2, production code path)

Harness constructs the serving store exactly as the MCP server does (`prepare_mcp_runtime` → env vars → `_store(_runtime_global_spine_path())` → `query_context_tool`), in-process, per release. 12 plans executed (the gold file contains only 5 plans, all qualitative; 7 harness-authored plans were added to reach the required diversity — provenance recorded per plan in `/tmp/a5_parity/plans.json`):

- Qualitative multi-concept (verbatim gold): aapl_regulation_supply, nvda_geopolitics_regulators, meta_privacy_energy, wmt_cost_pharmacy, jpm_rates_loans
- Metric-clause (authored; canonical metrics present in BOTH dictionaries — prod 25, v2 48): metric_aapl_revenue_yoy, metric_msft_income_margin, metric_wmt_eps_fcf
- Comparison (authored; value/value_difference and value/growth_rate axes): compare_aapl_msft_revenue, compare_cost_hd_margin_growth
- Chain-adjacent (authored; topics verified linked via global_chain_index in both releases): chain_mck_slb_working_capital (working_capital_change MCK↔SLB), chain_aig_hon_estimate_risk (accounting_estimate_assumption_risk AIG↔HON)

Authored plans were constrained to stable scope (explicit tickers, 10-K, CY2023–CY2025) so corpus growth cannot legitimately enter results; gold plans ran exactly as authored (universe=covered).

| plan | category | verdict | object_ids prod→v2 | answerability | clause_coverage | computed_values |
|---|---|---|---|---|---|---|
| router_plan_aapl_regulation_supply | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| router_plan_nvda_geopolitics_regulators | qualitative (gold) | **DIFF** | 12/12, 1-unit swap | partial=partial (identical incl. reason codes) | china clause: 2 ev → 1 ev (status still covered, direct/strong) | identical |
| router_plan_meta_privacy_energy | qualitative (gold) | PASS | 12/12 identical | answerable=answerable | identical | identical |
| router_plan_wmt_cost_pharmacy | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| router_plan_jpm_rates_loans | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| metric_aapl_revenue_yoy | metric | PASS | 12/12 identical | answerable=answerable | identical | identical |
| metric_msft_income_margin | metric | PASS | 12/12 identical | answerable=answerable | identical | identical |
| metric_wmt_eps_fcf | metric | PASS | 12/12 identical | partial=partial | identical | identical |
| compare_aapl_msft_revenue | comparison | PASS | 12/12 identical | partial=partial | identical | identical |
| compare_cost_hd_margin_growth | comparison | PASS | 12/12 identical | partial=partial | identical | identical |
| chain_mck_slb_working_capital | chain-adjacent | PASS | 12/12 identical | partial=partial | identical | identical |
| chain_aig_hon_estimate_risk | chain-adjacent | PASS | 12/12 identical | partial=partial | identical | identical |

**11/12 plans identical.** No SupportLink object ever appears in evidence on either release (DEFAULT_QUERY_TYPES is identical in both codebases and excludes SupportLink). match_mode sets identical (all strict).

The one diff, classified: `router_plan_nvda_geopolitics_regulators`, china clause. Prod's 12 units included `claim:NVDA:CY2024:10K:chinese-regulators-have-inquired-about-nvidia-s-sales-and-mellanox-acquisition-c…`; v2 replaced it with `quote:NVDA:CY2024:10K:item1a_00:0105:001`. Both objects exist byte-identically in both releases (same claim_text/quote_text, same 379-char object_search_text, same traceability count, same support links). Classification: **parity_diff_stable_document** (not corpus growth). Rank probe (same clause, limit_results=50): the dropped claim is still **rank 1** in v2; v2 additionally inserted `quote:GRMN:CY2024:10K:item1a_00:0005:001` at global rank 2 — and GRMN is one of the 118 dirty shards. Mechanism: ranking drift emanating from dirty shards (their `objects` population/FTS stats differ) perturbs scores; at the 12-unit selection budget the GRMN quote displaces the NVDA CY2024 claim from the final evidence set, dropping china clause from 2 to 1 evidence ids. Answerability status, strong_claim_allowed, reason codes, computed_values, and calculation_coverage are all unchanged. Missing/extra by type: ResearchClaim -1 / EvidenceQuote +1; answerability changed: no.

## 4. Trace parity spot-check

Five evidence object ids returned by the golden runs (one per type), traced via the exact trace-tool store path (`store.trace(object_id)`):

| type | object_id | result |
|---|---|---|
| EvidenceQuote | quote:AAPL:CY2024:10K:item8_00:0099:001 | identical (quote_text present, 167 chars) |
| ResearchClaim | claim:AIG:CY2024:10K:aig-s-accounting-estimates-rely-on-assumptions-… | identical (claim_text present, support depth 4) |
| MetricObservation | metric_observation:AAPL:CY2023:10K:6fbcca65aa1edb | identical (support depth 5, metric lineage fields equal) |
| ExternalFactorExposure | external_factor_exposure:FITB:CY2026Q2:10Q:credit-risk-operating-margin-mixed | identical |
| BusinessFactor | business_factor:AAPL:CY2024:10K:a58070d698ca02 | identical |

Full payloads are byte-identical after normalizing only release-root/shard-path strings (same payload keys, same evidence/quality/routing section counts, same quote text and support depth). **5/5 trace parity.**

## 5. Chain diff (informational, not gated)

Whole index: 87,502 → 87,729 (+227, +0.26%). By link_type: similar_topic 52,403→52,441 (+38), shared_topic 32,091→32,214 (+123), shared_factor 2,910→2,985 (+75), shared_entity 98→89 (-9).

Sample of 3 tickers (NVDA, AAPL, WEC): 1,494→1,495 links; 1,423 common, 72 new in v2, 71 gone, 17/1,423 common links with weight changes (e.g., CEG→WEC revenue_growth_six_months 0.7071→0.5774; weights look like 1/sqrt(n) normalizations shifting with document counts). Deltas are a mix of additive new similar_topic/shared_topic edges and neighbor-set churn — consistent with corpus growth (34 new filings) plus rebuild jitter, not a systematic weight recalculation. Not gated.

## 6. Sizes

| artifact | prod | v2 | delta |
|---|---:|---:|---:|
| shards (indexes/companies) | 218 GB | 132 GB | -39% |
| global spine | 47 GB | 37 GB | -21% |
| release total | 305 GB | 210 GB | -31% |

## 7. Gate verdict

| criterion | result |
|---|---|
| Gold gates green | YES (21/21; but gold source_manifest_hash does not bind this release — see §2) |
| All plans byte-parity on object_id sets | NO — 11/12; NVDA gold plan has a 1-unit evidence selection swap on a stable document |
| Answerability unchanged | YES (all 12 identical, incl. reason codes and strong_claim_allowed) |
| Structural checks hold | NO — locator SupportLink = 1,306,151 ≠ 0 (118/356 shards built with SupportLink still in `objects`) |

**GATE VERDICT: FAIL** (structural SupportLink invariant violated; one gold plan not byte-parity).

Recommended remediation before re-gate: rebuild the 118 dirty company shards with the v3 code path (or invalidate the company/fragment caches that produced them), re-assemble the spine + router sidecar/coherence, then re-run this harness (all scripts persisted in `/tmp/a5_parity/`). Re-author or re-bind the gold `source_release.source_manifest_hash` to the rebuilt release.

---

# ROUND 2 — Parity gate re-run against the clean rebuild

- Date: 2026-08-30/31
- Candidate: `~/krw-ontology-data/releases/v2-dev/dev/20260830_193811` (9,199,971 objects, 356 shards, spine-projection/v7, company-shard/v3)
- Baseline: unchanged (`~/krw-ontology-data/releases/prod/current -> 20260813_121609`, 18,392,007 objects)
- Root-cause fix under test: commit a1589dc ("rejected SupportLink rows re-entered shard objects via rejected_objects indexing"), caches purged, full clean release rebuilt. Repo at feat/observation-v3-bundle (a1589dc + 846c050); prod code unchanged (main @ 44a1484). Same serving pairing as round 1 (prod release×prod code vs v2 release×v2 code, in-process MCP path).
- Harness: reused `/tmp/a5_parity/` (same 12 plans, same runner/compare/rank_probe; round-1 outputs preserved in `out_prod_round1/`, `out_v2_round1/`; `compare.py` V2_SPINE repointed to 20260830_193811). Both releases opened read-only (`mode=ro&immutable=1`). Nothing under `releases/prod` was written.

## ROUND 2 GATE VERDICT: **PASS**

## R2.1 Structural — clean

- `verify/release_verify.json`: `ok: true`, `errors: []`, `file_count: 361`.
- Spine locator SupportLink count: **0** (prod: 9,386,337). Round-1 invariant violation fully resolved.
- All 118 previously-dirty shards re-checked directly: 0 SupportLink rows in `objects`; full sweep of all 356 shards: 0 violations. Round-1 worst cases now clean and carrying the derived table (GRMN: 0 in `objects`, 49,082 `support_links` rows; WEC: 0, 150,488).
- Total = 9,199,971 = 18,392,007 − 9,386,337 (SupportLink fully demoted) + 194,301 (corpus growth) — exactly the round-1 "expected-v3" figure.
- Object-type distribution: SupportLink −9,386,337 exactly; every non-SupportLink type count byte-matches round-1's v2 numbers (XBRLFact 2,471,827; EvidenceQuote 945,608; ResearchClaim 930,898; SourceLocation/Span 789,513; LanguageSignal 614,051; BusinessFactor 546,228; AssumptionCandidate 474,470; EntityMention 465,520; ExternalFactorExposure 336,783; MetricObservation 50,656; …). I.e. the clean rebuild changed ONLY the SupportLink demotion vs round 1's dirty build — the rebuild is otherwise deterministic.
- Catalog diff: +34 documents, 0 removed (11× 10-K, 22× 10-Q, 1× COMPANY for new ticker AA; all CY2026+), matching the +194,301 growth.

## R2.2 Gold binding re-author + gates

Binding updated in BOTH router gold files (they must stay equal per `test_checked_in_router_contracts_are_strict_and_versioned`):

| field | old | new |
|---|---|---|
| `source_release.release_id` (router_gold_v2.json, router_planned_gold_v2.json) | 20260711_121636 | **20260830_193811** |
| `source_release.source_manifest_hash` (both) | sha256:61846e490ade39983384a10a48c250b4fcffe6696deea05c6192da0409c3c22e | **sha256:5c2f0c14cc86c0bc825c9440f9c741f20fc5da73fe8b6c33491b64b565edc71c** |

- The new hash is the candidate spine's own `source_manifest_hash` (identical to round-1's v2 build — the fix changed shards, not source artifacts; the source corpus is the same). `_validate_gold_release` enforces exactly this hash; release_id is allowed to differ by design.
- Question set untouched: diff is the 4 binding lines only; no release-derived expected counts exist in these files (numerics are authoring parameters: max_candidate_count=20, limit_tickers=20, limit_results=12).
- Live-binding proof: both files validate against the candidate spine metadata; the old 61846e binding is rejected (`source_manifest_hash mismatch`).
- `uv run --extra dev pytest tests/unit/test_router_benchmark_gate.py -v` → **21 passed** (incl. the checked-in-contract test validating the rebound files).
- Committed: `test: rebind router planned gold to release 20260830_193811` (benchmarks/router_gold_v2.json + benchmarks/router_planned_gold_v2.json). Note: `router_agent_plan_e2e_ko_v1.json` and `agent_sdk_answer_gold_v1.json` still bind 61846e/20260711; they are outside this gate's test scope and were intentionally left for their own suites' re-acceptance.

## R2.3 Parity harness (same 12 plans as round 1)

| plan | category | verdict | object_ids prod→v2 | answerability | clause_coverage | computed_values |
|---|---|---|---|---|---|---|
| router_plan_aapl_regulation_supply | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| router_plan_nvda_geopolitics_regulators | qualitative (gold) | GROWTH | 12/12, 1-unit swap | partial=partial (identical) | china clause 2→1 evidence ids (status covered) | identical |
| router_plan_meta_privacy_energy | qualitative (gold) | PASS | 12/12 identical | answerable=answerable | identical | identical |
| router_plan_wmt_cost_pharmacy | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| router_plan_jpm_rates_loans | qualitative (gold) | PASS | 12/12 identical | partial=partial | identical | identical |
| metric_aapl_revenue_yoy | metric | PASS | 12/12 identical | answerable=answerable | identical | identical |
| metric_msft_income_margin | metric | PASS | 12/12 identical | answerable=answerable | identical | identical |
| metric_wmt_eps_fcf | metric | PASS | 12/12 identical | partial=partial | identical | identical |
| compare_aapl_msft_revenue | comparison | PASS | 12/12 identical | partial=partial | identical | identical |
| compare_cost_hd_margin_growth | comparison | PASS | 12/12 identical | partial=partial | identical | identical |
| chain_mck_slb_working_capital | chain-adjacent | PASS | 12/12 identical | partial=partial | identical | identical |
| chain_aig_hon_estimate_risk | chain-adjacent | PASS | 12/12 identical | partial=partial | identical | identical |

**11/12 identical, 1 growth-attributed, 0 unattributed.** Answerability identical on all 12 (status, reason codes, strong_claim_allowed); computed_values identical on all 12; match_modes all strict both sides; zero SupportLink objects in any evidence unit.

### R2.3.1 The NVDA plan diff — root cause (round-1 attribution corrected)

The diff reproduces byte-identically to round 1 (missing `claim:NVDA:CY2024:10K:chinese-regulators-have-inquired…`, extra `quote:NVDA:CY2024:10K:item1a_00:0105:001`), but on the clean release — so round 1's mechanism ("ranking drift emanating from dirty shards", via a GRMN quote at rank 2) is disproven. Direct probes:

- Ticker routing: GRMN is resolved in BOTH releases' top-20 (position 15); merged lists differ only at the tail (prod has PRU, v2 has AON — v2's AON rows never reach any final evidence set).
- Taiwan clause batch (`query_planned_batch_with_diagnostics`, limit 50): ordering byte-identical across releases, GRMN at ranks 9/20/31/41 in both — the round-1 "GRMN rank 2" was downstream budget reshuffling, not a GRMN score change. GRMN shard FTS corpora are identical (13,055 object_search_text/object_fts ids, zero set difference).
- China clause batch: prod returns 12 rows (all NVDA); v2 returns 16 — the 4 extra rows are all from **NVDA:10Q:CY2026Q3, one of the 34 added documents** (3,901 NVDA CY2026Q3 objects in v2, 0 in prod; all four verified absent from prod's locator). They enter at china ranks 10–12/15, pushing the CY2024 claim from china rank 11 (prod) to 14 (v2) and out of the 12-unit budget; the swapped-in quote sits at china rank 9 in BOTH releases (present in both candidate pools; the budget shift decides which survives).

Classification: **corpus_growth_new_document (ticker NVDA, new doc NVDA:10Q:CY2026Q3)** — consistent with the gate rule that the 34 new docs can only affect plans touching their tickers. The object-level auto-classifier in `compare.py` labels the two swapped objects "stable_document" because the swapped objects belong to a stable doc; the causal new-doc rows sit in the candidate list, not the final set. Answerability status/reason codes, strong_claim_allowed, and computed_values are unchanged; the clause remains covered (2→1 evidence ids).

## R2.4 Trace parity spot-check — 5/5 identical

Same 5 object ids/types as round 1 (note: round 1's `compare.py` had rewritten `trace_ids.json` into a dict form the runner misreads; restored to list form — initial "identical not_found on both sides" was a harness artifact, re-run):

| type | object_id | result |
|---|---|---|
| EvidenceQuote | quote:AAPL:CY2024:10K:item8_00:0099:001 | identical (quote_text 167 chars) |
| ResearchClaim | claim:AIG:CY2024:10K:aig-s-accounting-estimates-rely-on… | identical (claim_text present, evidence depth 4) |
| MetricObservation | metric_observation:AAPL:CY2023:10K:6fbcca65aa1edb | identical (evidence depth 5) |
| ExternalFactorExposure | external_factor_exposure:FITB:CY2026Q2:10Q:credit-risk-operating-margin-mixed | identical |
| BusinessFactor | business_factor:AAPL:CY2024:10K:a58070d698ca02 | identical |

Same payload keys, evidence/quality/routing/document/locator section counts (4-5 evidence, 9 document, 26 locator rows), and text lengths on both releases. **5/5 trace parity.**

## R2.5 Chain diff (informational)

Whole index: prod 87,502 → **87,729** (+227) — byte-identical total to round-1's v2 build (delta vs round 1: 0). By link_type: similar_topic 52,441 (+38), shared_topic 32,214 (+123), shared_factor 2,985 (+75), shared_entity 89 (−9). Unchanged from round 1; consistent with the same 34-doc corpus growth.

## R2.6 Round-2 gate matrix

| criterion | result |
|---|---|
| Gold gates green post-binding-update | YES — 21/21; binding validates against candidate spine; old binding rejected |
| All plans identical-or-growth-attributed | YES — 11/12 identical; 1 growth-attributed (NVDA new 10-Q); 0 unattributed |
| Answerability unchanged | YES (all 12 identical, incl. reason codes and strong_claim_allowed) |
| Structural checks hold | YES — ok:true; locator SupportLink = 0; 0/356 shards dirty; totals reconcile exactly |

**ROUND 2 GATE VERDICT: PASS**

## R2.7 Sizes

| artifact | prod | v2 (clean) | delta |
|---|---:|---:|---:|
| shards (indexes/companies) | 218 GB | 134 GB | -38% |
| release total | 305 GB | 210 GB | -31% |

Materially identical to round 1's v2 build (-39%/-31%): the SupportLink fix changed shard contents, not the size profile.
