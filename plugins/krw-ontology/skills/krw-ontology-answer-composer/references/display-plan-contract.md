# KRW Ontology Display Plan Contract

This contract defines pass 2 of the product workflow.

```text
ResearchSynthesis.canonical_answer -> DisplayPlan -> frontend renderer
```

The display planner does not write research content. It decides how already-written canonical answer units should be grouped and rendered.

## Core Rule

`canonical_answer.units` is the source of truth. `display_plan` may reference units, order units, group units, and choose block types. It must not introduce new claims, numbers, caveats, or evidence.

Good:

```json
{
  "block_type": "metric_grid",
  "title": "핵심 숫자",
  "source_ids": ["m1", "m2"]
}
```

Bad:

```json
{
  "block_type": "metric_grid",
  "metrics": [
    { "label": "Revenue", "value": "$10B" }
  ]
}
```

The bad example lets the planner rewrite facts. The frontend should pull all labels, values, periods, text, caveats, and evidence refs from `canonical_answer.units`.

## Final Response Envelope

When the runtime supports structured output, return this shape:

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

Rules:

- `display_plan` is required and ordered.
- Every block must have at least one `source_id`.
- Every `source_id` must exist in `ResearchSynthesis.canonical_answer.units`.
- The planner may create `block_id`, choose `block_type`, provide a short non-factual `title`, and set renderer hints in `options`.
- The planner must not include display fields such as `text`, `content`, `body`, `value`, `metrics`, `rows`, `items`, `events`, `segments`, `channels`, or `evidence_refs`.
- If unsure, use `markdown` and preserve canonical order.

## Compact JSON Schema

Use a compact schema with Claude Agent SDK structured output. Let the frontend perform detailed validation against the canonical units.

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["display_plan_version", "source_synthesis_version", "display_plan", "quality"],
  "properties": {
    "display_plan_version": { "const": "krw-display-plan/v1" },
    "source_synthesis_version": { "type": "string" },
    "display_plan": {
      "type": "array",
      "minItems": 1,
      "maxItems": 24,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["block_id", "block_type", "source_ids"],
        "properties": {
          "block_id": { "type": "string" },
          "block_type": {
            "enum": [
              "markdown",
              "metric_grid",
              "period_delta",
              "business_segments",
              "premise_card",
              "impact_channels",
              "risk_driver_map",
              "timeline",
              "comparison_table",
              "evidence_chain",
              "caveat_list",
              "glossary"
            ]
          },
          "source_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": { "type": "string" }
          },
          "title": {
            "type": ["string", "null"]
          },
          "options": {
            "type": "object",
            "additionalProperties": true
          }
        }
      }
    },
    "quality": {
      "type": "object",
      "required": ["overall", "warnings"],
      "additionalProperties": true,
      "properties": {
        "overall": { "enum": ["high", "medium", "low"] },
        "warnings": { "type": "array", "items": { "type": "string" } }
      }
    }
  }
}
```

## Allowed Block Types

Choose blocks by readability, not habit.

| Block type | Best source unit types | Use when |
| --- | --- | --- |
| `markdown` | `heading`, `paragraph`, `evidence_note` | Natural explanation, thesis, nuance, judgment |
| `metric_grid` | `metric` | Several verified numbers are easier to scan as cards/table |
| `period_delta` | `period_delta` | Verified period-to-period changes |
| `business_segments` | `business_segment` | Company overview or business model structure |
| `premise_card` | `premise` | User-provided or external market premise |
| `impact_channels` | `impact_channel` | Factor -> financial channel -> mechanism |
| `risk_driver_map` | `risk_item`, `driver_item`, `headwind_item`, `watch_item` | Risks, drivers, headwinds, offsets, watch items |
| `timeline` | `timeline_event` | COD/FID, maturities, project dates, event sequence |
| `comparison_table` | `comparison_row` | Ticker, segment, scenario, or period comparison |
| `evidence_chain` | `evidence_note` | User asked why, trace, or support path |
| `caveat_list` | `caveat` | Limitations or warnings that change interpretation |
| `glossary` | `glossary_term` | Short definitions for terms used in the answer |

The frontend may render any block as plain markdown when source units or block support are incomplete.

## Titles

`title` is a display label only. It may say things like:

- `핵심 숫자`
- `사업 구조`
- `영향 경로`
- `주의할 점`

It must not contain a new fact:

- Bad: `AWS operating income rose to $45.6B`
- Good: `AWS 수익성`

## Options

`options` is for renderer hints only. Examples:

```json
{
  "density": "compact",
  "emphasis": "primary",
  "group_by": "kind",
  "mobile_priority": "high"
}
```

Options must not carry claims, values, or hidden prose.

## Planning Procedure

1. Read `canonical_answer.default_order` and preserve it unless a different grouping clearly improves readability.
2. Start with a markdown block for the opening thesis or explanation.
3. Group adjacent compatible units only when a structured block helps.
4. Keep caveats near the units they qualify.
5. Use source unit IDs exactly as provided.
6. If a unit is marked `importance="required"`, include it in at least one block.
7. If a unit is marked `display_policy="hidden_by_default"`, exclude it unless the user asked for audit/debug evidence.
8. If a source unit's type does not fit a rich block, render it as `markdown`.

## Validation Rules

The frontend or product runner should validate:

- all `source_ids` exist
- no block contains forbidden content fields
- all required canonical units are referenced
- block type is compatible with source unit types
- fallback to canonical order if validation fails

## What Not To Do

- Do not produce `answer_blocks`.
- Do not rewrite canonical text.
- Do not invent or reformat numbers.
- Do not add caveats not present in canonical units.
- Do not expose raw ontology IDs or quote text.
- Do not output HTML, React, CSS, JavaScript, or layout code.
- Do not copy source HTML/CSS/JavaScript from `html-effectiveness-artifacts`.
