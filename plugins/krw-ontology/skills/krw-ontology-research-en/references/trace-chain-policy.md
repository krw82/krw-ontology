# Trace And Chain Policy

`trace` and `chain` are different tools.

```text
trace = evidence lineage
chain = connected business meaning
```

## Use trace when

```text
exact value matters
exact date matters
contract term matters
metric lineage matters
strong claim needs support
direct exposure claim needs verification
```

## Use chain when

```text
mechanism matters
risk -> financial path matters
business activity -> metric/channel link matters
temporal context matters
semantic neighbors are useful
```

## Limits

Trace/chain only selected roots. Do not trace or chain every candidate.

Use `ResearchState.recommended_actions` and the source IDs in the relevant
`evidence_units` when a load-bearing claim needs stronger lineage or mechanism
support. Continue only while that action can change clause coverage,
directness, calculation support, or answer correctness; do not apply a fixed
root count.

Keep chain/trace internals out of normal answers.
