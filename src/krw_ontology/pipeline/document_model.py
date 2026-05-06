"""Canonical document node and section boundary helpers.

This module keeps section detection deterministic. It may use raw HTML as
evidence for heading/table signals, but final boundaries are selected by code
from explicit candidates and offsets.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from krw_ontology.config.constants import SECTION_PATTERNS
from krw_ontology.pipeline.document_profiles import (
    SectionProfile,
    get_document_profile,
    part_marker_key,
)


_DEFAULT_PROFILE = get_document_profile("10-K")
CORE_SECTIONS = set(_DEFAULT_PROFILE.core_sections)

_ITEM_ORDER = [
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
]

_CANONICAL_TITLE_HINTS = {
    "item1": ("business",),
    "item1a": ("risk factors",),
    "item1b": ("unresolved staff",),
    "item1c": ("cybersecurity",),
    "item2": ("properties",),
    "item7": ("management", "discussion", "analysis"),
    "item7a": ("quantitative", "qualitative", "market risk"),
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
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PAGE_NUMBER_RE = re.compile(r"(?:\.{2,}|\s{2,})\s*\d+\s*$")
_ITEM_LABEL_ANY_RE = re.compile(r"(?i)^item\s+(\d{1,2})([a-z]?)(?:[.:—–-]|\s+)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_SELECTABLE_SCORE = 40
_ADJACENT_MERGE_NODE_GAP = 3
_ADJACENT_MERGE_CHAR_GAP = 1200
_PREMATURE_REORDER_WINDOW_CHARS = 50000


def build_document_nodes(
    markdown_text: str,
    raw_html_text: str | None = None,
    document_type: str = "10-K",
) -> list[dict[str, Any]]:
    """Build stable document nodes from clean markdown, enriched with HTML hints."""
    profile = get_document_profile(document_type)
    html_features = _collect_html_features(raw_html_text) if raw_html_text else {}
    blocks = _markdown_blocks(markdown_text)
    nodes: list[dict[str, Any]] = []

    for idx, block in enumerate(blocks):
        raw_text = block["raw_text"].strip()
        if not raw_text:
            continue

        heading_match = _HEADING_RE.match(raw_text)
        heading_level = len(heading_match.group(1)) if heading_match else None
        display_text = heading_match.group(2).strip() if heading_match else raw_text
        normalized = _normalize_text(display_text)
        features = html_features.get(normalized, {})

        html_heading_level = features.get("html_heading_level")
        node_type = "heading" if heading_level or html_heading_level else "paragraph"
        markdown_table_row = raw_text.startswith("|") and raw_text.endswith("|")
        if raw_text.startswith("|") and raw_text.endswith("|"):
            node_type = "table_row"
        if features.get("in_table") and node_type != "heading":
            node_type = "table_cell"

        nodes.append({
            "id": f"node:{idx:06d}",
            "node_index": idx,
            "node_type": node_type,
            "text": display_text,
            "raw_text": raw_text,
            "start_char": block["start_char"],
            "end_char": block["end_char"],
            "start_line": block["start_line"],
            "end_line": block["end_line"],
            "heading_level": heading_level or html_heading_level,
            "html_tag": features.get("html_tag"),
            "is_bold": bool(features.get("is_bold", False)),
            "in_table": markdown_table_row or bool(features.get("in_table", False)),
            "text_hash": _hash_text(display_text),
        })

    if raw_html_text:
        nodes.extend(_html_candidate_nodes(markdown_text, raw_html_text, nodes, profile))
        nodes = _renumber_nodes(nodes)

    return nodes


def build_section_candidates(
    nodes: list[dict[str, Any]],
    document_type: str = "10-K",
) -> list[dict[str, Any]]:
    """Find all section heading candidates before selecting boundaries."""
    profile = get_document_profile(document_type)
    candidates: list[dict[str, Any]] = []
    current_part: str | None = None
    for node in nodes:
        text = _candidate_text(node["text"])
        if not text:
            continue
        detected_part = part_marker_key(text)
        if detected_part:
            current_part = detected_part
        section_name = _infer_section_name(text, profile, current_part)
        if section_name is None:
            if profile.document_type == "10-K":
                for pattern_section_name, pattern in SECTION_PATTERNS.items():
                    if re.match(pattern, text):
                        section_name = pattern_section_name
                        break
        if section_name is None:
            continue
        candidate = _make_candidate(node, section_name, text, profile)
        if not candidate["is_heading_like"] and not _ITEM_LABEL_ANY_RE.match(text):
            continue
        if (
            section_name == "item1"
            and not _ITEM_LABEL_ANY_RE.match(text)
            and _normalize_text(text) != "business"
            and not _title_boundary_match("item1", text, profile)
        ):
            continue
        candidates.append(candidate)

    _mark_toc_density(candidates, nodes)
    for candidate in candidates:
        candidate["score"] = _score_candidate(candidate)
    return candidates


def select_section_boundaries(
    markdown_text: str,
    candidates: list[dict[str, Any]],
    document_type: str = "10-K",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Select section boundaries from explicit candidates and return audit rows."""
    profile = get_document_profile(document_type)
    if not candidates:
        return [], [], _section_quality([], profile)

    ordered = sorted(candidates, key=lambda c: (c["start_char"], _order_index(c["section_name"], profile)))
    prelim = [c for c in ordered if _is_selectable_boundary(c)]
    prelim = _drop_premature_out_of_order_boundaries(prelim, profile)
    prelim = _merge_adjacent_section_headings(prelim, profile)

    selected = _select_source_order_boundary_path(prelim, profile)

    selected = sorted(selected, key=lambda c: c["start_char"])
    section_counts: dict[str, int] = {}
    sections: list[dict[str, Any]] = []
    selected_ids = {c["candidate_id"] for c in selected}
    audit: list[dict[str, Any]] = []

    for idx, candidate in enumerate(selected):
        end_char = selected[idx + 1]["start_char"] if idx + 1 < len(selected) else len(markdown_text)
        body_char_count = max(0, end_char - candidate["start_char"])
        section_name = candidate["section_name"]
        section_instance = section_counts.get(section_name, 0)
        section_counts[section_name] = section_instance + 1
        section_key = f"{section_name}_{section_instance:02d}"
        confidence = _confidence(candidate["score"], body_char_count, section_name, profile)

        sections.append({
            "name": section_name,
            "number": section_name,
            "section_key": section_key,
            "section_instance": section_instance,
            "start_line": candidate["start_line"],
            "end_line": selected[idx + 1]["start_line"] - 1 if idx + 1 < len(selected) else None,
            "start_char": candidate["start_char"],
            "end_char": end_char,
            "text": markdown_text[candidate["start_char"]:end_char],
            "section_detection_confidence": confidence,
            "section_detection_method": "document_node_boundary_scoring",
            "boundary_score": candidate["score"],
            "start_node_id": candidate["node_id"],
            "end_node_id": selected[idx + 1]["node_id"] if idx + 1 < len(selected) else None,
        })

    for candidate in ordered:
        audit.append({
            **candidate,
            "selected": candidate["candidate_id"] in selected_ids,
            "rejection_reason": None if candidate["candidate_id"] in selected_ids else _candidate_rejection_reason(candidate),
        })

    return sections, audit, _section_quality(sections, profile)


