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


CORE_SECTIONS = {"item1", "item1a", "item7", "item7a", "item8"}

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
_ITEM_LABEL_RE = re.compile(r"(?i)^item\s+\d+[a-z]?\.")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def build_document_nodes(markdown_text: str, raw_html_text: str | None = None) -> list[dict[str, Any]]:
    """Build stable document nodes from clean markdown, enriched with HTML hints."""
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

    return nodes


def build_section_candidates(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find all section heading candidates before selecting boundaries."""
    candidates: list[dict[str, Any]] = []
    for node in nodes:
        text = _candidate_text(node["text"])
        if not text:
            continue
        for section_name, pattern in SECTION_PATTERNS.items():
            if re.match(pattern, text):
                candidate = _make_candidate(node, section_name, text)
                if not candidate["is_heading_like"] and not _ITEM_LABEL_RE.match(text):
                    continue
                if (
                    section_name == "item1"
                    and not _ITEM_LABEL_RE.match(text)
                    and _normalize_text(text) != "business"
                ):
                    continue
                candidates.append(candidate)
                break

    _mark_toc_density(candidates, nodes)
    for candidate in candidates:
        candidate["score"] = _score_candidate(candidate)
    return candidates


def select_section_boundaries(
    markdown_text: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Select section boundaries from explicit candidates and return audit rows."""
    if not candidates:
        return [], [], _section_quality([])

    ordered = sorted(candidates, key=lambda c: (c["start_char"], _order_index(c["section_name"])))
    prelim = [c for c in ordered if c["score"] >= 20 or len(ordered) <= 2]
    if not prelim:
        prelim = [max(ordered, key=lambda c: c["score"])]

    selected: list[dict[str, Any]] = []
    for candidate in prelim:
        if _is_probable_toc_only(candidate, ordered):
            continue
        if selected and candidate["start_char"] == selected[-1]["start_char"]:
            if candidate["score"] > selected[-1]["score"]:
                selected[-1] = candidate
            continue
        selected.append(candidate)

    if not selected:
        selected = [max(ordered, key=lambda c: c["score"])]

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
        confidence = _confidence(candidate["score"], body_char_count, section_name)

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

    return sections, audit, _section_quality(sections)


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


def _make_candidate(node: dict[str, Any], section_name: str, text: str) -> dict[str, Any]:
    lowered = text.lower()
    title_match = _canonical_title_match(section_name, lowered)
    starts_with_item_label = bool(_ITEM_LABEL_RE.match(text))
    is_heading_like = (
        node["node_type"] in {"heading", "table_row"}
        or bool(node.get("is_bold"))
        or starts_with_item_label
    )
    has_page_number = bool(_PAGE_NUMBER_RE.search(text) or (
        starts_with_item_label and re.search(r"\s+\d{1,3}\s*$", text)
    ))
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
        "is_heading_like": is_heading_like,
        "is_toc_like": is_toc_like,
        "in_table": bool(node.get("in_table")),
        "has_page_number": has_page_number,
        "canonical_title_match": title_match,
        "html_tag": node.get("html_tag"),
        "heading_level": node.get("heading_level"),
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
        prior_text = " ".join(
            nodes[i]["text"].lower()
            for i in range(max(0, candidate["node_index"] - 8), candidate["node_index"] + 1)
        )
        explicit_toc_context = "table of contents" in prior_text or "contents" == prior_text.strip()
        if candidate.get("has_page_number") and early and dense_item_list and len(candidate["candidate_text"]) <= 220 and (
            nearby_page_numbers or explicit_toc_context
        ):
            candidate["is_toc_like"] = True


def _score_candidate(candidate: dict[str, Any]) -> int:
    score = 0
    text = candidate["candidate_text"]
    if _ITEM_LABEL_RE.match(text):
        score += 35
    else:
        score += 15
    if candidate["is_heading_like"]:
        score += 20
    if candidate["canonical_title_match"]:
        score += 25
    if candidate.get("heading_level"):
        score += 10
    if candidate["section_name"] in CORE_SECTIONS:
        score += 5
    if candidate["is_toc_like"]:
        score -= 70
    if candidate["in_table"]:
        score -= 5 if candidate["canonical_title_match"] and _ITEM_LABEL_RE.match(text) else 35
    if not candidate["is_heading_like"]:
        score -= 25
    if len(text) > 500:
        score -= 20
    return score


def _is_probable_toc_only(candidate: dict[str, Any], all_candidates: list[dict[str, Any]]) -> bool:
    if not candidate["is_toc_like"]:
        return False
    same_section = [c for c in all_candidates if c["section_name"] == candidate["section_name"]]
    better_later = any(c["start_char"] > candidate["start_char"] and c["score"] > candidate["score"] for c in same_section)
    return better_later or candidate["score"] < 20


def _candidate_rejection_reason(candidate: dict[str, Any]) -> str:
    if candidate["is_toc_like"]:
        return "toc_like_candidate"
    if candidate["in_table"]:
        return "table_candidate"
    if candidate["score"] < 20:
        return "low_score"
    return "not_selected"


def _confidence(score: int, body_char_count: int, section_name: str) -> str:
    adjusted = score
    if section_name in CORE_SECTIONS and body_char_count < 500:
        adjusted -= 20
    if body_char_count < 120:
        adjusted -= 15
    if adjusted >= 70:
        return "high"
    if adjusted >= 40:
        return "medium"
    return "low"


def _section_quality(sections: list[dict[str, Any]]) -> dict[str, Any]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        by_name.setdefault(section["name"], []).append(section)
    missing_core = sorted(CORE_SECTIONS - set(by_name))
    low_confidence_core = sorted(
        name for name in CORE_SECTIONS
        if name in by_name and all(
            section.get("section_detection_confidence") == "low"
            for section in by_name[name]
        )
    )
    status = "pass"
    if missing_core or low_confidence_core:
        status = "warn"
    return {
        "status": status,
        "missing_core_sections": missing_core,
        "low_confidence_core_sections": low_confidence_core,
        "section_count": len(sections),
    }


def _canonical_title_match(section_name: str, lowered_text: str) -> bool:
    hints = _CANONICAL_TITLE_HINTS.get(section_name, ())
    if not hints:
        return False
    return any(hint in lowered_text for hint in hints)


def _candidate_text(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text.strip())
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


def _order_index(section_name: str) -> int:
    try:
        return _ITEM_ORDER.index(section_name)
    except ValueError:
        return 999
