# Router release benchmark

The router gate measures the production sidecar contract without changing the ontology schema,
agent prompt, model, effort, or turn budget.

## Measurement contract

- `connection_cold`: every sample opens, queries, and closes a new `RouterSidecar` connection.
  The operating-system page cache is not flushed, so this is not a disk-cold benchmark.
- `query_warm`: every gold query gets its own new persistent connection. The identical query runs
  once without timing, followed by the requested timed iterations.
- The normalized candidate ranking must be identical across every cold sample, the warmup, and
  every warm sample. A parity failure aborts the run.
- Direct gold is an English retrieval-query contract. The agent authors these queries before the
  router sees them. Raw Korean user questions live in
  `router_agent_plan_e2e_ko_v1.json` and must be evaluated through the Claude Agent SDK planning
  path, not sent directly to the sidecar and not translated by hardcoded rules.
- Planned cases call the production `OntologySpineRouter.route_planned_tickers` implementation.
  The benchmark does not maintain a second RRF implementation.

The benchmark validates the sidecar internally and binds it to the global spine with immutable
metadata (`release_id`, source manifest hash, source creation time, and schema version). It does
not rehash the 59 GiB global spine. When the matching deep-verification seal already contains a
SHA-256, the benchmark reuses that cached digest and compares it with sidecar metadata. A trusted
build-manifest digest can also be supplied with `--expected-global-spine-sha256`; if both sources
exist, they must agree.

## Capture an accepted baseline

Baseline acceptance requires the versioned bootstrap budget. It contains absolute quality,
latency, source-binding, layout, rank, distractor, and planned-clause checks but no relative
comparison. A report that fails any check is written for diagnosis, exits with status `2`, and is
not accepted.

```bash
uv run python scripts/benchmark_router_sidecar.py \
  --global-spine /path/to/accepted/indexes/global_spine.sqlite \
  --sidecar /path/to/accepted/indexes/router_sidecar.sqlite \
  --queries benchmarks/router_gold_v2.json \
  --planned-queries benchmarks/router_planned_gold_v2.json \
  --limit 20 \
  --cold-iterations 3 \
  --iterations 7 \
  --budget benchmarks/router_bootstrap_budget_v2.json \
  --output /path/to/accepted/verify/router-bootstrap-report.json \
  --accept-baseline /path/to/benchmarks/router-accepted-baseline-v4.json
```

## Gate a candidate

Run on the same CPU model, logical core count, OS, Python, and SQLite versions as the accepted
baseline. Input hashes, measurement protocol, iteration counts, and diagnostic flags must also
match. Exit status `2` means at least one absolute or relative check failed.

```bash
uv run python scripts/benchmark_router_sidecar.py \
  --global-spine /path/to/candidate/indexes/global_spine.sqlite \
  --sidecar /path/to/candidate/indexes/router_sidecar.sqlite \
  --queries benchmarks/router_gold_v2.json \
  --planned-queries benchmarks/router_planned_gold_v2.json \
  --limit 20 \
  --cold-iterations 3 \
  --iterations 7 \
  --baseline /path/to/benchmarks/router-accepted-baseline-v4.json \
  --budget benchmarks/router_release_budget_v2.json \
  --output /path/to/candidate/verify/router-benchmark-v4.json
```

Use `--include-score-breakdown` on both baseline and candidate when lexical and fusion diagnostics
are required. Use `--include-ablations` on both runs to produce direct and planned aggregate
quality/latency for the legacy raw-primary scale (`primary_rrf_scale = rrf_k + 1`). Diagnostic
flags are part of baseline compatibility.

Do not accept a new baseline merely to clear a regression. Inspect failed gold rows, expected rank,
hit@1/hit@5, MRR, distractor order, candidate count, required-clause joint coverage, lexical
diagnostics, and fusion diagnostics first.

