# KRW Ontology Artifact Contract

Use this contract when a web runtime or product UI needs a structured research artifact. The artifact is a canonical data shape for rendering; it is not raw HTML and not a React component.

## Ownership

- MCP tools own evidence retrieval: query, trace, chain, quality, and comparison.
- The agent owns synthesis: concise conclusions, interpretation, caveats, and inference labels.
- The web frontend owns rendering: React components, mobile cards, tables, drawers, self-contained HTML export, PDF, or deck export.
- Supabase or another product database may store the artifact JSON and evidence references.

Do not put frontend UI code, hooks, debounce logic, React components, Supabase migrations, or HTML templates in this plugin. Those belong to the product frontend.

## Core Rule

Store the structure that can regenerate HTML. Do not make HTML the source of truth.

```text
Evidence tools -> evidence ledger -> structured artifact JSON -> React/HTML render targets
```

HTML snapshots are allowed only as cache/export output. The canonical artifact is JSON plus evidence references.

## Visible Answer vs Artifact

For web chat, separate two outputs conceptually:

- `visible_answer`: short, customer-facing natural language. No raw object IDs, no quote text, no tool logs, no coverage/debug inventory.
- `artifact`: structured research content for the UI. It may include evidence references, chain summaries, source labels, confidence labels, and quality warnings.

If the runtime cannot accept a separate artifact channel, keep the visible answer clean and make the artifact structure implicit through tool calls/citations. Do not paste raw JSON into the user-facing answer unless the user explicitly asks for export/debug output.

## Recommended Artifact Shape

This is a target shape for product runtimes. Field names may be adapted by the frontend, but preserve the separation between content, evidence references, and quality.

```json
{
  "artifact_type": "company_snapshot | scenario_impact | comparison | timeline | evidence_chain | custom",
  "title": "Human-readable title",
  "summary": "One concise paragraph",
  "tickers": ["NVDA"],
  "source_scope": {
    "document_types": ["10-K", "10-Q"],
    "periods": ["FY2026"],
    "source_label": "NVDA FY2026 10-K"
  },
  "blocks": [
    {
      "type": "key_takeaways",
      "title": "Key takeaways",
      "items": [
        {
          "label": "Demand",
          "text": "Short synthesized point",
          "confidence": "direct | indirect | inferred | unsupported",
          "evidence_refs": ["ref_1"]
        }
      ]
    },
    {
      "type": "metric_grid",
      "title": "Reported metrics",
      "metrics": [
        {
          "label": "Revenue",
          "value": "$716.9B",
          "period": "FY2025",
          "source_label": "AMZN FY2025 10-K",
          "evidence_refs": ["ref_2"]
        }
      ]
    },
    {
      "type": "impact_channels",
      "title": "Impact channels",
      "channels": [
        {
          "name": "Revenue",
          "direction": "positive | negative | mixed | uncertain",
          "mechanism": "Why this channel matters",
          "confidence": "direct | indirect | inferred | unsupported",
          "evidence_refs": ["ref_3"]
        }
      ]
    },
    {
      "type": "evidence_chain",
      "title": "Evidence chain",
      "chains": [
        {
          "claim": "Plain-language claim",
          "source_label": "VG FY2025 10-K",
          "confidence": "direct",
          "quality_warnings": [],
          "evidence_refs": ["ref_4"]
        }
      ]
    }
  ],
  "evidence_refs": [
    {
      "id": "ref_1",
      "source_label": "NVDA FY2026 10-K",
      "object_type": "ResearchClaim",
      "evidence_grade": "direct",
      "trace_id": "internal-only-id",
      "quote_text": null,
      "chain_summary": {}
    }
  ],
  "quality": {
    "overall": "high | medium | low",
    "warnings": []
  }
}
```

## Block Guidance

Use a small set of stable block types:

- `key_takeaways`: 3-6 customer-facing conclusions.
- `metric_grid`: exact reported metrics, dates, amounts, capacity, guidance, or thresholds.
- `impact_channels`: scenario and market-report bridges by revenue, cost, margin, cash flow, liquidity, capex, contract, or regulatory channel.
- `risk_driver_map`: major risks, drivers, headwinds, and offsets.
- `timeline`: project milestones, events, guidance changes, filings, or temporal links.
- `comparison`: ticker or period comparisons.
- `evidence_chain`: chain summaries from `krw_ontology_chain`.
- `caveats`: data gaps, weak evidence, stale filings, or inference limits.

Prefer blocks that render well on mobile. Avoid wide tables unless the frontend has a responsive table component.

## Evidence Rules

- Use `krw_ontology_chain` for key conclusions that depend on connected evidence.
- Use `krw_ontology_trace` for exact facts, dates, amounts, project milestones, contract terms, and guidance.
- Keep raw ontology IDs internal. Put IDs in `evidence_refs` for the runtime, not in visible answer prose.
- Keep quote text hidden by default. Include quote text only when the user asks for raw evidence, audit output, or exportable citations.
- Label inference strength as `direct`, `indirect`, `inferred`, or `unsupported`.
- If evidence is weak, set a caveat block or quality warning instead of burying the weakness in prose.

## What Not To Do

- Do not output self-contained HTML as the canonical artifact.
- Do not output React components from the agent.
- Do not include hooks such as `useState`, `useEffect`, debounce examples, or data-fetching components in this plugin.
- Do not store design-system classes, CSS, or layout assumptions in the artifact.
- Do not make raw tool logs visible to users.

The frontend may implement React components like debounced search inputs, artifact panels, mobile cards, and HTML exporters. The plugin only defines how the agent should produce clean answers and structured research content.
