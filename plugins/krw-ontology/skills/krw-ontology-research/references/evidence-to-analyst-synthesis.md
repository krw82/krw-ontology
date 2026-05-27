# Evidence to Analyst Synthesis

This reference preserves the old skill's core discipline while keeping normal web-chat answers clean.

## Principle

KRW ontology evidence is not the final answer. It is the research state the analyst uses to write the final answer.

```text
MCP output = evidence workbench
AI agent = analyst judgment and final Korean explanation
Final answer = business interpretation grounded in filing evidence
```

Do not copy ontology internals into user-facing prose.

## Candidate route vs evidence

Projection and lookup tables are routing aids.

```text
metric_lookup
metric_dimension_lookup
company_dimension_catalog
exposure_lookup
agreement_lookup
event_lookup
factor_lookup
company_topic_index
object_search_text
```

These can identify likely objects, metrics, topics, or companies. They are not by themselves enough for strong claims.

Strong claims need at least one of:

```text
traced filing evidence
metric lineage
direct source object support
consistent cross-object support with caveat
```

## Translation rule

Convert internal evidence into analyst language.

```text
Internal: ExternalFactorExposure exists for input_cost_pressure.
User-facing: The company flags input-cost pressure as a margin risk.

Internal: directness = related_context.
User-facing: This supports a related pressure channel, not a directly quantified company impact.

Internal: specificity_score = 0.78.
User-facing: The disclosure is specific enough to support the direction of the risk, but not a precise quantified impact.

Internal: candidate route from company_topic_index.
User-facing: The company disclosures point to the topic as a recurring business issue.
```

## Sector/global answers

For sector, macro, discovery, or multi-company basket questions, do not summarize object inventory.

Use this synthesis shape:

```text
1. conclusion
2. common macro signal
3. company-by-company evidence signal
4. financial channel
5. interpretation and caveat
```

Example:

```text
Bad:
COST has ExternalFactorExposure objects for trade_down and input cost pressure.

Good:
Costco's disclosures point to a value-oriented consumer and price investment strategy: demand can hold up, but gross margin can be pressured when the company absorbs part of input-cost inflation to preserve traffic.
```

## What to hide

Never expose these in normal answers:

```text
object type names
ontology layer names
specificity scores
directness labels
traceable/non-traceable labels
candidate route mechanics
lookup/index/table names
raw object IDs
tool routing
coverage diagnostics
```

Use them only for debug, audit, raw evidence, structured handoff, or developer-facing output.