This is a ticker-routing gate. Joint required-clause coverage verifies that one expected ticker is
retrievable for every required planning clause, but it does not prove ontology `chain` traversal or
evidence retrieval correctness. Chain path quality needs its own MCP-level gold with expected node,
edge, depth, and evidence-anchor assertions; do not label the index chain-friendly from this report
alone.

## One-time candidate qualification

After a candidate finishes building, run the Router gate, real MCP
`SearchPlan -> ResearchState -> trace -> chain` gate once:

```bash
uv run python scripts/qualify_release_candidate.py \
  --release-root /path/to/releases/dev/<candidate-id>
```

The command never builds, polls, promotes, or changes `current`. It writes the bound reports under
the candidate's `verify/` directory. Router failure prevents the MCP check from running. The
external Agent SDK/provider answer run is excluded from the default release gate; pass
`--include-agent-sdk` only when provider-level A/B is explicitly wanted. An Agent SDK timeout stops
after the first isolated case rather than repeating the same timeout for every gold row.

The deterministic MCP gate requires every planned case to preserve the expected ticker, return
non-missing required-clause coverage, retain a traceable evidence root, and preserve the compound
`(ticker, object_id)` identity through both trace and chain. It also enforces the ResearchState model
and wire-size bounds and a query-context p95 latency ceiling.

The Agent SDK benchmark uses an ephemeral plugin copy. Every plugin file, including all skill
documents, remains byte-identical except `.mcp.json`, which is retargeted to the candidate. It does
not override the model, effort, max turns, ontology tool surface, or Claude Code system-prompt
preset. Each case runs in a separate process group with a hard timeout so the Claude CLI and stdio
MCP server cannot survive a stalled case.

To capture a same-runtime baseline explicitly:

```bash
uv run python scripts/benchmark_agent_sdk_plugin.py \
  --release-root /path/to/current/dev/release \
  --output /path/to/agent-sdk-baseline.json

uv run python scripts/benchmark_agent_sdk_plugin.py \
  --release-root /path/to/candidate \
  --baseline /path/to/agent-sdk-baseline.json \
  --output /path/to/candidate/verify/agent-sdk-answer-v1.json
```

Automatic answer checks are deliberately conservative: a non-empty answer, the expected company,
at least one ontology MCP tool, and no internal implementation leakage. The report also preserves
the complete answers and review focus for blind human comparison; an automatic pass is not labelled
as proof of prose quality.

## Planned-contract boundary

Planned gold is validated as the real `SearchPlan` contract. The benchmark reuses the MCP path's
public `clause_routing_query` adapter and then calls
`OntologySpineRouter.route_planned_tickers`, so it does not duplicate either clause conversion or
planned RRF. The shared adapter lives at
`src/krw_ontology/mcp_server/tools.py::clause_routing_query`.

## Guru retrieval gate

The Guru retrieval gate remains separate and reuses its checked-in consultation cases:

```bash
uv run krw-ontology guru eval-quality \
  --root /path/to/accepted-guru-release \
  --accept-baseline-path /path/to/benchmarks/guru-accepted-baseline.json

uv run krw-ontology guru eval-quality \
  --root /path/to/candidate-guru-release \
  --baseline-path /path/to/benchmarks/guru-accepted-baseline.json \
  --budget-path benchmarks/guru_release_budget_v1.json \
  --output-path /path/to/candidate-guru-release/reports/guru-quality-gate.json
```

## Evidence-gold gate (v1)

The evidence-gold gate measures end-to-end retrieval quality through the real MCP
`query_context_tool` path. Every case in `benchmarks/evidence_gold_v1.json`
(format `krw-ontology-evidence-gold/v1`, bound to release `20260830_193811`)
carries a validated `SearchPlan` plus expected evidence anchors (object ids from
that release's shards, or text fragments, or `expect_not_disclosed` forbidden
fragments). The harness runs each plan headlessly, grades the returned
`evidence_units` against the anchors, and reports `pass_rate`, `mean_recall`,
and `zero_hit_rate` overall and per stratum. Strata are `template` and
`dimensioned` (deterministic samples from the shard manifest, 2 cases per
ticker across all 356 tickers) plus the curated `vocabulary_mismatch`,
`fiscal_offset`, `multi_period`, `multi_span`, and `not_disclosed` cases.
`mean_recall` is the fraction of required anchors retrieved; `zero_hit_rate` is
the fraction of cases returning no evidence units at all.

