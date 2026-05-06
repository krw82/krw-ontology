"""Document-type specific section profiles.

The extraction pipeline is source-grounded across document types, but section
boundaries are not universal. 10-K filings use annual-report item labels, while
10-Q filings reuse item numbers under Part I and Part II.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from krw_ontology.config.constants import denormalize_doc_type, normalize_doc_type


@dataclass(frozen=True)
class SectionProfile:
    document_type: str
    doc_type_key: str
    section_order: tuple[str, ...]
    core_sections: frozenset[str]
    title_prefixes: dict[str, tuple[str, ...]]
    title_contains_all: dict[str, tuple[str, ...]]
    title_contains_any: dict[str, tuple[str, ...]]
    title_to_section: tuple[tuple[str, str], ...] = ()
    item_part_map: dict[tuple[str, str], str] | None = None
    item_title_fallbacks: tuple[tuple[str, str], ...] = ()
    cover_titles: frozenset[str] = frozenset({"cover"})
    part_markers_are_sections: bool = False

    def order_index(self, section_name: str) -> int:
        try:
            return self.section_order.index(section_name)
        except ValueError:
            return 999

    def is_core(self, section_name: str) -> bool:
        return section_name in self.core_sections

    def infer_section_name(self, text: str, current_part: str | None = None) -> str | None:
        normalized = normalize_heading_text(text)
        if not normalized:
            return None

        if normalized in self.cover_titles:
            return "cover" if "cover" in self.section_order else None

        part_marker = part_marker_key(normalized)
        if part_marker and self.part_markers_are_sections and "cover" in self.section_order:
            return "cover"

        item_match = ITEM_LABEL_RE.match(normalized)
        if item_match:
            item_number = item_match.group(1)
            suffix = item_match.group(2).lower()
            item_key = f"item{item_number}{suffix}"
            if self.item_part_map:
                section_name = self.item_part_map.get((current_part or "", item_key))
                if section_name:
                    return section_name
                fallback_section = self._infer_item_from_title(normalized, item_key)
                if fallback_section:
                    return fallback_section
            if item_key in self.section_order:
                return item_key

        for section_name, title in self.title_to_section:
            if normalized == title or normalized.startswith(f"{title} "):
                return section_name
        for section_name in self.section_order:
            if section_name == "cover":
                continue
            if self.title_boundary_match(section_name, normalized):
                return section_name
        return None

    def canonical_title_match(self, section_name: str, text: str) -> bool:
        normalized = normalize_heading_text(text)
        contains_all = self.title_contains_all.get(section_name)
        if contains_all and all(part in normalized for part in contains_all):
            return True
        contains_any = self.title_contains_any.get(section_name)
        if contains_any and any(part in normalized for part in contains_any):
            return True
        return self.title_boundary_match(section_name, normalized)

    def title_boundary_match(self, section_name: str, text: str) -> bool:
        normalized = normalize_heading_text(text)
        if len(normalized) > 180:
            return False
        prefixes = self.title_prefixes.get(section_name, ())
        return any(normalized == prefix or normalized.startswith(f"{prefix} ") for prefix in prefixes)

    def _infer_item_from_title(self, normalized: str, item_key: str) -> str | None:
        title = strip_item_label(normalized)
        for section_name, required_text in self.item_title_fallbacks:
            if item_key in section_name and required_text in title:
                return section_name
        return None


ITEM_LABEL_RE = re.compile(r"(?i)^item\s+(\d{1,2})([a-z]?)(?:[.:—–-]|\s+)")
PART_MARKER_RE = re.compile(r"(?i)^part\s+([ivx]+)\b")


def normalize_heading_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_item_label(text: str) -> str:
    normalized = normalize_heading_text(text)
    item_match = ITEM_LABEL_RE.match(normalized)
    if item_match:
        normalized = normalized[item_match.end():]
    return normalized.strip(" .:—–-")


def part_marker_key(text: str) -> str | None:
    normalized = normalize_heading_text(text)
    match = PART_MARKER_RE.match(normalized)
    if not match:
        return None
    roman = match.group(1).lower()
    return {
        "i": "part1",
        "ii": "part2",
        "iii": "part3",
        "iv": "part4",
    }.get(roman)


TEN_K_PROFILE = SectionProfile(
    document_type="10-K",
    doc_type_key="10K",
    section_order=(
        "cover",
        "item1",
        "item1a",
        "item1b",
        "item1c",
        "item2",
        "item7",
        "item7a",
        "item8",
        "item9a",
        "item9b",
        "item10",
        "item11",
        "item12",
        "item13",
        "item14",
        "item15",
        "item16",
    ),
    core_sections=frozenset({"item1", "item1a", "item7", "item7a", "item8"}),
    title_prefixes={
        "item1": ("business", "business overview", "business summary", "description of the business"),
        "item1a": ("risk factors",),
        "item1b": ("unresolved staff",),
        "item1c": ("cybersecurity",),
        "item2": ("properties",),
        "item7": ("management's discussion",),
        "item7a": ("quantitative and qualitative", "market risk", "financing and market risk"),
        "item8": ("financial statements", "consolidated financial statements", "index to consolidated financial statements"),
        "item9a": ("controls and procedures",),
        "item9b": ("other information",),
        "item10": ("directors", "executive officers"),
        "item11": ("executive compensation",),
        "item12": ("security ownership",),
        "item13": ("certain relationships",),
        "item14": ("principal accountant",),
        "item15": ("exhibits",),
        "item16": ("form 10-k summary",),
    },
    title_contains_all={
        "item7": ("management", "discussion", "analysis"),
        "item7a": ("quantitative", "qualitative"),
    },
    title_contains_any={
        "item1": ("business",),
        "item1a": ("risk factors",),
        "item1b": ("unresolved staff",),
        "item1c": ("cybersecurity",),
        "item2": ("properties",),
        "item7a": ("market risk",),
        "item8": ("financial statements", "supplementary data"),
        "item9a": ("controls", "procedures"),
        "item9b": ("other information",),
        "item10": ("directors", "executive officers", "governance"),
        "item11": ("executive compensation",),
        "item12": ("security ownership",),
        "item13": ("certain relationships", "related transactions"),
        "item14": ("principal accountant", "fees"),
        "item15": ("exhibits", "financial statement schedules"),
        "item16": ("form 10-k summary",),
    },
    cover_titles=frozenset({"part i", "cover"}),
    part_markers_are_sections=True,
)


TEN_Q_PROFILE = SectionProfile(
    document_type="10-Q",
    doc_type_key="10Q",
    section_order=(
        "part1_item1",
        "part1_item2",
        "part1_item3",
        "part1_item4",
        "part2_item1",
        "part2_item1a",
        "part2_item2",
        "part2_item3",
        "part2_item4",
        "part2_item5",
        "part2_item6",
    ),
    core_sections=frozenset({"part1_item1", "part1_item2", "part1_item4", "part2_item1a"}),
    item_part_map={
        ("part1", "item1"): "part1_item1",
        ("part1", "item2"): "part1_item2",
        ("part1", "item3"): "part1_item3",
        ("part1", "item4"): "part1_item4",
        ("part2", "item1"): "part2_item1",
        ("part2", "item1a"): "part2_item1a",
        ("part2", "item2"): "part2_item2",
        ("part2", "item3"): "part2_item3",
        ("part2", "item4"): "part2_item4",
        ("part2", "item5"): "part2_item5",
        ("part2", "item6"): "part2_item6",
        ("", "item1a"): "part2_item1a",
    },
    item_title_fallbacks=(
        ("part1_item1", "financial statements"),
        ("part1_item2", "management"),
        ("part1_item3", "market risk"),
        ("part1_item4", "controls"),
        ("part2_item1", "legal proceedings"),
        ("part2_item1a", "risk factors"),
        ("part2_item2", "unregistered"),
        ("part2_item5", "other information"),
        ("part2_item6", "exhibits"),
    ),
    title_prefixes={
        "part1_item1": ("financial statements", "condensed consolidated financial statements"),
        "part1_item2": ("management's discussion",),
        "part1_item3": ("quantitative and qualitative", "market risk"),
        "part1_item4": ("controls and procedures",),
        "part2_item1": ("legal proceedings",),
        "part2_item1a": ("risk factors",),
        "part2_item2": ("unregistered sales",),
        "part2_item3": ("defaults upon senior securities",),
        "part2_item4": ("mine safety disclosures",),
        "part2_item5": ("other information",),
        "part2_item6": ("exhibits",),
    },
    title_contains_all={
        "part1_item2": ("management", "discussion", "analysis"),
        "part1_item3": ("quantitative", "qualitative"),
        "part2_item2": ("unregistered", "sales"),
    },
    title_contains_any={
        "part1_item1": ("financial statements",),
        "part1_item3": ("market risk",),
        "part1_item4": ("controls", "procedures"),
        "part2_item1": ("legal proceedings",),
        "part2_item1a": ("risk factors",),
        "part2_item5": ("other information",),
        "part2_item6": ("exhibits",),
    },
)


DOCUMENT_PROFILES = {
    "10-K": TEN_K_PROFILE,
    "10-Q": TEN_Q_PROFILE,
}


def get_document_profile(document_type: str) -> SectionProfile:
    if document_type in DOCUMENT_PROFILES:
        return DOCUMENT_PROFILES[document_type]
    try:
        display = denormalize_doc_type(normalize_doc_type(document_type))
    except KeyError as exc:
        raise ValueError(f"Unsupported document type: {document_type}") from exc
    if display not in DOCUMENT_PROFILES:
        raise ValueError(f"Unsupported document type: {document_type}")
    return DOCUMENT_PROFILES[display]


def supported_document_types() -> set[str]:
    return set(DOCUMENT_PROFILES)
