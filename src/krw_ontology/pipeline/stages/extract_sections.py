"""Stage 7.5: Extract sections from clean markdown using regex patterns."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from krw_ontology.config.constants import SECTION_PATTERNS
from krw_ontology.errors import PipelineStageError

logger = logging.getLogger("krw_ontology")

V1_TARGET_SECTIONS = {"item1", "item1a", "item1c", "item7", "item7a", "item8"}


def extract_sections(clean_md_path: Path) -> dict:
    """Detect and extract 10-K sections from clean markdown.

    Returns dict with: sections (list of section dicts).
    """
    try:
        text = clean_md_path.read_text()
    except OSError as e:
        raise PipelineStageError(f"extract_sections: cannot read {clean_md_path}: {e}") from e

    lines = text.split("\n")

    # First pass: find all section heading candidates
    matches = []
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        heading_text = re.sub(r'^#{1,6}\s*', '', stripped)
        if not heading_text:
            continue
        for section_name, pattern in SECTION_PATTERNS.items():
            if re.match(pattern, heading_text):
                matches.append((line_idx, section_name, heading_text))
                break

    if not matches:
        logger.warning("extract_sections: no sections detected", extra={"stage": "extract_sections"})
        return {"sections": []}

    # TOC heuristic: if a section name appears multiple times with short content between,
    # the first occurrence is likely TOC. Prefer later body headings.
    filtered = _filter_toc_matches(matches, lines)

    # Second pass: build section dicts
    sections = []
    full_text = text
    section_counts: dict[str, int] = {}
    for i, (line_idx, section_name, heading) in enumerate(filtered):
        section_instance = section_counts.get(section_name, 0)
        section_counts[section_name] = section_instance + 1
        section_key = f"{section_name}_{section_instance:02d}"
        start_char = _line_offset(lines, line_idx)
        if i + 1 < len(filtered):
            next_line_idx = filtered[i + 1][0]
            end_char = _line_offset(lines, next_line_idx)
        else:
            end_char = len(full_text)

        section_text = full_text[start_char:end_char]
        confidence = "high" if section_name in V1_TARGET_SECTIONS else "medium"

        sections.append({
            "name": section_name,
            "number": section_name,
            "section_key": section_key,
            "section_instance": section_instance,
            "start_line": line_idx,
            "end_line": filtered[i + 1][0] - 1 if i + 1 < len(filtered) else len(lines) - 1,
            "text": section_text,
            "section_detection_confidence": confidence,
            "section_detection_method": "regex",
        })

    found_names = [s["name"] for s in sections]
    logger.info(
        "extract_sections: found %d sections: %s",
        len(sections), found_names,
        extra={"stage": "extract_sections"},
    )
    return {"sections": sections}


def _filter_toc_matches(
    matches: list[tuple[int, str, str]],
    lines: list[str],
) -> list[tuple[int, str, str]]:
    """Remove likely TOC entries, keeping body headings with substantial content."""
    if not matches:
        return matches

    # Group by section name
    by_name: dict[str, list[tuple[int, str, str]]] = {}
    for m in matches:
        by_name.setdefault(m[1], []).append(m)

    filtered = []
    for m in matches:
        group = by_name[m[1]]
        if len(group) == 1:
            filtered.append(m)
            continue

        # Multiple occurrences: keep the one with the most following content before next section
        idx_in_group = group.index(m)
        next_line = group[idx_in_group + 1][0] if idx_in_group + 1 < len(group) else len(lines)
        content_chars = sum(len(lines[j]) for j in range(m[0] + 1, min(next_line, len(lines))))

        if content_chars > 200:
            filtered.append(m)

    if not filtered:
        # Fallback: take last occurrence of each section
        seen = set()
        result = []
        for m in reversed(matches):
            if m[1] not in seen:
                seen.add(m[1])
                result.append(m)
        result.reverse()
        return result

    return filtered


def _line_offset(lines: list[str], target_line: int) -> int:
    """Compute character offset of a line index in the full text."""
    offset = 0
    for i in range(target_line):
        offset += len(lines[i]) + 1  # +1 for newline
    return offset