Both runs read the runtime release from the `KRW_ONTOLOGY_*` environment
(mirroring `scripts/benchmark_mcp_candidate.py`); nothing is rebuilt and the
release is treated as read-only. Capture the committed baseline numbers first:

```bash
RELEASE=/path/to/releases/v2-dev/dev/20260830_193811
KRW_ONTOLOGY_ENV=dev \
KRW_ONTOLOGY_RELEASE_ROOT=$RELEASE \
KRW_ONTOLOGY_ROOT=$RELEASE \
KRW_ONTOLOGY_MANIFEST_PATH=$RELEASE/manifest.json \
KRW_ONTOLOGY_GLOBAL_SPINE_PATH=$RELEASE/indexes/global_spine.sqlite \
KRW_MCP_EXPECTED_CONTRACT_VERSION=krw-ontology-mcp/v2 \
uv run python scripts/benchmark_evidence_gold.py \
  --gold benchmarks/evidence_gold_v1.json --label baseline
```

Then gate a candidate against the accepted baseline report (same gold file,
candidate release root):

```bash
RELEASE=/path/to/releases/v2-dev/dev/<candidate-id>
KRW_ONTOLOGY_ENV=dev \
KRW_ONTOLOGY_RELEASE_ROOT=$RELEASE \
KRW_ONTOLOGY_ROOT=$RELEASE \
KRW_ONTOLOGY_MANIFEST_PATH=$RELEASE/manifest.json \
KRW_ONTOLOGY_GLOBAL_SPINE_PATH=$RELEASE/indexes/global_spine.sqlite \
KRW_MCP_EXPECTED_CONTRACT_VERSION=krw-ontology-mcp/v2 \
uv run python scripts/benchmark_evidence_gold.py \
  --gold benchmarks/evidence_gold_v1.json --label candidate \
  --baseline benchmarks/reports/evidence_gold_baseline_20260908.json
```

Exit status `2` means at least one metric regressed: overall or per-stratum
`pass_rate`/`mean_recall` dropped below the baseline value (minus `--tolerance`).
A stratum present in the baseline but absent from the candidate report compares
against the baseline value itself, so shrinking the stratum set cannot hide a
regression. Reports are written under `benchmarks/reports/`; only drops gate,
improvements never fail the run. Regenerate the deterministic template half
with `scripts/generate_evidence_gold_templates.py generate --release-root
<release> --per-ticker 2` (same seed reproduces the same cases byte-for-byte).

Baseline v1.0 (2026-09-08, release `20260830_193811`, 736 cases, ~58 s wall clock; kept for history):

| stratum | cases | pass_rate | mean_recall | zero_hit_rate |
| --- | --- | --- | --- | --- |
| overall | 736 | 0.8777 | 0.8798 | 0.0000 |
| curated | 24 | 0.4583 | 0.5208 | 0.0000 |
| dimensioned | 310 | 0.8935 | 0.8935 | 0.0000 |
| fiscal_offset | 4 | 0.7500 | 0.7500 | 0.0000 |
| multi_period | 4 | 0.0000 | 0.2500 | 0.0000 |
| multi_span | 4 | 0.7500 | 0.8750 | 0.0000 |
| not_disclosed | 4 | 1.0000 | 1.0000 | 0.0000 |
| template | 402 | 0.8905 | 0.8905 | 0.0000 |
| vocabulary_mismatch | 8 | 0.1250 | 0.1250 | 0.0000 |