def _markdown_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    blocks: list[dict[str, Any]] = []
    current: list[str] = []
    current_start_line = 0
    current_start_char = 0
    char_offset = 0

    def flush(end_line: int, end_char: int) -> None:
        nonlocal current
        if current:
            blocks.append({
                "raw_text": "".join(current),
                "start_line": current_start_line,
                "end_line": end_line,
                "start_char": current_start_char,
                "end_char": end_char,
            })
            current = []

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush(line_idx - 1, char_offset)
            char_offset += len(line)
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush(line_idx - 1, char_offset)
            blocks.append({
                "raw_text": line,
                "start_line": line_idx,
                "end_line": line_idx,
                "start_char": char_offset,
                "end_char": char_offset + len(line),
            })
            char_offset += len(line)
            continue

        if not current:
            current_start_line = line_idx
            current_start_char = char_offset
        current.append(line)
        char_offset += len(line)

    flush(len(lines) - 1, char_offset)
    return blocks


def _collect_html_features(raw_html_text: str | None) -> dict[str, dict[str, Any]]:
    if not raw_html_text:
        return {}
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(raw_html_text, "lxml")
    features: dict[str, dict[str, Any]] = {}
    tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"]
    for tag in soup.find_all(tags):
        text = tag.get_text(" ", strip=True)
        if len(text) < 2 or len(text) > 2000:
            continue
        key = _normalize_text(text)
        if not key:
            continue
        heading_level = int(tag.name[1]) if tag.name.startswith("h") and tag.name[1:].isdigit() else None
        in_table = bool(tag.find_parent("table") or tag.name in {"td", "th"})
        is_bold = bool(tag.find(["b", "strong"]) or tag.name in {"th", "h1", "h2", "h3", "h4", "h5", "h6"})
        current = features.get(key)
        if not current:
            features[key] = {
                "html_tag": tag.name,
                "html_heading_level": heading_level,
                "in_table": in_table,
                "is_bold": is_bold,
            }
            continue
        current["html_heading_level"] = current.get("html_heading_level") or heading_level
        current["in_table"] = bool(current.get("in_table")) and in_table
        current["is_bold"] = bool(current.get("is_bold")) or is_bold
        if heading_level:
            current["html_tag"] = tag.name
    return features


