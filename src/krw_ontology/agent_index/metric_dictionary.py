"""Canonical metric dictionary used by every serving-index layer.

The ontology YAML is the only source of metric identities.  This module turns
that immutable input into deterministic canonical, alias, and XBRL lookups and
exposes the content binding persisted in index/release metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from krw_ontology.validators.metric_validator import _metric_dictionary_path


METRIC_DICTIONARY_BINDING_FORMAT = "krw-ontology-metric-dictionary-binding/v1"


def _identity_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^0-9A-Za-z]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_").casefold()


def normalize_dimension_key(value: Any) -> str:
    """Normalize an axis/member label to the exact serving-index key shape."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("#")[-1].split("/")[-1].split(":")[-1]
    text = re.sub(r"(?:Member|Axis|Domain)$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"[^0-9A-Za-z]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_").casefold()


def _unicode_alias_key(value: Any) -> str:
    """Fallback alias key for non-ASCII (for example Korean) aliases.

    ``_identity_key`` reduces any script that is not ASCII alphanumeric to an
    empty string, which would silently drop every Korean alias.  Unicode-only
    aliases are therefore keyed by their NFC-normalized, case-folded text so
    ``canonicalize`` keeps working across scripts.  Mixed-script aliases must
    not rely on this: their ASCII residue (for example ``실질GDP`` -> ``gdp``)
    is ambiguous by construction, so the dictionary keeps Korean aliases pure.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"\s+", " ", text)


def _xbrl_keys(value: Any) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    local = text.split("#")[-1].split("/")[-1].split(":")[-1]
    keys = {_identity_key(text), _identity_key(local)}
    # Safe taxonomy tags commonly replace the namespace separator with an
    # underscore (for example ``us_gaap_Revenues``).  The dictionary still
    # remains the source: this only normalizes representations of its tag.
    safe_match = re.match(r"^[A-Za-z0-9]+_[A-Za-z0-9]+_(.+)$", text)
    if safe_match:
        keys.add(_identity_key(safe_match.group(1)))
    return tuple(sorted(key for key in keys if key))


_ALIAS_TERM_SEPARATOR = r"[\s\-_]+"


def _alias_term_pattern(written: str) -> re.Pattern[str] | None:
    """Compile a static alias term into a word-boundary match pattern.

    Separator runs inside the written form (spaces, underscores, hyphens)
    match any separator run in the text, so both ``debt load`` and
    ``debt_load`` match the phrase ``debt load`` / ``debt-load``.  English
    terms take an optional plural ``s`` without enumerating plural forms in
    the dictionary; Korean terms are left untouched.
    """
    parts = [part for part in re.split(_ALIAS_TERM_SEPARATOR, written.casefold()) if part]
    if not parts:
        return None
    body = _ALIAS_TERM_SEPARATOR.join(re.escape(part) for part in parts)
    last_char = parts[-1][-1]
    if "a" <= last_char <= "z" and last_char != "s":
        body += "s?"
    try:
        return re.compile(rf"\b{body}\b", flags=re.IGNORECASE)
    except re.error:
        return None


@dataclass(frozen=True)
class MetricAliasTerm:
    """One static alias entry: written form, canonical target, match pattern."""

    written: str
    canonical: str
    pattern: re.Pattern[str] = field(compare=False, repr=False)


@dataclass(frozen=True)
class MetricDictionaryCatalog:
    path: Path
    schema_version: str
    sha256: str
    entries: Mapping[str, Mapping[str, Any]]
    aliases: Mapping[str, str]
    xbrl_tags: Mapping[str, str]
    alias_terms: tuple[MetricAliasTerm, ...] = ()

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "format": METRIC_DICTIONARY_BINDING_FORMAT,
            "schema_version": self.schema_version,
            "sha256": self.sha256,
            "canonical_metric_count": len(self.entries),
        }

    def canonicalize(self, value: Any) -> str | None:
        canonical = self.aliases.get(_identity_key(value))
        if canonical is not None:
            return canonical
        return self.aliases.get(_unicode_alias_key(value))

    def canonicalize_xbrl_tag(self, value: Any) -> str | None:
        for key in _xbrl_keys(value):
            canonical = self.xbrl_tags.get(key)
            if canonical:
                return canonical
        return None

    def aliases_for(self, canonical: str) -> tuple[str, ...]:
        entry = self.entries.get(canonical)
        if not isinstance(entry, Mapping):
            return ()
        values = [canonical, str(entry.get("display_name") or "")]
        values.extend(str(value) for value in entry.get("aliases") or [])
        values.extend(str(value) for value in entry.get("xbrl_tags") or [])
        return tuple(dict.fromkeys(value for value in values if value))

    def resolve_alias_terms(self, text: str) -> dict[str, str]:
        """Resolve static alias terms in free text to canonical metric ids.

        Matching is case-insensitive with word boundaries and plural forms;
        several distinct hits are returned.  Terms are visited longest-first,
        so an overlapping shorter alias (``earnings`` inside
        ``earnings_per_share``) never shadows its longer form.  The result is
        a pure function of (dictionary, text), sorted by written alias.
        """
        if not text:
            return {}
        resolved: dict[str, str] = {}
        accepted_spans: list[tuple[int, int]] = []
        for term in self.alias_terms:
            for match in term.pattern.finditer(text):
                start, end = match.span()
                if any(start < span_end and span_start < end for span_start, span_end in accepted_spans):
                    continue
                accepted_spans.append((start, end))
                resolved.setdefault(term.written, term.canonical)
        return dict(sorted(resolved.items()))


@lru_cache(maxsize=8)
def _load_catalog(path_text: str, size: int, mtime_ns: int) -> MetricDictionaryCatalog:
    del size, mtime_ns  # cache-key material; content is read exactly once per revision.
    path = Path(path_text)
    raw = path.read_bytes()
    # Import lazily so importing the serving SDK does not parse YAML unless a
    # metric path is actually used.
    import yaml

    payload = yaml.safe_load(raw) or {}
    raw_entries = payload.get("canonical_metrics")
    if not isinstance(raw_entries, Mapping) or not raw_entries:
        raise ValueError(f"metric dictionary has no canonical_metrics: {path}")

    entries: dict[str, Mapping[str, Any]] = {}
    aliases: dict[str, str] = {}
    xbrl_tags: dict[str, str] = {}
    alias_written: dict[str, str] = {}
    for raw_canonical, raw_entry in raw_entries.items():
        canonical = _identity_key(raw_canonical)
        if not canonical:
            raise ValueError(f"metric dictionary contains an empty canonical id: {path}")
        if canonical in entries:
            raise ValueError(f"duplicate canonical metric {canonical!r}: {path}")
        entry = dict(raw_entry) if isinstance(raw_entry, Mapping) else {}
        entries[canonical] = MappingProxyType(entry)
        # Text-matching terms cover the canonical id and every listed alias
        # exactly as written in the dictionary (display names and XBRL tags
        # stay out of free-text matching).
        for written in (str(raw_canonical), *(str(value) for value in entry.get("aliases") or [])):
            written = written.strip()
            if not written:
                continue
            previous = alias_written.setdefault(written, canonical)
            if previous != canonical:
                raise ValueError(
                    f"metric dictionary alias {written!r} maps to both {previous!r} and {canonical!r}"
                )
        alias_values = [raw_canonical, entry.get("display_name"), *(entry.get("aliases") or [])]
        for alias in alias_values:
            key = _identity_key(alias)
            if not key:
                # Non-ASCII aliases (Korean display terms) survive only under a
                # Unicode key; anything empty under both schemes is skipped.
                key = _unicode_alias_key(alias)
            if not key:
                continue
            previous = aliases.setdefault(key, canonical)
            if previous != canonical:
                raise ValueError(
                    f"metric dictionary alias {alias!r} maps to both {previous!r} and {canonical!r}"
                )
        for tag in entry.get("xbrl_tags") or []:
            for key in _xbrl_keys(tag):
                previous = xbrl_tags.setdefault(key, canonical)
                if previous != canonical:
                    raise ValueError(
                        f"metric dictionary XBRL tag {tag!r} maps to both {previous!r} "
                        f"and {canonical!r}"
                    )

    alias_terms = tuple(
        sorted(
            (
                MetricAliasTerm(written=written, canonical=canonical, pattern=pattern)
                for written, canonical in alias_written.items()
                if (pattern := _alias_term_pattern(written)) is not None
            ),
            key=lambda term: (-len(term.written), term.written, term.canonical),
        )
    )

    return MetricDictionaryCatalog(
        path=path,
        schema_version=str(payload.get("schema_version") or ""),
        sha256=hashlib.sha256(raw).hexdigest(),
        entries=MappingProxyType(entries),
        aliases=MappingProxyType(aliases),
        xbrl_tags=MappingProxyType(xbrl_tags),
        alias_terms=alias_terms,
    )


def metric_dictionary_catalog(path: Path | None = None) -> MetricDictionaryCatalog:
    resolved = _metric_dictionary_path(path).expanduser().resolve()
    stat = resolved.stat()
    return _load_catalog(str(resolved), stat.st_size, stat.st_mtime_ns)


def metric_dictionary_binding(path: Path | None = None) -> dict[str, Any]:
    return metric_dictionary_catalog(path).binding


def metric_dictionary_binding_errors(
    value: Any,
    *,
    expected: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return ["metric_dictionary_binding_missing"]
    expected_binding = dict(expected or metric_dictionary_binding())
    errors: list[str] = []
    for key in ("format", "schema_version", "sha256", "canonical_metric_count"):
        if value.get(key) != expected_binding.get(key):
            errors.append(f"metric_dictionary_{key}_mismatch")
    return errors


def canonical_metric_name(value: Any) -> str:
    """Return a dictionary canonical id, or a normalized unknown id."""
    return metric_dictionary_catalog().canonicalize(value) or _identity_key(value)


def canonical_metric_for_xbrl_tag(value: Any) -> str | None:
    return metric_dictionary_catalog().canonicalize_xbrl_tag(value)


def metric_aliases(canonical: str) -> tuple[str, ...]:
    return metric_dictionary_catalog().aliases_for(canonical)


def resolve_alias_terms(text: str) -> dict[str, str]:
    """Resolve alias terms in ``text`` against the packaged default dictionary."""
    return metric_dictionary_catalog().resolve_alias_terms(text)


def stable_binding_json(binding: Mapping[str, Any] | None = None) -> str:
    return json.dumps(
        dict(binding or metric_dictionary_binding()),
        sort_keys=True,
        separators=(",", ":"),
    )
