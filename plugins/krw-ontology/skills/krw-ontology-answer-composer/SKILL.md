---
name: krw-ontology-answer-composer
description: Use after KRW ontology research synthesis is complete to create a display_plan that arranges canonical_answer units for frontend rendering. Use this for display planning only, not evidence retrieval or rewriting research content.
---

# KRW Ontology Answer Composer

Use this skill after `krw-ontology-research` has produced a complete `ResearchSynthesis` with `canonical_answer.units`. This is pass 2 in the product workflow.

Despite the name, this skill is a display planner. It does not write the research answer. It chooses how already-written canonical units should be grouped and rendered.

## Boundary

This skill owns:

- display block selection
- block ordering
- grouping by `source_ids`
- short non-factual display titles
- renderer hints in `options`
- validation warnings about missing or incompatible source units

This skill does not own:

- broad ontology search
- trace or chain discovery
- numeric validation
- customer-facing research prose
- rewriting canonical text
- adding new claims, numbers, caveats, or evidence
- deciding whether evidence is accepted or rejected
- frontend React components
- HTML, CSS, JavaScript, PDF, or deck layout
- database migrations or Supabase storage

If the provided synthesis lacks `canonical_answer.units` or material required content, say what is missing and request a new research pass. Do not fill gaps yourself.

## Input

Expected input:

```json
{
  "question": "Original user question",
  "research_synthesis": {
    "synthesis_version": "krw-research-synthesis/v1",
    "canonical_answer": {
      "default_order": ["p1", "m1"],
      "units": []
    }
  },
  "runtime": {
    "supports_structured_output": true,
    "language": "ko"
  }
}
```

The synthesis contains all displayable text, values, caveats, and evidence refs. Use only unit IDs from `canonical_answer.units`.

## Output

When the runtime supports structured output, return one `display_plan` payload according to `references/display-plan-contract.md`.

The output should contain block metadata only. It must not contain prose, metrics, rows, events, segments, channels, caveat text, evidence refs, raw ontology IDs, or quote text.

## Display Planning Rules

1. Preserve `canonical_answer.default_order` unless grouping adjacent compatible units improves readability.
2. Use `markdown` for normal prose and any unit that does not clearly fit a richer block.
3. Use rich block types only when they make the existing canonical units easier to understand.
4. Every block must reference existing `source_ids`.
5. Every required visible unit should appear in at least one block.
6. Keep caveats close to the units they qualify.
7. Do not rewrite canonical text.
8. Do not invent, reformat, round, or restate numbers.
9. Do not add new block types unless the frontend contract has been updated.
10. If unsure, output a simple markdown-first plan.

Allowed block types:

- `markdown`
- `metric_grid`
- `period_delta`
- `business_segments`
- `premise_card`
- `impact_channels`
- `risk_driver_map`
- `timeline`
- `comparison_table`
- `evidence_chain`
- `caveat_list`
- `glossary`

## Freedom Within Guardrails

The planner may choose whether to use rich blocks at all. It may choose block order, grouping, titles, and renderer hints.

The planner may not change content. This is intentional: the research answer remains flexible and high quality, while the display layer stays safe and deterministic.

## Quality Checks Before Finalizing

- Does every `source_id` exist in `canonical_answer.units`?
- Are all `importance="required"` and `display_policy="show"` units included?
- Are rich block types compatible with the referenced unit types?
- Did the plan avoid forbidden content fields such as `text`, `value`, `rows`, `metrics`, or `items`?
- Would the frontend be able to fall back to markdown if this plan partially fails?

## Reference

Use `references/display-plan-contract.md` for the full response envelope, schema, block compatibility table, validation rules, and examples.