def _html_candidate_nodes(
    markdown_text: str,
    raw_html_text: str,
    existing_nodes: list[dict[str, Any]],
    profile: SectionProfile,
) -> list[dict[str, Any]]:
    """Recover section-heading nodes that markdown conversion may flatten."""
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    soup = BeautifulSoup(raw_html_text, "lxml")
    nodes: list[dict[str, Any]] = []
    existing_offsets = [node["start_char"] for node in existing_nodes]
    tags = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "td", "th"]
    seen_offsets: set[tuple[int, str]] = set()

    for tag in soup.find_all(tags):
        text = tag.get_text(" ", strip=True)
        candidate_text = _candidate_text(text)
        if len(candidate_text) < 4 or len(candidate_text) > 260:
            continue
        section_name = _infer_section_name(candidate_text, profile, None)
        if section_name is None:
            continue
        in_table = bool(tag.find_parent("table") or tag.name in {"td", "th"})
        if in_table and not _ITEM_LABEL_ANY_RE.match(candidate_text):
            continue
        offset = _find_markdown_offset(markdown_text, candidate_text)
        if offset is None:
            continue
        if _is_markdown_link_at_offset(markdown_text, offset, candidate_text):
            continue
        if any(abs(offset - existing_offset) <= 2 for existing_offset in existing_offsets):
            continue
        dedupe_key = (offset, section_name)
        if dedupe_key in seen_offsets:
            continue
        seen_offsets.add(dedupe_key)

        heading_level = int(tag.name[1]) if tag.name.startswith("h") and tag.name[1:].isdigit() else None
        is_bold = bool(tag.find(["b", "strong"]) or tag.name in {"th", "h1", "h2", "h3", "h4", "h5", "h6"})
        node_type = "heading" if heading_level or is_bold or _ITEM_LABEL_ANY_RE.match(candidate_text) else "paragraph"
        start_line = _line_for_offset(markdown_text, offset)
        nodes.append({
            "id": f"node:html:{len(nodes):06d}",
            "node_index": len(existing_nodes) + len(nodes),
            "node_type": node_type,
            "text": candidate_text,
            "raw_text": candidate_text,
            "start_char": offset,
            "end_char": offset + len(candidate_text),
            "start_line": start_line,
            "end_line": start_line,
            "heading_level": heading_level,
            "html_tag": tag.name,
            "is_bold": is_bold,
            "in_table": in_table,
            "text_hash": _hash_text(candidate_text),
            "source": "raw_html_heading",
        })
    return nodes


def _renumber_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(nodes, key=lambda node: (node["start_char"], node["end_char"], node.get("id", "")))
    for idx, node in enumerate(ordered):
        node["node_index"] = idx
        node["id"] = f"node:{idx:06d}"
    return ordered


