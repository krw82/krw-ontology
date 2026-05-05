"""Stage 7.4: Clean HTML to markdown via BeautifulSoup + markdownify."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from krw_ontology.errors import PipelineStageError
from krw_ontology.utils.io import atomic_write, compute_sha256

logger = logging.getLogger("krw_ontology")


def clean_to_markdown(raw_html_path: Path, output_path: Path) -> dict:
    """Convert raw SEC HTML to clean markdown.

    Returns dict with: clean_md_path, sha256.
    """
    try:
        html_content = raw_html_path.read_bytes()
        soup = BeautifulSoup(html_content, "lxml")

        # Remove unwanted elements
        for tag in soup.find_all(["script", "style", "nav", "footer"]):
            tag.decompose()
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        # Remove inline XBRL tags but keep text content
        for ix_tag in soup.find_all(re.compile(r"^ix:")):
            ix_tag.unwrap()

        # Convert tables to markdown (best-effort)
        _process_tables(soup)

        try:
            from markdownify import markdownify as md
            body = soup.find("body") or soup
            markdown_text = md(str(body), heading_style="ATX", bullets="-")
        except ImportError:
            body = soup.find("body") or soup
            markdown_text = body.get_text(separator="\n")

        # Post-process: normalize whitespace
        markdown_text = _normalize_markdown(markdown_text)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(output_path, markdown_text)

        sha256 = compute_sha256(markdown_text.encode())
        logger.info(
            "clean_to_markdown: wrote %d chars, sha256=%s",
            len(markdown_text), sha256[:16],
            extra={"stage": "clean_to_markdown"},
        )
        return {"clean_md_path": output_path, "sha256": sha256}

    except PipelineStageError:
        raise
    except Exception as e:
        raise PipelineStageError(f"clean_to_markdown: {e}") from e


def _process_tables(soup: BeautifulSoup) -> None:
    """Best-effort table cleanup: mark complex tables with HTML comment."""
    for table in soup.find_all("table"):
        has_colspan = any(td.get("colspan") or td.get("rowspan") for td in table.find_all(["td", "th"]))
        if has_colspan:
            table.insert_before(Comment(" complex_table_start "))
            table.insert_after(Comment(" complex_table_end "))


def _normalize_markdown(text: str) -> str:
    """Normalize markdown whitespace while preserving structure."""
    # Collapse multiple blank lines to at most two
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove trailing whitespace on lines
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)
