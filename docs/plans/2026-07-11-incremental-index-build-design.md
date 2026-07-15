# Incremental index build design

## Goal and safety boundary

The release builder must stop treating every forced release as a completely new index while preserving deterministic output and the final cross-layer correctness gate. The ontology schema is unchanged. Cache reuse is permitted only when a content-addressed key covers every semantic input for that layer. A missing, mismatched, or changed key always falls back to the existing complete deterministic builder. No partial SQL patching of canonical shared objects or cross-company chain links is allowed.

Immutable SQLite cache entries receive a stat-bound seal after a successful deep verification. A later build may trust that seal only while device, inode, size, mtime, and ctime are unchanged. The first encounter with an old unsealed cache performs the existing deep verification and writes a seal. Those one-time migrations run concurrently within the global worker budget. Release artifacts are still bound by SHA-256 and the final release verifier checks their cross-layer relationships.

## Content-addressed build graph

The build graph is:

`source manifest -> artifact fragment -> company shard -> spine fragment -> global spine -> router sidecar`

The in-process source manifest is the authoritative result of the immediately preceding byte-hashing stage, so planning consumes its artifact identities and hashes without rereading the same files. Artifact and company cache probes use immutable seals. Company and spine cache clones rebind only release-local paths and metadata in SQLite transactions, then inherit the source cache's deep verification evidence. Any structural or binding mismatch quarantines the cache and rebuilds that node.

The Global Spine cache key includes the ordered ticker/fragment keys, source manifest hash, link-generation mode, schema and builder versions, projection and semantic-identity versions, metric dictionary, and company/source-artifact contracts. An exact hit clones and rebinds the verified Global Spine. A miss executes semantic preflight and the full deterministic merge, then publishes the sealed result. A compatible current release can bootstrap the new cache once, which avoids another full merge after deploying this optimization.

## Verification and performance gates

Each newly generated cache entry is deeply checked before publication. Reused immutable entries are not rescanned during planning. The shard manifest computes each release shard SHA-256 once and records it against the unchanged file identity, allowing the final verifier to reuse that digest. The final release verification remains the sole cross-layer gate and promotion reuses the same in-process verification report.

Tests cover manifest hash reuse, seal-based probe behavior, automatic fallback after cache mutation, exact Global Spine cache reuse, release-ID rebinding, and result-count equality. Performance evaluation compares cold and warm builds with identical semantic inputs. The primary budgets are build-plan/cache-probe time, Global Spine merge avoidance on exact hits, total warm build time, and zero result or verification regressions.