def _make_candidate(
    node: dict[str, Any],
    section_name: str,
    text: str,
    profile: SectionProfile,
) -> dict[str, Any]:
    lowered = text.lower()
    title_match = _canonical_title_match(section_name, lowered, profile)
    starts_with_item_label = bool(_ITEM_LABEL_ANY_RE.match(text))
    title_only_boundary = _title_boundary_match(section_name, text, profile)
    is_heading_like = (
        node["node_type"] in {"heading", "table_row"}
        or bool(node.get("is_bold"))
        or starts_with_item_label
        or title_only_boundary
    )
    has_page_number = bool(
        _PAGE_NUMBER_RE.search(text)
        or (starts_with_item_label and re.search(r"\s+\d{1,3}\s*$", text))
        or (
            node.get("in_table")
            and title_only_boundary
            and re.search(r"\s+\d{1,3}\s*$", text)
        )
    )
    is_cross_reference = _is_cross_reference_candidate(text)
    is_toc_like = bool(has_page_number)
    raw = f"{node['id']}|{section_name}|{text}"
    return {
        "candidate_id": "section_candidate:" + hashlib.sha1(raw.encode()).hexdigest()[:12],
        "section_name": section_name,
        "node_id": node["id"],
        "node_index": node["node_index"],
        "start_char": node["start_char"],
        "end_char": node["end_char"],
        "start_line": node["start_line"],
        "candidate_text": text,
        "raw_text": node.get("raw_text", text),
        "is_heading_like": is_heading_like,
        "is_toc_like": is_toc_like,
        "is_cross_reference": is_cross_reference,
        "in_table": bool(node.get("in_table")),
        "has_page_number": has_page_number,
        "canonical_title_match": title_match,
        "title_boundary_match": title_only_boundary,
        "html_tag": node.get("html_tag"),
        "heading_level": node.get("heading_level"),
        "source": node.get("source", "markdown"),
        "order_index": profile.order_index(section_name),
        "is_core_section": profile.is_core(section_name),
        "score": 0,
    }


