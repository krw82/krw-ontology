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

Fast mode normally uses no trace/chain. Standard mode normally uses 1-2 total selected trace/chain calls. Deep mode can use more, but still no chain-all behavior.

Keep chain/trace internals out of normal answers.
