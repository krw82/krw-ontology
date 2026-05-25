# Artifact Contract Reference

This reference is for pipeline, export, audit, and canonical artifact discussions. It is not a normal web-chat answer contract.

## Core rule

Canonical JSONL artifacts are the source of truth. `agent_index.sqlite` is a rebuildable read-optimized serving cache. MCP tools are read-only retrieval and trace interfaces over the serving cache.

## Normal web chat boundary

Do not expose artifact names, schema versions, registry fields, support-link IDs, validation counters, rejected object details, or index internals in normal answers.

Use this reference only when the user asks about:

```text
artifact contract
canonical JSONL
index rebuild
schema migration
validation reports
registry snapshots
support links
quality gates
audit/export payloads
```

## Strong evidence rule

Final strong claims should be grounded in traceable filing evidence or metric lineage. Serving/index optimizations may route retrieval but must not become the source of truth.