def _mark_toc_density(candidates: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    first_body_window = max(12, int(len(nodes) * 0.2))
    for candidate in candidates:
        nearby = [
            c for c in candidates
            if abs(c["node_index"] - candidate["node_index"]) <= 12
        ]
        dense_item_list = len({c["section_name"] for c in nearby}) >= 4
        early = candidate["node_index"] <= first_body_window
        nearby_page_numbers = any(c.get("has_page_number") for c in nearby)
        toc_signal = bool(
            candidate.get("has_page_number")
            or "](" in candidate.get("raw_text", "")
            or (
                candidate.get("source") == "raw_html_heading"
                and candidate.get("in_table")
            )
        )
        prior_text = " ".join(
            nodes[i]["text"].lower()
            for i in range(max(0, candidate["node_index"] - 8), candidate["node_index"] + 1)
        )
        explicit_toc_context = "table of contents" in prior_text or "contents" == prior_text.strip()
        if (
            early
            and dense_item_list
            and toc_signal
            and len(candidate["candidate_text"]) <= 220
            and (
                nearby_page_numbers or explicit_toc_context
            )
        ):
            candidate["is_toc_like"] = True


def _score_candidate(candidate: dict[str, Any]) -> int:
    score = 0
    text = candidate["candidate_text"]
    if _ITEM_LABEL_ANY_RE.match(text):
        score += 35
    else:
        score += 15
    if candidate["is_heading_like"]:
        score += 20
    if candidate["canonical_title_match"]:
        score += 25
    if candidate.get("heading_level"):
        score += 10
    if candidate.get("is_core_section"):
        score += 5
    if candidate.get("source") == "raw_html_heading":
        score += 5
    if candidate["is_toc_like"]:
        score -= 70
    if candidate.get("is_cross_reference"):
        score -= 70
    if candidate["in_table"]:
        strong_table_heading = candidate["canonical_title_match"] and (
            _ITEM_LABEL_ANY_RE.match(text)
            or candidate.get("title_boundary_match")
        )
        score -= 5 if strong_table_heading else 35
    if not candidate["is_heading_like"]:
        score -= 25
    if len(text) > 500:
        score -= 20
    return score


def _is_selectable_boundary(candidate: dict[str, Any]) -> bool:
    if candidate["is_toc_like"] or candidate.get("is_cross_reference"):
        return False
    return candidate["score"] >= _SELECTABLE_SCORE


def _merge_adjacent_section_headings(
    candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> list[dict[str, Any]]:
    """Merge split item labels and title-only continuations for the same section."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (c["start_char"], _order_index(c["section_name"], profile)))
    merged: list[dict[str, Any]] = []
    idx = 0
    while idx < len(ordered):
        current = dict(ordered[idx])
        idx += 1
        while idx < len(ordered) and _should_merge_adjacent(current, ordered[idx]):
            nxt = ordered[idx]
            current["candidate_id"] = current["candidate_id"] + "+" + nxt["candidate_id"].rsplit(":", 1)[-1]
            current["end_char"] = max(current["end_char"], nxt["end_char"])
            current["candidate_text"] = f"{current['candidate_text']} / {nxt['candidate_text']}"
            current["canonical_title_match"] = bool(current["canonical_title_match"] or nxt["canonical_title_match"])
            current["title_boundary_match"] = bool(
                current.get("title_boundary_match") or nxt.get("title_boundary_match")
            )
            current["is_heading_like"] = bool(current["is_heading_like"] or nxt["is_heading_like"])
            current["heading_level"] = current.get("heading_level") or nxt.get("heading_level")
            current["html_tag"] = current.get("html_tag") or nxt.get("html_tag")
            current["source"] = current.get("source") if current.get("source") == nxt.get("source") else "merged"
            current["score"] = min(95, max(current["score"], nxt["score"]) + 15)
            idx += 1
        merged.append(current)
    return merged


def _drop_premature_out_of_order_boundaries(
    candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> list[dict[str, Any]]:
    """Remove early late-item headings when core body headings appear later."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (c["start_char"], _order_index(c["section_name"], profile)))
    keep: list[dict[str, Any]] = []
    for idx, candidate in enumerate(ordered):
        if _has_later_body_duplicate(candidate, ordered[idx + 1:], profile):
            continue
        if _is_premature_late_item_before_core(candidate, ordered[idx + 1:], profile):
            continue
        keep.append(candidate)
    return keep


def _is_premature_late_item_before_core(
    candidate: dict[str, Any],
    later_candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> bool:
    if "item9a" not in profile.section_order or "item7" not in profile.section_order or "item8" not in profile.section_order:
        return False
    if _order_index(candidate["section_name"], profile) < _order_index("item9a", profile):
        return False
    return any(
        _order_index("item7", profile) <= _order_index(later["section_name"], profile) <= _order_index("item8", profile)
        and later["score"] >= 60
        for later in later_candidates
    )


def _has_later_body_duplicate(
    candidate: dict[str, Any],
    later_candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> bool:
    if candidate.get("source") != "raw_html_heading":
        return False
    if _ITEM_LABEL_ANY_RE.match(candidate["candidate_text"]):
        return False
    last_primary_order = _order_index("item8", profile) if "item8" in profile.section_order else max(
        (profile.order_index(name) for name in profile.core_sections),
        default=999,
    )
    if _order_index(candidate["section_name"], profile) > last_primary_order:
        return False
    return any(
        later["section_name"] == candidate["section_name"]
        and later["start_char"] - candidate["start_char"] > _PREMATURE_REORDER_WINDOW_CHARS
        and later["score"] >= candidate["score"] - 15
        for later in later_candidates
    )


def _should_merge_adjacent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["section_name"] != right["section_name"]:
        return False
    if right["node_index"] - left["node_index"] > _ADJACENT_MERGE_NODE_GAP:
        return False
    if right["start_char"] - left["end_char"] > _ADJACENT_MERGE_CHAR_GAP:
        return False
    left_item = bool(_ITEM_LABEL_ANY_RE.match(left["candidate_text"]))
    right_item = bool(_ITEM_LABEL_ANY_RE.match(right["candidate_text"]))
    return left_item != right_item or left["canonical_title_match"] != right["canonical_title_match"]


def _same_boundary_position(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return abs(left["start_char"] - right["start_char"]) <= 2


def _candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(candidate.get("canonical_title_match", False)),
        int(candidate.get("score", 0)),
        -int(candidate.get("in_table", False)),
    )


def _select_source_order_boundary_path(
    candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> list[dict[str, Any]]:
    """Choose one high-quality boundary per item while preserving source order.

    Most 10-K filings follow SEC item order, but some annual-report-style
    filings use a cross-reference index and place sections such as Risk Factors
    after MD&A. Source order plus duplicate suppression keeps those sections
    instead of forcing a canonical SEC ordering that can reduce recall.
    """
    if not candidates:
        return []

    ordered = _dedupe_same_boundary_positions(candidates, profile)
    best_by_section: dict[str, dict[str, Any]] = {}
    for candidate in ordered:
        section_name = candidate["section_name"]
        current = best_by_section.get(section_name)
        if current is not None and _section_duplicate_rank(current, profile) >= _section_duplicate_rank(candidate, profile):
            continue
        best_by_section[section_name] = candidate
    _prefer_item7a_after_item7(best_by_section, ordered, profile)
    return sorted(best_by_section.values(), key=lambda c: c["start_char"])


def _dedupe_same_boundary_positions(
    candidates: list[dict[str, Any]],
    profile: SectionProfile,
) -> list[dict[str, Any]]:
    ordered = sorted(candidates, key=lambda c: (c["start_char"], _order_index(c["section_name"], profile)))
    deduped: list[dict[str, Any]] = []
    for candidate in ordered:
        if deduped and _same_boundary_position(candidate, deduped[-1]):
            if _candidate_rank(candidate) > _candidate_rank(deduped[-1]):
                deduped[-1] = candidate
            continue
        deduped.append(candidate)
    return deduped


def _section_duplicate_rank(
    candidate: dict[str, Any],
    profile: SectionProfile,
) -> tuple[int, int, int, int, int]:
    text = candidate["candidate_text"]
    return (
        int(_is_primary_section_heading(candidate, profile)),
        int(_ITEM_LABEL_ANY_RE.match(text) is not None),
        int(_title_boundary_match(candidate["section_name"], text, profile)),
        -int(candidate["start_char"]),
        int(candidate.get("score", 0)),
    )


def _prefer_item7a_after_item7(
    best_by_section: dict[str, dict[str, Any]],
    ordered: list[dict[str, Any]],
    profile: SectionProfile,
) -> None:
    if "item7" not in profile.section_order or "item7a" not in profile.section_order:
        return
    item7 = best_by_section.get("item7")
    item7a = best_by_section.get("item7a")
    if not item7 or not item7a or item7a["start_char"] > item7["start_char"]:
        return
    later_item7a = [
        candidate for candidate in ordered
        if candidate["section_name"] == "item7a"
        and candidate["start_char"] > item7["start_char"]
    ]
    if later_item7a:
        best_by_section["item7a"] = max(later_item7a, key=lambda c: _section_duplicate_rank(c, profile))


def _is_primary_section_heading(candidate: dict[str, Any], profile: SectionProfile) -> bool:
    text = candidate["candidate_text"]
    if not candidate.get("canonical_title_match"):
        return False
    title = _section_title_without_item_label(text)
    if re.search(r"\s[-:]\s|[—–]", title):
        return False
    return _title_boundary_match(candidate["section_name"], title, profile)


def _section_title_without_item_label(text: str) -> str:
    normalized = _normalize_text(text).replace("’", "'")
    item_match = _ITEM_LABEL_ANY_RE.match(normalized)
    if item_match:
        normalized = normalized[item_match.end():]
    return normalized.strip(" .:—–-")


def _candidate_rejection_reason(candidate: dict[str, Any]) -> str:
    if candidate.get("is_cross_reference"):
        return "cross_reference_candidate"
    if candidate["is_toc_like"]:
        return "toc_like_candidate"
    if candidate["in_table"]:
        return "table_candidate"
    if candidate["score"] < 20:
        return "low_score"
    return "not_selected"


def _confidence(
    score: int,
    body_char_count: int,
    section_name: str,
    profile: SectionProfile,
) -> str:
    adjusted = score
    if profile.is_core(section_name) and body_char_count < 500:
        adjusted -= 20
    if body_char_count < 120:
        adjusted -= 15
    if adjusted >= 70:
        return "high"
    if adjusted >= 40:
        return "medium"
    return "low"


def _section_quality(
    sections: list[dict[str, Any]],
    profile: SectionProfile,
) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        by_name.setdefault(section["name"], []).append(section)
    missing_core = sorted(
        profile.core_sections - set(by_name),
        key=lambda name: profile.order_index(name),
    )
    low_confidence_core = sorted(
        (
            name for name in profile.core_sections
            if name in by_name and all(
                section.get("section_detection_confidence") == "low"
                for section in by_name[name]
            )
        ),
        key=lambda name: profile.order_index(name),
    )
    fail_reasons: list[str] = []
    warn_reasons: list[str] = []
    if not sections:
        fail_reasons.append("no_sections_detected")
    if len(missing_core) >= 2:
        fail_reasons.append("multiple_core_sections_missing")
    if sections and len(sections) < 3 and missing_core:
        fail_reasons.append("too_few_sections_for_core_coverage")
    if _has_large_core_gap(sections, set(missing_core), profile):
        fail_reasons.append("large_core_section_gap")
    if missing_core:
        warn_reasons.append("core_sections_missing")
    if low_confidence_core:
        warn_reasons.append("low_confidence_core_sections")
    status = "fail" if fail_reasons else "warn" if warn_reasons else "pass"
    return {
        "status": status,
        "document_type": profile.document_type,
        "missing_core_sections": missing_core,
        "low_confidence_core_sections": low_confidence_core,
        "fail_reasons": fail_reasons,
        "warn_reasons": warn_reasons,
        "section_count": len(sections),
    }


def _canonical_title_match(
    section_name: str,
    lowered_text: str,
    profile: SectionProfile,
) -> bool:
    return profile.canonical_title_match(section_name, lowered_text)


def _infer_section_name(
    text: str,
    profile: SectionProfile,
    current_part: str | None,
) -> str | None:
    return profile.infer_section_name(text, current_part=current_part)


def _title_boundary_match(
    section_name: str,
    text: str,
    profile: SectionProfile,
) -> bool:
    return profile.title_boundary_match(section_name, text)


def _has_large_core_gap(
    sections: list[dict[str, Any]],
    missing_core: set[str],
    profile: SectionProfile,
) -> bool:
    if not missing_core:
        return False
    for left, right in zip(sections, sections[1:]):
        left_order = _order_index(left["name"], profile)
        right_order = _order_index(right["name"], profile)
        if right_order <= left_order + 1:
            continue
        skipped = {
            name for name in profile.core_sections
            if left_order < _order_index(name, profile) < right_order
        }
        skipped_missing = skipped & missing_core
        if len(skipped_missing) >= 2:
            return True
    return False


def _is_cross_reference_candidate(text: str) -> bool:
    normalized = _normalize_text(text).replace("’", "'")
    if not _ITEM_LABEL_ANY_RE.match(normalized):
        return False
    cross_reference_markers = (
        "of this annual report",
        "of this quarterly report",
        "of this form 10-k",
        "of this form 10-q",
        "in this annual report",
        "in this quarterly report",
        "in this form 10-k",
        "in this form 10-q",
        "included in part",
        "refer to part",
        "see item",
    )
    return any(marker in normalized for marker in cross_reference_markers)


def _is_markdown_link_at_offset(markdown_text: str, offset: int, candidate_text: str) -> bool:
    if offset <= 0 or markdown_text[offset - 1] != "[":
        return False
    link_tail = markdown_text[offset: offset + len(candidate_text) + 64]
    return "](" in link_tail


def _find_markdown_offset(markdown_text: str, candidate_text: str) -> int | None:
    stripped = candidate_text.strip()
    if not stripped:
        return None
    direct = markdown_text.find(stripped)
    if direct >= 0:
        return direct
    direct_ci = markdown_text.lower().find(stripped.lower())
    if direct_ci >= 0:
        return direct_ci
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&.-]*", stripped)
    if not tokens:
        return None
    compact_tokens = tokens[:16]
    pattern = r"\s+".join(re.escape(token) for token in compact_tokens)
    match = re.search(pattern, markdown_text, re.IGNORECASE)
    return match.start() if match else None


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset))


def _candidate_text(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text.strip())
    text = text.replace("\xa0", " ").replace("’", "'")
    if "|" in text:
        cells = [
            re.sub(r"\s+", " ", cell).strip()
            for cell in text.split("|")
        ]
        cells = [
            cell for cell in cells
            if cell and set(cell) != {"-"} and cell != "---"
        ]
        text = " ".join(cells)
    text = re.sub(r"^#{1,6}\s*", "", text.strip()).strip()
    return re.sub(r"\s+", " ", text)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _hash_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()


def _order_index(section_name: str, profile: SectionProfile | None = None) -> int:
    if profile is not None:
        return profile.order_index(section_name)
    try:
        return _ITEM_ORDER.index(section_name)
    except ValueError:
        return 999
