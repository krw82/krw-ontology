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
    r"(?P<prefix>[$€£])?(?P<number>\d[\d,]*(?:\.\d+)?)(?P<plus>\+)?"
    r"\s*(?P<unit>%|percent|percentage points?|basis points?|bps|thousand|million|billion|k|m|b)?"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

_ACCOUNTING_AMOUNT_RE = re.compile(
    r"(?P<prefix>[$€£])?\s*\((?P<number>\d[\d,]*(?:\.\d+)?)\)"
    r"\s*(?P<unit>thousand|million|billion|k|m|b)?(?![A-Za-z])",
    re.IGNORECASE,
)

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
    context_multipliers = _context_amount_multipliers(text)

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
                    value,
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
        unit = (match.group("unit") or "").lower()
        prefix = match.group("prefix") or ""
        if _should_ignore_number(text, match, value, unit):
            continue
        token = _make_numeric_token(value, prefix, unit, match.group(0))
        tokens.append(token)
        if not unit and (prefix or "|" in text):
            for multiplier in context_multipliers:
                context_unit = _unit_for_multiplier(multiplier)
                if context_unit:
                    tokens.append(_make_numeric_token(value, prefix, context_unit, match.group(0)))
    return tokens


def _overlaps(span: tuple[int, int], consumed_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < consumed_end and end > consumed_start for consumed_start, consumed_end in consumed_spans)


def _context_amount_multiplier(text: str) -> int | None:
    multipliers = _context_amount_multipliers(text)
    return multipliers[0] if multipliers else None


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
    if re.match(r"\s*(days?|months?|years?)\b", after):
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
            r"\s*(?:to|-|–|—)\s*\d+(?:\.\d+)?\s*(?:days?|months?|years?)\b",
            after,
        )
    )


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
    if next_word in {"countries", "country", "jurisdictions", "regions", "locations"}:
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
        candidate_value = candidate.value
        if required.kind != candidate.kind:
            if required.kind == "percent" and candidate.kind == "number":
                candidate_value = candidate.value
            elif required.kind == "amount" and candidate.kind == "number":
                if required.value >= 1_000_000_000:
                    scaled_values = (candidate.value * 1_000, candidate.value * 1_000_000)
                elif required.value >= 1_000_000:
                    scaled_values = (candidate.value * 1_000_000,)
                else:
                    scaled_values = ()
                tolerance = max(
                    1_000_000,
                    abs(required.value) * 0.00001,
                    required.tolerance or 0,
                    candidate.tolerance or 0,
                )
                if any(
                    abs(required.value - scaled) <= tolerance
                    for scaled in scaled_values
                ):
                    return True
                continue
            else:
                continue
        if required.kind == "amount":
            tolerance = max(
                1_000_000,
                abs(required.value) * 0.00001,
                required.tolerance or 0,
                candidate.tolerance or 0,
            )
            if abs(abs(required.value) - abs(candidate_value)) <= tolerance:
                return True
        elif required.kind == "percent":
            tolerance = 0.05
        else:
            tolerance = max(0.01, abs(required.value) * 0.000001)
            if required.value >= 1_000_000 and candidate.kind == "number":
                scaled_values = (candidate.value * 1_000, candidate.value * 1_000_000)
                if any(abs(required.value - scaled) <= tolerance for scaled in scaled_values):
                    return True
        if abs(required.value - candidate_value) <= tolerance:
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