Baseline v1.1 (same date/release/gold; the grader now treats bare `FY`/`CY`
annual labels as label twins — `FY2024` matches gold `CY2024` — removing a
measurement artifact where the anchored unit exposed the `FY` twin of the
shard's `CY` filing key; quarter-suffixed labels stay strict. 4/736 cases
recovered, all in `dimensioned`):

| stratum | cases | pass_rate | mean_recall | zero_hit_rate |
| --- | --- | --- | --- | --- |
| overall | 736 | 0.8832 | 0.8852 | 0.0000 |
| curated | 24 | 0.4583 | 0.5208 | 0.0000 |
| dimensioned | 310 | 0.9065 | 0.9065 | 0.0000 |
| fiscal_offset | 4 | 0.7500 | 0.7500 | 0.0000 |
| multi_period | 4 | 0.0000 | 0.2500 | 0.0000 |
| multi_span | 4 | 0.7500 | 0.8750 | 0.0000 |
| not_disclosed | 4 | 1.0000 | 1.0000 | 0.0000 |
| template | 402 | 0.8905 | 0.8905 | 0.0000 |
| vocabulary_mismatch | 8 | 0.1250 | 0.1250 | 0.0000 |

Candidate 2a (same date/release/gold; the deterministic similarity lane —
metric-dictionary alias expansion for metric-less clauses, so a retrieval
query naming an alias such as `top line` or `debt load` also runs the exact
metric channel for the canonical metric, plus per-requested-period metric
reservation at the fusion-window cut. Gate vs v1.1: exit 0, no regressions,
58 s wall clock):

| stratum | cases | pass_rate | mean_recall | zero_hit_rate |
| --- | --- | --- | --- | --- |
| overall | 736 | 0.8859 | 0.8879 | 0.0000 |
| curated | 24 | 0.5417 | 0.6042 | 0.0000 |
| dimensioned | 310 | 0.9065 | 0.9065 | 0.0000 |
| fiscal_offset | 4 | 0.7500 | 0.7500 | 0.0000 |
| multi_period | 4 | 0.0000 | 0.2500 | 0.0000 |
| multi_span | 4 | 0.7500 | 0.8750 | 0.0000 |
| not_disclosed | 4 | 1.0000 | 1.0000 | 0.0000 |
| template | 402 | 0.8905 | 0.8905 | 0.0000 |
| vocabulary_mismatch | 8 | 0.3750 | 0.3750 | 0.0000 |

