# Display Plan Contract

This contract is optional and applies only when structured frontend display planning is explicitly requested.

Default web chat returns Korean Markdown and does not use this contract.

## Core rule

Display planning arranges already-written research content. It must not create, rewrite, round, summarize, or add facts.

Good:

```json
{
  "block_type": "metric_grid",
  "source_ids": ["m1", "m2"],
  "title": "핵심 숫자"
}
```

Bad:

```json
{
  "block_type": "metric_grid",
  "metrics": [{ "label": "Revenue", "value": "$10B" }]
}
```

## Output envelope

```json
{
  "display_plan_version": "krw-display-plan/v1",
  "source_synthesis_version": "krw-research-synthesis/v1",
  "display_plan": [
    {
      "block_id": "b1",
      "block_type": "markdown",
      "source_ids": ["p1"],
      "title": null,
      "options": {}
    }
  ],
  "quality": {
    "overall": "medium",
    "warnings": []
  }
}
```

## Allowed block types

```text
markdown
metric_grid
period_delta
business_segments
premise_card
impact_channels
risk_driver_map
timeline
comparison_table
evidence_chain
caveat_list
glossary
```

## Validation

```text
all source_ids exist
all required content is referenced
block type matches source unit type
no factual content fields inside blocks
frontend can fall back to markdown
```
