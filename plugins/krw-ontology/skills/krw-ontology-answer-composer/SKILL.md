---
name: krw-ontology-answer-composer
description: Use only when structured display planning is explicitly requested after KRW ontology research content already exists. This skill creates display_plan metadata only. It does not retrieve evidence, rewrite research content, or create normal web-chat answers.
---

# KRW Ontology Answer Composer

This skill is optional. It is not part of the default web-chat Markdown runtime.

Use it only when the runtime or user explicitly requests frontend display planning from already-written research content.

## Boundary

This skill owns:

```text
display block selection
block ordering
source unit grouping
short non-factual display titles
renderer hints
validation warnings
```

This skill does not own:

```text
evidence retrieval
MCP tool calls
trace or chain discovery
numeric validation
research prose
new claims or numbers
rewriting answer content
React/CSS/HTML implementation
```

If canonical answer units or equivalent source content are missing, say that display planning cannot proceed without research content. Do not fill the gap.

## Output

When structured output is supported, return a `display_plan` according to `references/display-plan-contract.md`.

The plan may reference existing source unit IDs and choose block types. It must not contain factual prose, values, rows, metrics, segments, channels, caveats, raw IDs, or quote text.

## Default web chat

Do not use this skill for normal Korean Markdown answers.
