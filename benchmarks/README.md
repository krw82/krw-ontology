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
