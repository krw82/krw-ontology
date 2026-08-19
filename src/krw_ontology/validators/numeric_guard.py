"""Numeric guard — numeric claim XBRL cross-check (Section 8.6)."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re


_NUMERIC_TEXT_FIELDS: dict[str, list[str]] = {
    "ResearchClaim": ["claim_text"],
    "AssumptionCandidate": ["assumption_text", "value_hint"],
    "RiskFactor": ["description", "qualitative_impact"],
    "GrowthDriver": ["description", "qualitative_impact"],
    "Headwind": ["description", "qualitative_impact"],
}

_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<sign>[+-])?\s*(?P<prefix>[$€£])?\s*"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<plus>\+)?"
    r"\s*(?P<unit>%|percent|percentage points?|basis points?|bps|thousand|million|billion|k|m|b)?"
    r"(?!\w)",
    re.IGNORECASE,
)

_ACCOUNTING_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?P<prefix>[$€£])?\s*\((?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\)"
    r"\s*(?P<unit>thousand|million|billion|k|m|b)?(?!\w)",
    re.IGNORECASE,
)

_NUMERIC_SUPPORT_TYPES = frozenset({
    "XBRLFact",
    "MetricObservation",
    "FinancialMetricValue",
    "DerivedMetricValue",
    "CalculatedNumericSupport",
    "NumericEvidence",
})

_PERCENT_UNITS = {
    "%",
    "percent",
    "percentage point",
    "percentage points",
    "basis point",
    "basis points",
    "bps",
}

_AMOUNT_UNITS = {"thousand", "million", "billion", "k", "m", "b"}

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
)

_DOCUMENT_CONTEXT_WORDS = (
    "item", "form", "article", "section", "part", "chapter", "rule",
    "regulation", "note", "table", "exhibit",
)

_VERSION_CONTEXT_WORDS = (
    "model", "series", "version", "gen", "generation", "class", "type",
    "tier", "v",
)

_ACCOUNTING_CONTEXT_WORDS = (
    "asc", "asu", "ifrs", "gaap", "ias", "sfas",
)

_FINANCIAL_CONTEXT_WORDS = (
    "revenue", "sales", "margin", "income", "expense", "expenses", "cost",
    "costs", "profit", "profits", "loss", "losses", "earnings", "eps",
    "cash", "flow", "debt", "assets", "liabilities", "equity", "tax",
    "taxes", "share", "shares", "dividend", "dividends", "buyback", "repurchase",
    "capex", "opex", "gross", "operating", "net", "free cash flow",
    "payment", "payments", "acquisition", "property", "plant", "equipment",
    "obligation", "obligations", "commitment", "commitments",
)

_OPERATING_COUNT_WORDS = {
    "employee", "employees", "worker", "workers", "headcount",
    "store", "stores", "office", "offices", "branch", "branches",
    "facility", "facilities", "site", "sites", "location", "locations",
    "country", "countries", "jurisdiction", "jurisdictions", "region", "regions",
}


@dataclass(frozen=True)
class NumericToken:
    kind: str
    value: float
    label: str
    tolerance: float | None = None


def _parse_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _extract_numbers(text: str) -> list[NumericToken]:
    """Extract financially meaningful numeric tokens from text."""
    if not text:
        return []

    tokens: list[NumericToken] = []
    consumed_spans: list[tuple[int, int]] = []
    context_multiplier = _context_amount_multiplier(text)

    for match in _ACCOUNTING_AMOUNT_RE.finditer(text):
        value = _parse_float(match.group("number"))
        if value is None:
            continue
        unit = (match.group("unit") or "").lower()
        prefix = match.group("prefix") or ""
        label = match.group(0).strip()
        if unit or prefix or context_multiplier:
            tokens.append(
                _make_numeric_token(
                    -value,
                    prefix or "$" if context_multiplier else prefix,
                    unit or _unit_for_multiplier(context_multiplier),
                    label,
                )
            )
            consumed_spans.append(match.span())

    for match in _NUMBER_RE.finditer(text):
        if _overlaps(match.span(), consumed_spans):
            continue
        value = _parse_float(match.group("number"))
        if value is None:
            continue
        if match.group("sign") == "-":
            value = -value
        unit = (match.group("unit") or "").lower()
        prefix = match.group("prefix") or ""
        if _should_ignore_number(text, match, value, unit):
            continue
        if unit in _PERCENT_UNITS and not match.group("sign"):
            direction = _percent_direction(text, match)
            if direction is not None:
                value = abs(value) * direction
        token = _make_numeric_token(value, prefix, unit, match.group(0))
        tokens.append(token)
        if not unit and context_multiplier:
            context_unit = _unit_for_multiplier(context_multiplier)
            if context_unit:
                tokens.append(_make_numeric_token(value, prefix, context_unit, match.group(0)))
    return tokens


def _overlaps(span: tuple[int, int], consumed_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < consumed_end and end > consumed_start for consumed_start, consumed_end in consumed_spans)


def _context_amount_multiplier(text: str) -> int | None:
    # A table that mentions more than one unit has ambiguous scale. Do not make
    # a best-effort conversion: an unsupported value is safer than a false one.
    multipliers = list(dict.fromkeys(_context_amount_multipliers(text)))
    return multipliers[0] if len(multipliers) == 1 else None


def _context_amount_multipliers(text: str) -> list[int]:
    lower = text.lower()
    multipliers: list[int] = []
    if re.search(r"\b(?:dollars\s+)?in\s+millions\b", lower):
        multipliers.append(1_000_000)
    if re.search(r"\b(?:dollars\s+)?in\s+thousands\b", lower):
        multipliers.append(1_000)
    if re.search(r"\b(?:dollars\s+)?in\s+billions\b", lower):
        multipliers.append(1_000_000_000)
    return multipliers


def _unit_for_multiplier(multiplier: int | None) -> str:
    return {
        1_000: "thousand",
        1_000_000: "million",
        1_000_000_000: "billion",
    }.get(multiplier or 0, "")


def _should_ignore_number(text: str, match: re.Match, value: float, unit: str) -> bool:
    """Skip date, product, filing-code, and label numbers that are not financial values."""
    lower = text.lower()
    start, end = match.span()
    before = lower[max(0, start - 40):start]
    after = lower[end:min(len(lower), end + 40)]
    context = before + lower[start:end] + after

    if unit in _PERCENT_UNITS:
        return False
    if unit in _AMOUNT_UNITS or match.group("prefix"):
        return False
    if re.match(r"^[\s|]*%", after):
        return False

    if _is_enumeration_marker(text, match, value):
        return True
    if match.group("plus") and re.match(r"\s*(?:yrs?|years?)\b", after):
        return True
    if _is_date_context(before, after, value):
        return True
    if _is_regulatory_code_context(text, match, value):
        return True
    if _is_duration_range_context(after):
        return True
    if re.search(r"\basu\s+$", before) or (start > 0 and text[start - 1] == "-") or (end < len(text) and text[end:end + 1] == "-"):
        return True
    if re.search(r"\bq$", before) and value in {1, 2, 3, 4}:
        return True
    if re.match(r"\s*(days?|months?|yrs?|years?)\b", after):
        return True
    if value <= 20 and "article" in context:
        return True
    if 1 <= value <= 60 and "weeks" in context:
        return True
    if 1900 <= value <= 2100:
        return True
    if value.is_integer() and 1 <= value <= 31 and any(re.search(rf"\b{re.escape(month)}\.?\b", context) for month in _MONTH_NAMES):
        return True
    if _looks_like_non_financial_identifier(text, match, value, context):
        return True
    return False


def _is_date_context(before: str, after: str, value: float) -> bool:
    """Detect day/month numbers in prose dates like "Sept. 27, 2025"."""
    month_pattern = r"\b(?:" + "|".join(re.escape(month) for month in _MONTH_NAMES) + r")\.?\b"
    if value.is_integer() and 1 <= value <= 31 and re.search(month_pattern + r"\s*$", before):
        return True
    if value.is_integer() and 1 <= value <= 31 and re.match(r"\s*,?\s*(?:19|20)\d{2}\b", after):
        return True
    return False


def _is_regulatory_code_context(text: str, match: re.Match, value: float) -> bool:
    """Detect regulatory/document code suffixes such as "Group D:5"."""
    start, end = match.span()
    if not (0 <= value <= 9999 and start > 0 and text[start - 1] == ":"):
        return False
    if start > 1 and text[start - 2].isalpha():
        return True
    before = text[max(0, start - 80):start].lower()
    after = text[end:min(len(text), end + 20)].lower()
    if re.search(r"\b(?:group|groups|country|countries|class|classes|part|rule|article)\s+[a-z]:$", before):
        return True
    return not after or after.lstrip().startswith((",", ";", ")", "]"))


def _is_duration_range_context(after: str) -> bool:
    return bool(
        re.match(
            r"\s*(?:to|-|–|—)\s*\d+(?:\.\d+)?\s*(?:days?|months?|yrs?|years?)\b",
            after,
        )
    )


def _percent_direction(text: str, match: re.Match) -> int | None:
    """Infer an explicit percentage direction from the local wording only."""
    start, end = match.span()
    context = text[max(0, start - 80):min(len(text), end + 80)].lower()
    if re.search(r"\b(?:decreased?|declined?|fell|reduced?|down)\b", context):
        return -1
    if re.search(r"\b(?:increased?|grew|rose|expanded?|up)\b", context):
        return 1
    return None


def _is_enumeration_marker(text: str, match: re.Match, value: float) -> bool:
    """Detect list markers like "(1)" and "1)" in prose."""
    if not value.is_integer() or not 1 <= value <= 100:
        return False
    start, end = match.span()
    before = text[max(0, start - 3):start]
    after = text[end:min(len(text), end + 3)]
    if before.endswith("(") and after.startswith(")"):
        return True
    if after.startswith(")") and (not before or before[-1].isspace()):
        return True
    return False


def _looks_like_non_financial_identifier(
    text: str, match: re.Match, value: float, context: str
) -> bool:
    """Detect company-neutral document, accounting, product, and version numbers.

    This intentionally avoids company/product dictionaries. The guard should not
    know about specific issuers or product catalogs. It only
    classifies patterns that are structurally unlikely to be financial values.
    """
    start, end = match.span()
    before = text[max(0, start - 80):start]
    after = text[end:min(len(text), end + 80)]
    before_lower = before.lower()
    after_lower = after.lower()

    previous_words = re.findall(r"[A-Za-z][A-Za-z0-9.-]*", before_lower)
    next_words = re.findall(r"[A-Za-z][A-Za-z0-9.-]*", after_lower)
    previous_word = previous_words[-1] if previous_words else ""
    next_word = next_words[0] if next_words else ""
    next_two_words = " ".join(next_words[:2])

    if previous_word in _DOCUMENT_CONTEXT_WORDS or previous_word in _ACCOUNTING_CONTEXT_WORDS:
        return True
    if previous_word in _VERSION_CONTEXT_WORDS:
        return True
    if next_word in _DOCUMENT_CONTEXT_WORDS and value <= 50:
        return True
    if next_word in _VERSION_CONTEXT_WORDS and value <= 50:
        return True
    if next_two_words in {"operating system"} and value <= 9999:
        return True
    if next_word in _OPERATING_COUNT_WORDS:
        return True

    # Compact product/model identifiers such as a letter-prefixed model number.
    if start > 0 and text[start - 1].isalpha() and value <= 9999:
        return True

    local_context = f"{before_lower[-80:]} {after_lower[:80]}".replace("operating system", "")
    if any(word in local_context for word in _FINANCIAL_CONTEXT_WORDS):
        return False
    if "|" in before or "|" in after:
        return False

    # Generic product/version phrases. The previous token is deliberately
    # generic: when there is no financial context and no financial unit, a bare
    # number following a named token is more likely to be an identifier than a
    # supported metric.
    if previous_word and previous_word not in _FINANCIAL_CONTEXT_WORDS and value <= 9999:
        if next_word not in {"million", "billion", "thousand", "percent", "bps"}:
            return True

    return False


def _make_numeric_token(value: float, prefix: str, unit: str, label: str) -> NumericToken:
    if unit in {"%", "percent"}:
        return NumericToken("percent", value, label.strip())
    if unit in {"percentage point", "percentage points"}:
        return NumericToken("percent", value, label.strip())
    if unit in {"basis point", "basis points", "bps"}:
        return NumericToken("percent", value / 100, label.strip())

    multiplier = {
        "thousand": 1_000,
        "k": 1_000,
        "million": 1_000_000,
        "billion": 1_000_000_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
    }.get(unit, 1)
    if prefix or unit in _AMOUNT_UNITS:
        return NumericToken(
            "amount",
            value * multiplier,
            label.strip(),
            _amount_rounding_tolerance(label, unit, multiplier),
        )
    return NumericToken("number", value, label.strip())


def _amount_rounding_tolerance(label: str, unit: str, multiplier: int) -> float | None:
    """Infer amount tolerance from the written unit and decimal precision.

    "$178.35 billion" represents a value rounded to the nearest 0.01 billion,
    so support may differ by up to 0.005 billion. This keeps validation strict
    while allowing normal filing/claim wording conversions.
    """
    normalized = label.replace(",", "")
    match = re.search(r"\d+(?:\.(\d+))?", normalized)
    if not match or not unit:
        return None
    decimals = len(match.group(1) or "")
    step = multiplier / (10 ** decimals)
    return step / 2


def _is_supported(required: NumericToken, supported: list[NumericToken]) -> bool:
    for candidate in supported:
        if required.kind != candidate.kind:
            continue
        if required.kind == "amount":
            tolerance = max(
                0.01,
                required.tolerance or 0,
                candidate.tolerance or 0,
            )
        elif required.kind == "percent":
            tolerance = 0.05
        else:
            tolerance = max(0.01, abs(required.value) * 0.000001)
        if abs(required.value - candidate.value) <= tolerance:
            return True
    return False


def _build_calculated_percent_support(
    numbers: list[NumericToken], label_prefix: str
) -> list[NumericToken]:
    """Derive percent support from source-backed numeric pairs.

    10-K tables often provide only the raw values while AI claims naturally
    summarize the ratio or year-over-year change. These calculated percentages
    are allowed only when both input numbers already came from supporting quote
    text or XBRL facts.
    """
    values_by_kind: dict[str, list[float]] = {"amount": [], "number": []}
    for token in numbers:
        if token.kind not in values_by_kind:
            continue
        if token.value == 0:
            continue
        bucket = values_by_kind[token.kind]
        if not any(abs(existing - token.value) <= max(0.01, abs(token.value) * 1e-12) for existing in bucket):
            bucket.append(token.value)

    tokens: list[NumericToken] = []
    for kind, values in values_by_kind.items():
        if len(values) > 80:
            continue
        for numerator in values:
            for denominator in values:
                if numerator == denominator or denominator == 0:
                    continue
                growth = ((numerator - denominator) / abs(denominator)) * 100
                ratio = (numerator / denominator) * 100
                if abs(growth) <= 1_000:
                    tokens.extend(
                        _percent_support_variants(
                            growth,
                            f"{label_prefix}_{kind}_growth",
                        )
                    )
                if 0 < abs(ratio) <= 1_000:
                    tokens.extend(
                        _percent_support_variants(
                            ratio,
                            f"{label_prefix}_{kind}_ratio",
                        )
                    )
    return tokens


def _build_calculated_amount_support(
    numbers: list[NumericToken], label_prefix: str
) -> list[NumericToken]:
    """Derive amount support from source-backed additions and differences."""
    values_by_kind: dict[str, list[float]] = {"amount": [], "number": []}
    for token in numbers:
        if token.kind not in values_by_kind:
            continue
        bucket = values_by_kind[token.kind]
        if not any(abs(existing - token.value) <= max(0.01, abs(token.value) * 1e-12) for existing in bucket):
            bucket.append(token.value)

    tokens: list[NumericToken] = []
    for kind, values in values_by_kind.items():
        if len(values) > 80:
            continue
        for index, left in enumerate(values):
            for right in values[index + 1:]:
                total = left + right
                difference = abs(left - right)
                if total:
                    tokens.append(NumericToken(kind, total, f"{label_prefix}_{kind}_sum"))
                if difference:
                    tokens.append(NumericToken(kind, difference, f"{label_prefix}_{kind}_difference"))
    return tokens


def _unsupported_labels(required: list[NumericToken], supported: list[NumericToken]) -> set[str]:
    return {
        token.label
        for token in required
        if not _is_supported(token, supported)
    }


def numeric_support_objects(all_objects: dict[str, dict]) -> dict[str, dict]:
    """Return the object subset that may be used as numeric support.

    Callers use this shared filter so quality revalidation and pipeline
    validation cannot accidentally validate against different evidence.
    """
    return {
        object_id: obj
        for object_id, obj in all_objects.items()
        if obj.get("type") in _NUMERIC_SUPPORT_TYPES
        or (not obj.get("type") and "value" in obj)
    }


def _build_numeric_support_index(xbrl_facts: dict[str, dict]) -> list[NumericToken]:
    """Build quote-independent support from direct XBRL evidence only.

    Numeric claims must not become valid merely because unrelated numbers can
    be combined. Derived metrics, generic calculated support and model output
    remain report-only unless a future explicit, evidence-bound rule allows
    them.
    """
    supported_numbers: list[NumericToken] = []
    xbrl_values: list[dict] = []

    for fact in xbrl_facts.values():
        fact_type = fact.get("type") or "XBRLFact"
        if fact_type == "NumericEvidence":
            # Quote values must flow through their declared quote support path.
            # Other ledger rows are not an independent canonical source.
            if fact.get("source_method") in {"xbrl_fact", "direct_xbrl"}:
                supported_numbers.append(_numeric_token_from_evidence(fact))
            continue

        if fact_type == "MetricObservation" and fact.get("source_type") != "xbrl":
            continue
        if fact_type not in {"XBRLFact", "MetricObservation"}:
            continue

        value = _parse_float(str(fact.get("value", "")))
        if value is None:
            continue
        unit = str(fact.get("unit", "")).lower()
        label = str(fact["value"])
        if unit == "percent":
            supported_numbers.append(NumericToken("percent", value, label))
        elif unit in {"usd", "usd_per_share"}:
            supported_numbers.append(NumericToken("amount", value, label))
        else:
            supported_numbers.append(NumericToken("number", value, label))

        if fact_type == "XBRLFact":
            xbrl_values.append(fact)

    supported_numbers.extend(_compute_xbrl_yoy_growth_support(xbrl_values))
    return supported_numbers


def _numeric_token_from_evidence(evidence: dict) -> NumericToken:
    """Convert a NumericEvidence ledger row into a validator token."""
    kind = str(evidence.get("numeric_kind") or "number")
    value = _parse_float(str(evidence.get("value", ""))) or 0
    label = str(evidence.get("raw_text") or evidence.get("value") or "")
    return NumericToken(kind, value, label)


def _numeric_evidence_for_quote(all_objects: dict[str, dict], quote_id: str) -> list[NumericToken]:
    """Return ledger tokens tied to one supporting quote."""
    tokens = [
        _numeric_token_from_evidence(obj)
        for obj in all_objects.values()
        if obj.get("type") == "NumericEvidence"
        and obj.get("source_method") == "quote_text"
        and obj.get("source_quote_id") == quote_id
    ]
    return tokens


def _compute_yoy_growth_support(financial_values: list[dict]) -> list[NumericToken]:
    """Compute deterministic adjacent-year growth percentages by metric."""
    by_metric: dict[str, dict[int, float]] = {}
    for row in financial_values:
        metric_name = str(row.get("metric_name") or "")
        fiscal_year = row.get("fiscal_year")
        value = _parse_float(str(row.get("value", "")))
        if not metric_name or not isinstance(fiscal_year, int) or value is None:
            continue
        by_metric.setdefault(metric_name, {})[fiscal_year] = value

    tokens: list[NumericToken] = []
    for metric_name, values_by_year in by_metric.items():
        years = sorted(values_by_year)
        for current_year in years:
            prior_year = current_year - 1
            if prior_year not in values_by_year:
                continue
            prior_value = values_by_year[prior_year]
            if prior_value == 0:
                continue
            growth = ((values_by_year[current_year] - prior_value) / abs(prior_value)) * 100
            label = f"{metric_name}_growth_{current_year}_vs_{prior_year}"
            tokens.extend(_percent_support_variants(growth, label))
    return tokens


def _compute_xbrl_yoy_growth_support(xbrl_values: list[dict]) -> list[NumericToken]:
    """Compute YoY support only for an unambiguous matching XBRL series."""
    by_group: dict[tuple[str, str, str, str, str, tuple[str, ...]], dict[int, set[float]]] = {}
    for fact in xbrl_values:
        context = fact.get("context") or {}
        fiscal_year = context.get("fiscal_year")
        if not isinstance(fiscal_year, int):
            continue
        value = _parse_float(str(fact.get("value", "")))
        if value is None:
            continue
        tag = str(fact.get("safe_taxonomy_tag") or fact.get("taxonomy_tag") or "")
        unit = str(fact.get("unit") or "").lower()
        period_type = _xbrl_period_type(context)
        dimensions = tuple(sorted(str(item) for item in (context.get("dimensions") or [])))
        if not tag or not unit:
            continue
        start = str(context.get("start_date") or "")
        end = str(context.get("end_date") or context.get("instant") or "")
        group = (tag, unit, period_type, _period_shape(start), _period_shape(end), dimensions)
        by_group.setdefault(group, {}).setdefault(fiscal_year, set()).add(value)

    tokens: list[NumericToken] = []
    for (tag, _unit, _period_type, _start, _end, dimensions), values_by_year in by_group.items():
        years = sorted(values_by_year)
        for current_year in years:
            prior_year = current_year - 1
            if prior_year not in values_by_year:
                continue
            # Duplicate source facts for the same semantic period are not safe
            # to choose between automatically.
            if len(values_by_year[current_year]) != 1 or len(values_by_year[prior_year]) != 1:
                continue
            prior_value = next(iter(values_by_year[prior_year]))
            if prior_value == 0:
                continue
            current_value = next(iter(values_by_year[current_year]))
            growth = ((current_value - prior_value) / abs(prior_value)) * 100
            dimension_label = "_".join(dimensions) if dimensions else "consolidated"
            label = f"{tag}_{dimension_label}_growth_{current_year}_vs_{prior_year}"
            tokens.extend(_percent_support_variants(growth, label))
    return tokens


def _xbrl_period_type(context: dict) -> str:
    if context.get("instant"):
        return "instant"
    days = context.get("duration_days")
    if isinstance(days, int):
        if 70 <= days <= 115:
            return "quarter"
        if 160 <= days <= 300:
            return "year_to_date"
        if days >= 330:
            return "annual"
    return str(context.get("period_type") or "duration")


def _period_shape(value: str) -> str:
    """Compare equivalent fiscal windows across years without mixing quarters."""
    return value[5:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else value


def _percent_support_variants(value: float, label: str) -> list[NumericToken]:
    """Support exact and common rounded forms of a code-calculated percentage."""
    signed_floor = math.copysign(math.floor(abs(value)), value)
    signed_ceil = math.copysign(math.ceil(abs(value)), value)
    values = {
        value,
        round(value, 1),
        round(value),
        signed_floor,
        signed_ceil,
    }
    return [
        NumericToken("percent", float(candidate), f"{label}:{candidate:g}%")
        for candidate in values
    ]


def validate_numeric(
    obj: dict, all_quotes: dict[str, dict], xbrl_facts: dict[str, dict]
) -> tuple[bool, str | None]:
    """Every numeric token in research text must be in quote text or XBRL.

    Unsupported numeric values cause rejection (not needs_review).
    If XBRL is unavailable, only quote text matching is checked.
    """
    obj_type = obj.get("type", "")
    fields = _NUMERIC_TEXT_FIELDS.get(obj_type)
    if fields is None:
        return True, None

    numbers: list[NumericToken] = []
    for field in fields:
        value = obj.get(field)
        if value:
            numbers.extend(_extract_numbers(str(value)))

    if not numbers:
        return True, None

    supported_numbers: list[NumericToken] = []
    # Collect numbers from supporting quotes
    quote_ids = obj.get("supported_by_quotes") or []
    for qid in quote_ids:
        quote = all_quotes.get(qid)
        if quote:
            quote_tokens = _numeric_evidence_for_quote(all_quotes, qid)
            if not quote_tokens:
                quote_tokens = _extract_numbers(quote.get("quote_text", ""))
            supported_numbers.extend(quote_tokens)

    # Also check ResearchClaim's supported_by_quotes for objects that have
    # supported_by_claims referencing claims with their own quotes
    claim_ids = obj.get("supported_by_claims") or []
    for cid in claim_ids:
        claim = all_quotes.get(cid)
        if claim and claim.get("type") == "ResearchClaim":
            for qid in claim.get("supported_by_quotes") or []:
                quote = all_quotes.get(qid)
                if quote:
                    quote_tokens = _numeric_evidence_for_quote(all_quotes, qid)
                    if not quote_tokens:
                        quote_tokens = _extract_numbers(quote.get("quote_text", ""))
                    supported_numbers.extend(quote_tokens)

    supported_numbers.extend(_build_numeric_support_index(numeric_support_objects(xbrl_facts)))

    unsupported = _unsupported_labels(numbers, supported_numbers)
    if unsupported:
        return False, f"Unsupported numeric values: {unsupported}"

    return True, None
