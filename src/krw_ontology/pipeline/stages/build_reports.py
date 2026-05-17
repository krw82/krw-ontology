"""Build evidence workbook and audit report artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from krw_ontology.utils.io import read_jsonl


_ARTIFACT_FILES = {
    "document_nodes": "document_nodes.jsonl",
    "section_candidates": "section_candidates.jsonl",
    "sections": "sections.jsonl",
    "section_boundary_audit": "section_boundary_audit.jsonl",
    "spans": "spans.jsonl",
    "span_eligibility_audit": "span_eligibility_audit.jsonl",
    "evidence_quotes": "evidence_quotes.jsonl",
    "language_signals": "language_signals.jsonl",
    "claims": "claims.jsonl",
    "business_activities": "business_activities.jsonl",
    "external_factor_exposures": "external_factor_exposures.jsonl",
    "business_factors": "business_factors.jsonl",
    "agreement_terms": "agreement_terms.jsonl",
    "business_events": "business_events.jsonl",
    "modeling_cues": "assumption_candidates.jsonl",
    "edges": "edges.jsonl",
    "xbrl_facts": "xbrl_facts.jsonl",
    "metric_observations": "metric_observations.jsonl",
    "calculations": "calculations.jsonl",
    "support_links": "support_links.jsonl",
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
        f"- Document nodes: {counts['document_nodes']}",
        f"- Selected sections: {counts['sections']}",
        f"- Section boundary candidates: {counts['section_candidates']}",
        f"- Span eligibility decisions: {counts['span_eligibility_audit']}",
        f"- Accepted evidence quotes: {counts['evidence_quotes']}",
        f"- Evidence-backed claims: {counts['claims']}",
        f"- Language signals normalized from quotes: {counts['language_signals']}",
        "",
        "## Research Organization Layer",
        "",
        f"- Business activities: {counts['business_activities']}",
        f"- External factor exposures: {counts['external_factor_exposures']}",
        f"- Business factors: {counts['business_factors']}",
        f"- Agreement terms: {counts['agreement_terms']}",
        f"- Business events: {counts['business_events']}",
        f"- Modeling cues requiring review: {counts['modeling_cues']}",
        f"- Metric observations: {counts['metric_observations']}",
        f"- Calculations: {counts['calculations']}",
        f"- Support links: {counts['support_links']}",
        "",
        "## Sample Modeling Cues",
        "",
        *_render_items(data["modeling_cues"], "assumption_text"),
        "",
        "## Sample Business Factors / Events / Agreements",
        "",
        *_render_items([*data["business_factors"], *data["business_events"], *data["agreement_terms"]], "description"),
        "",
        "## Sample Business / Exposure Objects",
        "",
        *_render_business_and_exposure_items(data["business_activities"], data["external_factor_exposures"]),
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
        f"- Accepted artifacts: {sum(len(data[key]) for key in ('spans', 'evidence_quotes', 'language_signals', 'claims', 'business_activities', 'external_factor_exposures', 'business_factors', 'agreement_terms', 'business_events', 'modeling_cues', 'edges', 'xbrl_facts', 'metric_observations', 'calculations', 'support_links'))}",
        f"- Rejected objects: {len(data['rejected_objects'])}",
        f"- Batch failures: {len(data['batch_failures'])}",
        f"- Document nodes: {len(data['document_nodes'])}",
        f"- Section candidates: {len(data['section_candidates'])}",
        f"- Selected sections: {len(data['sections'])}",
        f"- Span eligibility decisions: {len(data['span_eligibility_audit'])}",
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


def _render_business_and_exposure_items(
    activities: list[dict],
    exposures: list[dict],
    limit: int = 8,
) -> list[str]:
    items = [*activities, *exposures]
    if not items:
        return ["_None generated._"]
    lines = []
    for item in items[:limit]:
        name = item.get("name") or item.get("factor") or item.get("id", "")
        if item.get("type") == "ExternalFactorExposure":
            text = item.get("mechanism", "")
        else:
            text = item.get("description", "")
        support = item.get("supported_by_claims") or item.get("supported_by_quotes") or []
        lines.append(f"- **{name}**: {text}")
        if support:
            lines.append(f"  - Support: {', '.join(support[:3])}")
    return lines


def _render_counter(counter: Counter) -> list[str]:
    if not counter:
        return ["_None._"]
    return [f"- {key}: {value}" for key, value in counter.most_common()]