Plan 2b (sqlite-vec dense lane) is DEFERRED. The multi_period residual is
a period-contract issue — `EvidenceUnit.period` exposes the filing period,
the harness adapter drops `metric_points`, and the grader's period filter
rejects comparative rows — that a dense lane cannot move. The true
dense-scope residual is 2 curated vocab cases (`meta_share_buybacks`: no
dictionary canonical for the phrase; `msft_bottom_line`: value-identity
dedupe keeps the newest filing's row). Revisit condition: after the three
classified deterministic residuals are addressed (period-contract fix,
buyback dictionary canonical, msft dedupe), if a meaningful residual
remains.

Baseline v1.1-regold (same date/release; the v1.1 retrieval code re-run on the
Task-7 reinforced gold, where multi_period historical items additionally
accept the latest filing's comparative-row provenance as any-of anchors —
produced by temporarily reverting the retrieval-path delta
`store.py`/`spine_router.py`/`mcp_server/tools.py` to the v1.1 commit and
restoring it afterwards; tree verified clean). Byte-identical to v1.1 on the
old gold per-case: the gold reinforcement alone moves nothing on the old
retrieval, so this report is the honest apples-to-apples baseline for the
2a+ gate:

| stratum | cases | pass_rate | mean_recall | zero_hit_rate |
| --- | --- | --- | --- | --- |
| overall | 736 | 0.8832 | 0.8852 | 0.0000 |
| curated | 24 | 0.4583 | 0.5208 | 0.0000 |
| dimensioned | 310 | 0.9065 | 0.9065 | 0.0000 |
| fiscal_offset | 4 | 0.7500 | 0.7500 | 0.0000 |
| multi_period | 4 | 0.0000 | 0.2500 | 0.0000 |
| multi_span | 4 | 0.7500 | 0.8750 | 0.0000 |
| not_disclosed | 4 | 1.0000 | 1.0000 | 0.0000 |
| template | 402 | 0.8905 | 0.8905 | 0.0000 |
| vocabulary_mismatch | 8 | 0.1250 | 0.1250 | 0.0000 |

Candidate 2a+ (same date/release/reinforced gold; adds the alias metric floor
for full-window single-period metric-less clauses, filing-bucket alignment
for metric-channel rows, and the comparative-provenance anchors above. Gate
vs v1.1-regold: exit 0, no regressions, 60 s wall clock):

| stratum | cases | pass_rate | mean_recall | zero_hit_rate |
| --- | --- | --- | --- | --- |
| overall | 736 | 0.8899 | 0.8920 | 0.0000 |
| curated | 24 | 0.6667 | 0.7292 | 0.0000 |
| dimensioned | 310 | 0.9065 | 0.9065 | 0.0000 |
| fiscal_offset | 4 | 0.7500 | 0.7500 | 0.0000 |
| multi_period | 4 | 0.0000 | 0.2500 | 0.0000 |
| multi_span | 4 | 0.7500 | 0.8750 | 0.0000 |
| not_disclosed | 4 | 1.0000 | 1.0000 | 0.0000 |
| template | 402 | 0.8905 | 0.8905 | 0.0000 |
| vocabulary_mismatch | 8 | 0.7500 | 0.7500 | 0.0000 |

Exactly 5 per-case flips vs v1.1-regold, all in `vocabulary_mismatch`
(`aapl_top_line`, `meta_debt_load` from 2a; `wmt_operating_profit`,
`amzn_profit_per_share`, `aapl_research_spending` from the alias floor);
every other stratum is byte-identical.

Post-2a+ 2b decision (binding rule: defer 2b only if BOTH strata reach
mean_recall >= 0.7): `vocabulary_mismatch` = **0.7500** (>= 0.7) but
`multi_period` = **0.2500** (< 0.7) — the mechanical rule would proceed,
but the standing decision is **2b is DEFERRED**. Scope from the
classified residual: the remaining `multi_period` misses are NOT a retrieval
gap — every anchored row (own-filing or comparative) is retrieved into the
clause window; the compiled `MetricObservation` evidence unit exposes the
filing period as its top-level `period` (the observation period lives only in
`metric_points`, which the harness adapter drops), so the grader's period
filter rejects historical-year items. A dense lane cannot move that stratum;
the fix is a compiler/grader period-contract change (separate deterministic
work). The true dense-lane residual is `curated_vocab_meta_share_buybacks`
(no dictionary alias for the phrase; text/claim anchors) plus
`curated_vocab_msft_bottom_line` (wrong filing bucket survives
`_query_metrics` value-identity dedupe, which keeps only the newest filing's
row for a repeated observation).

Gate candidates against the v1.1-regold baseline report (same reinforced
gold):

```bash
KRW_ONTOLOGY_RELEASE_ROOT=/path/to/releases/v2-dev/dev/<candidate-id> \
uv run python scripts/benchmark_evidence_gold.py \
  --gold benchmarks/evidence_gold_v1.json --label candidate \
  --baseline benchmarks/reports/evidence_gold_baseline_v1_1_regold_20260908.json
```

Read `vocabulary_mismatch` through `mean_recall`, not `zero_hit_rate`: the
colloquial-vocabulary plans deliberately omit the canonical metric, so the
full-text lane still returns qualitative units (assumptions, claims, quotes)
while the anchored `metric_observation` rows are never retrieved. The
similarity-lane work should move that stratum's `mean_recall` and `pass_rate`.
