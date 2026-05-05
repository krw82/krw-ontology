"""Build evidence workbook and audit report artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from krw_ontology.utils.io import read_jsonl


_ARTIFACT_FILES = {
    "spans": "spans.jsonl",
    "evidence_quotes": "evidence_quotes.jsonl",
    "language_signals": "language_signals.jsonl",
    "claims": "claims.jsonl",
    "risks": "risks.jsonl",
    "growth_drivers": "growth_drivers.jsonl",
    "headwinds": "headwinds.jsonl",
    "modeling_cues": "assumption_candidates.jsonl",
    "edges": "edges.jsonl",
    "xbrl_facts": "xbrl_facts.jsonl",
    "financial_metric_values": "financial_metric_values.jsonl",
    "derived_metric_values": "derived_metric_values.jsonl",
    "numeric_evidence": "numeric_evidence.jsonl",
    "calculated_numeric_support": "calculated_numeric_support.jsonl",
    "rejected_objects": "rejected_objects.jsonl",
    "batch_failures": "batch_failures.jsonl",
}


def build_reports(
    *,
    ticker: str,
    period: str,
    document_type: str,
    ontology_dir: Path,
) -> dict[str, Path]:
    """Write graph_report.md and audit_report.md as review-oriented reports."""
    data = {key: read_jsonl(ontology_dir / filename) for key, filename in _ARTIFACT_FILES.items()}
    now = datetime.now(timezone.utc).isoformat()

    report_path = ontology_dir / "graph_report.md"
    audit_path = ontology_dir / "audit_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(_render_graph_report(ticker, period, document_type, now, data))
    audit_path.write_text(_render_audit_report(ticker, period, now, data))
    return {"graph_report": report_path, "audit_report": audit_path}


def _render_graph_report(
    ticker: str,
    period: str,
    document_type: str,
    generated_at: str,
    data: dict[str, list[dict]],
) -> str:
    counts = {key: len(rows) for key, rows in data.items()}
    sections = [
        "# Evidence Workbook",
        "",
        f"**Ticker:** {ticker}",
        f"**Period:** {period}",
        f"**Document Type:** {document_type}",
        f"**Generated:** {generated_at}",
        "",
        "> This report is an evidence review artifact. It does not provide buy/sell recommendations, target prices, forecasts, or portfolio advice.",
        "",
        "## Object Counts",
        "",
        "| Artifact | Count |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in counts.items()],
        "",
        "## Evidence Layer",
        "",
        f"- Accepted evidence quotes: {counts['evidence_quotes']}",
        f"- Evidence-backed claims: {counts['claims']}",
        f"- Language signals normalized from quotes: {counts['language_signals']}",
        "",
        "## Research Organization Layer",
        "",
        f"- Risks: {counts['risks']}",
        f"- Growth drivers: {counts['growth_drivers']}",
        f"- Headwinds: {counts['headwinds']}",
        f"- Modeling cues requiring review: {counts['modeling_cues']}",
        f"- Financial metric values: {counts['financial_metric_values']}",
        f"- Derived metric values: {counts['derived_metric_values']}",
        f"- Numeric evidence rows: {counts['numeric_evidence']}",
        "",
        "## Sample Modeling Cues",
        "",
        *_render_items(data["modeling_cues"], "assumption_text"),
        "",
        "## Sample Risks / Drivers / Headwinds",
        "",
        *_render_items([*data["risks"], *data["growth_drivers"], *data["headwinds"]], "description"),
        "",
        "## Sample Claims",
        "",
        *_render_items(data["claims"], "claim_text"),
        "",
    ]
    return "\n".join(sections)


def _render_audit_report(
    ticker: str,
    period: str,
    generated_at: str,
    data: dict[str, list[dict]],
) -> str:
    rejected_by_stage = Counter(obj.get("rejection_stage", "unknown") for obj in data["rejected_objects"])
    batch_failures_by_stage = Counter(obj.get("stage", "unknown") for obj in data["batch_failures"])
    sections = [
        "# Audit Report",
        "",
        f"**Ticker:** {ticker}",
        f"**Period:** {period}",
        f"**Generated:** {generated_at}",
        "",
        "## Validation Summary",
        "",
        f"- Accepted artifacts: {sum(len(data[key]) for key in ('spans', 'evidence_quotes', 'language_signals', 'claims', 'risks', 'growth_drivers', 'headwinds', 'modeling_cues', 'edges', 'xbrl_facts', 'financial_metric_values', 'derived_metric_values', 'numeric_evidence', 'calculated_numeric_support'))}",
        f"- Rejected objects: {len(data['rejected_objects'])}",
        f"- Batch failures: {len(data['batch_failures'])}",
        "",
        "## Rejections By Stage",
        "",
        *_render_counter(rejected_by_stage),
        "",
        "## Batch Failures By Stage",
        "",
        *_render_counter(batch_failures_by_stage),
        "",
        "## Boundary",
        "",
        "Canonical artifacts store evidence, claims, classification tags, metric references, and reviewable modeling cues. Investment opinions are intentionally excluded from canonical outputs.",
        "",
    ]
    return "\n".join(sections)


def _render_items(items: list[dict], text_key: str, limit: int = 8) -> list[str]:
    if not items:
        return ["_None generated._"]
    lines = []
    for item in items[:limit]:
        name = item.get("name") or item.get("id", "")
        text = item.get(text_key, "")
        support = item.get("supported_by_claims") or item.get("supported_by_quotes") or []
        lines.append(f"- **{name}**: {text}")
        if support:
            lines.append(f"  - Support: {', '.join(support[:3])}")
    return lines


def _render_counter(counter: Counter) -> list[str]:
    if not counter:
        return ["_None._"]
    return [f"- {key}: {value}" for key, value in counter.most_common()]
