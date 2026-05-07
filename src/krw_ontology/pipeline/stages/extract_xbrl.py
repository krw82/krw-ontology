"""Stage 7.7: Extract inline XBRL facts from HTML."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
import yaml

from krw_ontology.errors import PipelineStageError
from krw_ontology.schema.id_utils import generate_scoped_id, generate_xbrl_local_id
from krw_ontology.schema.objects import SCHEMA_VERSION
from krw_ontology.utils.io import find_project_root, write_jsonl

logger = logging.getLogger("krw_ontology")

US_GAAP_NS_RE = re.compile(r"\bus-gaap_(\w+)", re.IGNORECASE)
IXBRL_NS_RE = re.compile(r"^\{[^}]*\}(\w+)")
_AMOUNT_UNITS = {"usd", "USD", "iso4217:USD"}


def extract_xbrl(
    raw_html_path: Path,
    ticker: str,
    period: str,
    doc_type_key: str,
    source_document_id: str,
    output_path: Path,
    document_type: str = "10-K",
) -> dict:
    """Parse inline XBRL facts from SEC filing HTML.

    Returns dict with: facts, output_path, status.
    """
    try:
        xbrl_source_path, soup, ix_tags = _load_inline_xbrl_source(raw_html_path)
    except OSError as e:
        raise PipelineStageError(f"extract_xbrl: cannot read {raw_html_path}: {e}") from e

    facts: list[dict] = []
    contexts = _extract_contexts(soup)

    if not ix_tags:
        logger.warning(
            "extract_xbrl: no inline XBRL elements found",
            extra={"stage": "extract_xbrl_facts", "status": "missing_or_failed"},
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_empty_xbrl_outputs(output_path)
        return {"facts": [], "output_path": output_path, "status": "missing_or_failed"}

    for tag in ix_tags:
        tag_name = tag.name
        if tag_name and "nonfraction" not in str(tag_name).lower():
            continue

        taxonomy_tag = tag.get("name", "")
        if not taxonomy_tag:
            continue

        # Extract the taxonomy tag, normalizing us-gaap prefix
        safe_tag = _safe_taxonomy_tag(taxonomy_tag)
        if not safe_tag:
            continue

        context_ref = _get_attr(tag, "contextRef") or ""
        unit_ref = _get_attr(tag, "unitRef") or ""
        decimals = tag.get("decimals")
        scale = _parse_int(_get_attr(tag, "scale"))
        value_str = tag.get_text(strip=True)

        value = _parse_ixbrl_number(value_str, scale, _get_attr(tag, "sign"))
        if value is None:
            continue

        fact_id = generate_scoped_id(
            "xbrl", ticker, period, doc_type_key,
            generate_xbrl_local_id(safe_tag, context_ref, unit_ref, str(value)),
        )

        facts.append({
            "id": fact_id,
            "type": "XBRLFact",
            "ticker": ticker,
            "source_document_id": source_document_id,
            "document_type": document_type,
            "period": period,
            "taxonomy_tag": taxonomy_tag,
            "safe_taxonomy_tag": safe_tag,
            "value": value,
            "unit": unit_ref,
            "context_ref": context_ref,
            "context": contexts.get(context_ref, {}),
            "source_filing_detail": str(xbrl_source_path),
            "decimals": _parse_int(decimals),
            "schema_version": SCHEMA_VERSION,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_path, facts)
    metric_values = _build_financial_metric_values(
        facts=facts,
        raw_html_path=raw_html_path,
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        document_type=document_type,
        source_document_id=source_document_id,
    )
    derived_values = _build_derived_metric_values(
        metric_values=metric_values,
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        document_type=document_type,
        source_document_id=source_document_id,
    )
    write_jsonl(output_path.parent / "financial_metric_values.jsonl", metric_values)
    write_jsonl(output_path.parent / "derived_metric_values.jsonl", derived_values)

    status = "ok" if facts else "missing_or_failed"
    logger.info(
        "extract_xbrl: extracted %d facts, %d metric values, %d derived values, status=%s",
        len(facts),
        len(metric_values),
        len(derived_values),
        status,
        extra={"stage": "extract_xbrl_facts"},
    )
    return {"facts": facts, "output_path": output_path, "status": status}


def _load_inline_xbrl_source(raw_html_path: Path) -> tuple[Path, BeautifulSoup, list[Any]]:
    """Load primary HTML or a sibling filing document that contains inline XBRL."""
    soup = BeautifulSoup(raw_html_path.read_bytes(), "lxml")
    ix_tags = _inline_xbrl_tags(soup)
    if ix_tags:
        return raw_html_path, soup, ix_tags

    best_path: Path | None = None
    best_soup: BeautifulSoup | None = None
    best_tags: list[Any] = []
    for candidate in _sibling_html_candidates(raw_html_path):
        candidate_soup = BeautifulSoup(candidate.read_bytes(), "lxml")
        candidate_tags = _inline_xbrl_tags(candidate_soup)
        if len(candidate_tags) > len(best_tags):
            best_path = candidate
            best_soup = candidate_soup
            best_tags = candidate_tags

    if best_path and best_soup and best_tags:
        logger.info(
            "extract_xbrl: using sibling inline XBRL source %s instead of %s",
            best_path,
            raw_html_path,
            extra={"stage": "extract_xbrl_facts", "xbrl_source_recovered": True},
        )
        return best_path, best_soup, best_tags
    return raw_html_path, soup, ix_tags


def _sibling_html_candidates(raw_html_path: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in sorted(raw_html_path.parent.iterdir()):
        if candidate == raw_html_path or not candidate.is_file():
            continue
        if candidate.suffix.lower() in {".htm", ".html"}:
            candidates.append(candidate)
    return candidates


def _inline_xbrl_tags(soup: BeautifulSoup) -> list[Any]:
    # Find inline XBRL elements: <ix:nonFraction> and <ix:nonNumeric>.
    ix_tags = soup.find_all(re.compile(r"^ix:"))
    if ix_tags:
        return ix_tags
    return soup.find_all(re.compile(r"ix:", re.IGNORECASE))


def _write_empty_xbrl_outputs(output_path: Path) -> None:
    write_jsonl(output_path, [])
    write_jsonl(output_path.parent / "financial_metric_values.jsonl", [])
    write_jsonl(output_path.parent / "derived_metric_values.jsonl", [])


def _extract_contexts(soup: BeautifulSoup) -> dict[str, dict]:
    contexts = {}
    for tag in soup.find_all():
        if not tag.name or "context" not in str(tag.name).lower():
            continue
        context_id = _get_attr(tag, "id")
        if not context_id:
            continue
        explicit_members = [
            member.get_text(strip=True)
            for member in tag.find_all()
            if member.name and "explicitmember" in str(member.name).lower()
        ]
        start_date = _find_child_text(tag, "startdate")
        end_date = _find_child_text(tag, "enddate")
        instant = _find_child_text(tag, "instant")
        contexts[context_id] = {
            "id": context_id,
            "start_date": start_date,
            "end_date": end_date,
            "instant": instant,
            "period_type": "instant" if instant else "duration",
            "duration_days": _duration_days(start_date, end_date),
            "fiscal_year": _year_from_date(end_date or instant),
            "dimensions": explicit_members,
            "has_dimensions": bool(explicit_members),
        }
    return contexts


def _build_financial_metric_values(
    *,
    facts: list[dict],
    raw_html_path: Path,
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
) -> list[dict]:
    metric_specs = _load_metric_specs(raw_html_path)
    values: list[dict] = []
    selected: set[tuple[str, int | None, str, str | None, str | None]] = set()

    for metric_name, spec in metric_specs.items():
        tags = set(spec.get("xbrl_tags") or [])
        if not tags:
            continue
        candidates = [fact for fact in facts if fact.get("taxonomy_tag") in tags]
        candidates.sort(key=_fact_rank)
        for fact in candidates:
            context = fact.get("context") or {}
            fiscal_year = context.get("fiscal_year")
            period_type = _metric_period_type(context)
            start_date = context.get("start_date")
            end_date = context.get("end_date") or context.get("instant")
            key = (metric_name, fiscal_year, period_type, start_date, end_date)
            if key in selected:
                continue
            if context.get("has_dimensions"):
                continue
            selected.add(key)
            values.append({
                "id": generate_scoped_id(
                    "financial_metric",
                    ticker,
                    period,
                    doc_type_key,
                    _hash_local_id(metric_name, str(fiscal_year), fact["id"]),
                ),
                "type": "FinancialMetricValue",
                "ticker": ticker,
                "source_document_id": source_document_id,
                "document_type": document_type,
                "period": period,
                "metric_name": metric_name,
                "value": fact["value"],
                "unit": spec.get("unit", fact.get("unit", "")),
                "fiscal_year": fiscal_year,
                "fiscal_period": _period_quarter(period),
                "period_type": period_type,
                "start_date": start_date,
                "end_date": end_date,
                "source_xbrl_fact_id": fact["id"],
                "source": "filing_inline_xbrl",
                "schema_version": SCHEMA_VERSION,
            })
    return values


def _build_derived_metric_values(
    *,
    metric_values: list[dict],
    ticker: str,
    period: str,
    doc_type_key: str,
    document_type: str,
    source_document_id: str,
) -> list[dict]:
    by_metric_year: dict[tuple[str, int | None], dict] = {
        (row["metric_name"], row.get("fiscal_year")): row
        for row in metric_values
    }
    years = sorted({row.get("fiscal_year") for row in metric_values if row.get("fiscal_year")})
    current_year = _period_year(period) or (years[-1] if years else None)
    prior_year = current_year - 1 if current_year else None
    derived: list[dict] = []

    def add_ratio(metric_name: str, numerator_name: str, denominator_name: str, formula: str) -> None:
        numerator = by_metric_year.get((numerator_name, current_year))
        denominator = by_metric_year.get((denominator_name, current_year))
        if not numerator or not denominator or not denominator.get("value"):
            return
        value = (numerator["value"] / denominator["value"]) * 100
        _append_derived(
            derived, ticker, period, doc_type_key, source_document_id,
            document_type, metric_name, value, "percent", formula, [numerator["id"], denominator["id"]],
        )

    def add_growth(metric_name: str, base_name: str) -> None:
        current = by_metric_year.get((base_name, current_year))
        prior = by_metric_year.get((base_name, prior_year))
        if not current or not prior or not prior.get("value"):
            return
        value = ((current["value"] - prior["value"]) / prior["value"]) * 100
        _append_derived(
            derived, ticker, period, doc_type_key, source_document_id,
            document_type, metric_name, value, "percent", f"({base_name}_{current_year} - {base_name}_{prior_year}) / {base_name}_{prior_year}", [current["id"], prior["id"]],
        )

    def add_difference(metric_name: str, left_name: str, right_name: str, formula: str) -> None:
        left = by_metric_year.get((left_name, current_year))
        right = by_metric_year.get((right_name, current_year))
        if not left or not right:
            return
        value = left["value"] - abs(right["value"])
        _append_derived(
            derived, ticker, period, doc_type_key, source_document_id,
            document_type, metric_name, value, "USD", formula, [left["id"], right["id"]],
        )

    add_growth("revenue_growth", "revenue")
    add_ratio("gross_margin", "gross_profit", "revenue", "gross_profit / revenue")
    add_ratio("operating_margin", "operating_income", "revenue", "operating_income / revenue")
    add_ratio("net_margin", "net_income", "revenue", "net_income / revenue")
    add_difference("free_cash_flow", "operating_cash_flow", "capital_expenditures", "operating_cash_flow - capital_expenditures")
    by_metric_year.update({(row["metric_name"], current_year): row for row in derived})
    add_ratio("fcf_margin", "free_cash_flow", "revenue", "free_cash_flow / revenue")
    return derived


def _safe_taxonomy_tag(tag: str) -> str:
    """Convert us-gaap namespace tag to safe ID component."""
    match = US_GAAP_NS_RE.search(tag)
    if match:
        return match.group(1)
    # Handle namespace-prefixed tags like {uri}TagName
    match = IXBRL_NS_RE.match(tag)
    if match:
        return match.group(1)
    if ":" in tag:
        return tag.split(":", 1)[1]
    # Use the raw tag name if it looks safe
    if re.match(r"^[A-Za-z_]\w*$", tag):
        return tag
    return ""


def _get_attr(tag: Any, name: str) -> str | None:
    target = name.lower()
    for key, value in tag.attrs.items():
        if str(key).lower() == target:
            return str(value)
    return None


def _find_child_text(tag: Any, child_name: str) -> str | None:
    for child in tag.find_all():
        if child.name and str(child.name).lower().endswith(child_name.lower()):
            return child.get_text(strip=True)
    return None


def _year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _duration_days(start_date: str | None, end_date: str | None) -> int | None:
    if not start_date or not end_date:
        return None
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return None
    return (end - start).days + 1


def _metric_period_type(context: dict) -> str:
    if context.get("instant"):
        return "instant"
    days = context.get("duration_days")
    if not isinstance(days, int):
        return context.get("period_type") or "duration"
    if 70 <= days <= 115:
        return "quarter"
    if 160 <= days <= 300:
        return "year_to_date"
    if days >= 330:
        return "annual"
    return "duration"


def _period_year(period: str) -> int | None:
    match = re.match(r"^FY(\d{4})", period)
    return int(match.group(1)) if match else None


def _period_quarter(period: str) -> str | None:
    match = re.match(r"^FY\d{4}(Q[1-4])$", period)
    return match.group(1) if match else None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_ixbrl_number(raw: str, scale: int | None, sign: str | None) -> float | None:
    if not raw:
        return None
    normalized = raw.replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    if normalized in {"", "—", "-", "–"}:
        return None
    negative = normalized.startswith("(") and normalized.endswith(")")
    normalized = normalized.strip("()")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if scale is not None:
        value *= 10 ** scale
    if sign == "-" or negative:
        value *= -1
    return value


def _load_metric_specs(raw_html_path: Path) -> dict[str, dict]:
    metric_path = find_project_root(raw_html_path) / "ontology" / "schema" / "metric_dictionary.yaml"
    if not metric_path.exists():
        return {}
    with open(metric_path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("canonical_metrics") or {}


def _fact_rank(fact: dict) -> tuple[int, int, int]:
    context = fact.get("context") or {}
    no_dimensions = 0 if not context.get("has_dimensions") else 1
    unit_rank = 0 if fact.get("unit") in _AMOUNT_UNITS or fact.get("unit") in {"shares", "usdPerShare", "number"} else 1
    year = context.get("fiscal_year") or 0
    return no_dimensions, unit_rank, -year


def _append_derived(
    rows: list[dict],
    ticker: str,
    period: str,
    doc_type_key: str,
    source_document_id: str,
    document_type: str,
    metric_name: str,
    value: float,
    unit: str,
    formula: str,
    input_metric_ids: list[str],
) -> None:
    rows.append({
        "id": generate_scoped_id(
            "derived_metric",
            ticker,
            period,
            doc_type_key,
            _hash_local_id(metric_name, formula, *input_metric_ids),
        ),
        "type": "DerivedMetricValue",
        "ticker": ticker,
        "source_document_id": source_document_id,
        "document_type": document_type,
        "period": period,
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "fiscal_year": _period_year(period),
        "fiscal_period": _period_quarter(period),
        "period_type": "derived",
        "formula": formula,
        "input_metric_ids": input_metric_ids,
        "source": "code_calculated",
        "schema_version": SCHEMA_VERSION,
    })


def _hash_local_id(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]