def _build_numeric_support_index(xbrl_facts: dict[str, dict]) -> list[NumericToken]:
    """Build quote-independent numeric support from facts and code-calculated values.

    The guard accepts three numeric support sources:
    - Raw XBRL facts and canonical financial metric values
    - Explicit DerivedMetricValue rows written by code
    - Additional deterministic YoY growth percentages computed from adjacent
      FinancialMetricValue years

    The last source handles claims such as "R&D expenses increased 10%" when
    the filing provides 2025 and 2024 expense values but not the calculated
    percentage itself.
    """
    supported_numbers: list[NumericToken] = []
    financial_values: list[dict] = []
    xbrl_values: list[dict] = []

    for fact in xbrl_facts.values():
        fact_type = fact.get("type")
        if fact_type == "NumericEvidence":
            # Quote-sourced evidence is only valid through a declared support
            # path. Non-quote evidence comes from XBRL/metric/code rows and may
            # support numeric claims independently.
            if fact.get("source_method") != "quote_text":
                supported_numbers.append(_numeric_token_from_evidence(fact))
            continue

        value = _parse_float(str(fact["value"]))
        if value is None:
            continue
        unit = str(fact.get("unit", "")).lower()
        label = str(fact["value"])
        if unit == "percent":
            supported_numbers.append(NumericToken("percent", value, label))
        elif unit in {"usd", "usd_per_share"} or fact_type in {
            "XBRLFact",
            "FinancialMetricValue",
            "CalculatedNumericSupport",
        }:
            supported_numbers.append(NumericToken("amount", value, label))
            supported_numbers.append(NumericToken("number", value, label))
        else:
            supported_numbers.append(NumericToken("number", value, label))

        if fact_type == "FinancialMetricValue":
            financial_values.append(fact)
        elif fact_type == "XBRLFact":
            xbrl_values.append(fact)

    supported_numbers.extend(_compute_yoy_growth_support(financial_values))
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
    """Compute YoY growth support for matching XBRL facts, including segments."""
    by_group: dict[tuple[str, str, str, tuple[str, ...]], dict[int, float]] = {}
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
        period_type = str(context.get("period_type") or "")
        dimensions = tuple(sorted(str(item) for item in (context.get("dimensions") or [])))
        if not tag or not unit:
            continue
        group = (tag, unit, period_type, dimensions)
        by_group.setdefault(group, {}).setdefault(fiscal_year, value)

    tokens: list[NumericToken] = []
    for (tag, _unit, _period_type, dimensions), values_by_year in by_group.items():
        years = sorted(values_by_year)
        for current_year in years:
            prior_year = current_year - 1
            if prior_year not in values_by_year:
                continue
            prior_value = values_by_year[prior_year]
            if prior_value == 0:
                continue
            growth = ((values_by_year[current_year] - prior_value) / abs(prior_value)) * 100
            dimension_label = "_".join(dimensions) if dimensions else "consolidated"
            label = f"{tag}_{dimension_label}_growth_{current_year}_vs_{prior_year}"
            tokens.extend(_percent_support_variants(growth, label))
    return tokens


def _percent_support_variants(value: float, label: str) -> list[NumericToken]:
    """Support exact and common rounded forms of a code-calculated percentage."""
    signed_floor = math.copysign(math.floor(abs(value)), value)
    signed_ceil = math.copysign(math.ceil(abs(value)), value)
    values = {
        value,
        abs(value),
        round(value, 1),
        abs(round(value, 1)),
        round(value),
        abs(round(value)),
        signed_floor,
        abs(signed_floor),
        signed_ceil,
        abs(signed_ceil),
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
    quote_numbers: list[NumericToken] = []

    # Collect numbers from supporting quotes
    quote_ids = obj.get("supported_by_quotes") or []
    for qid in quote_ids:
        quote = all_quotes.get(qid)
        if quote:
            quote_tokens = _numeric_evidence_for_quote(all_quotes, qid)
            if not quote_tokens:
                quote_tokens = _extract_numbers(quote.get("quote_text", ""))
            supported_numbers.extend(quote_tokens)
            quote_numbers.extend(quote_tokens)

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
                    quote_numbers.extend(quote_tokens)

    supported_numbers.extend(_build_calculated_percent_support(quote_numbers, "quote"))
    supported_numbers.extend(_build_calculated_amount_support(quote_numbers, "quote"))
    supported_numbers.extend(_build_numeric_support_index(xbrl_facts))

    unsupported = _unsupported_labels(numbers, supported_numbers)
    if unsupported:
        return False, f"Unsupported numeric values: {unsupported}"

    return True, None
