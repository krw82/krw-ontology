"""Guru advisor skill name mapping.

The application owns guru selection. This module only maps an already selected
author key to the matching plugin skill.
"""

from __future__ import annotations


GURU_SKILL_MAP = {
    "buffett": "krw-guru-buffett-advisor",
    "marks": "krw-guru-marks-advisor",
    "ackman": "krw-guru-ackman-advisor",
    "flatt": "krw-guru-flatt-advisor",
    "terry_smith": "krw-guru-terry-smith-advisor",
}


def guru_skill_name(author_key: str) -> str:
    """Return the plugin skill name for an already selected guru author key."""
    try:
        return GURU_SKILL_MAP[author_key]
    except KeyError as exc:
        raise ValueError(f"Unknown guru author_key: {author_key}") from exc

