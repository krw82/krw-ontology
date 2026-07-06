"""Parse fetched guru raw documents into private text spans."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Comment

from krw_ontology.guru.models import (
    GuruParsedDocument,
    GuruParsedManifest,
    GuruPrivateSpanRecord,
    GuruRawDocument,
    GuruRawManifest,
    utc_now_iso,
)
from krw_ontology.guru.workspace import _write_json_atomic, guru_root, guru_running_root


PARSED_MANIFEST_FILENAME = "parsed_manifest.json"
DEFAULT_MAX_SPAN_CHARS = 2400
_PDF_STOP_SECTION_RE = re.compile(
    r"\b("
    r"Principal\s+Risks\s+and\s+Uncertainties|"
    r"Report\s+of\s+the\s+Directors|"
    r"Condensed\s+Interim\s+Financial\s+Statements|"
    r"Interim\s+Financial\s+Statements|"
    r"Financial\s+Statements|"
    r"Independent\s+Auditor|"
    r"Statement\s+of\s+Financial\s+Position"
    r")\b",
    re.IGNORECASE,
)
_PDF_LETTER_START_RE = re.compile(
    r"\b("
    r"LETTER\s+TO\s+SHAREHOLDERS|"
    r"To\s+the\s+Shareholders\s+of|"
    r"Investment\s+Manager[’']?s\s+Report"
    r")\b",
    re.IGNORECASE,
)


class GuruParseError(RuntimeError):
    """Raised when a raw guru document cannot be converted to text."""


def load_raw_manifest(running_root: Path | str | None = None) -> GuruRawManifest:
    path = guru_running_root(running_root) / "raw_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GuruRawManifest.model_validate(payload)


def parse_guru_sources(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    source_ids: set[str] | None = None,
    overwrite: bool = False,
    max_span_chars: int = DEFAULT_MAX_SPAN_CHARS,
) -> GuruParsedManifest:
    """Parse fetched PDF/HTML/text documents into markdown and private spans."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    manifest = load_raw_manifest(running_path)
    parsed_documents: list[GuruParsedDocument] = []
    for raw_document in manifest.raw_documents:
        if source_ids and raw_document.source_id not in source_ids:
            continue
        parsed_documents.append(
            _parse_one(
                raw_document,
                running_path,
                overwrite=overwrite,
                max_span_chars=max_span_chars,
            )
        )
    parsed_manifest = GuruParsedManifest(
        generated_at=utc_now_iso(),
        root=str(root_path),
        running_root=str(running_path),
        parsed_documents=parsed_documents,
    )
    _write_json_atomic(
        running_path / PARSED_MANIFEST_FILENAME,
        parsed_manifest.model_dump(mode="json"),
    )
    return parsed_manifest


def build_span_records(
    document: GuruRawDocument,
    text: str,
    *,
    max_span_chars: int = DEFAULT_MAX_SPAN_CHARS,
) -> list[GuruPrivateSpanRecord]:
    """Build private paragraph spans from normalized markdown text."""
    records: list[GuruPrivateSpanRecord] = []
    position = 0
    for section_title, paragraph in _paragraphs_with_sections(text):
        for chunk in _chunk_text(paragraph, max_span_chars=max_span_chars):
            records.append(
                GuruPrivateSpanRecord(
                    span_id=f"{document.source_id}:span:{position:04d}",
                    source_id=document.source_id,
                    span_type="paragraph",
                    position=position,
                    section_title=section_title,
                    text_hash=_sha256_text(chunk),
                    author_key=document.author_key,
                    title=document.title,
                    official_url=document.official_url,
                    text=chunk,
                    char_count=len(chunk),
                )
            )
            position += 1
    return records


def parse_raw_document_text(
    path: Path,
    content_type: str | None,
    *,
    source_url: str | None = None,
    title: str | None = None,
) -> tuple[str, str]:
    """Return normalized text and parser name for a raw document."""
    suffix = path.suffix.lower()
    normalized_content_type = _clean_content_type(content_type)
    if normalized_content_type == "application/pdf" or suffix == ".pdf":
        text, parser_name = _extract_pdf_text(path, source_url=source_url, title=title)
    elif normalized_content_type in {"text/html", "application/xhtml+xml"} or suffix == ".html":
        text = _html_to_markdown(path.read_bytes())
        parser_name = "beautifulsoup-markdownify"
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        parser_name = "plain_text"
    normalized = _normalize_text(text)
    if not normalized:
        raise GuruParseError(f"no text extracted from {path}")
    return normalized, parser_name


def _parse_one(
    raw_document: GuruRawDocument,
    running_root: Path,
    *,
    overwrite: bool,
    max_span_chars: int,
) -> GuruParsedDocument:
    if raw_document.status != "fetched" or not raw_document.raw_path:
        return GuruParsedDocument(
            source_id=raw_document.source_id,
            author_key=raw_document.author_key,
            title=raw_document.title,
            source_type=raw_document.source_type,
            official_url=raw_document.official_url,
            raw_path=raw_document.raw_path,
            content_type=raw_document.content_type,
            status="skipped",
            error=raw_document.error or "raw_document_not_fetched",
        )
    raw_path = Path(raw_document.raw_path)
    parsed_path = _parsed_path_for(raw_document, running_root)
    spans_path = _spans_path_for(raw_document, running_root)
    if parsed_path.exists() and spans_path.exists() and not overwrite:
        text = parsed_path.read_text(encoding="utf-8", errors="replace")
        span_count = sum(1 for _ in spans_path.open(encoding="utf-8"))
        return GuruParsedDocument(
            source_id=raw_document.source_id,
            author_key=raw_document.author_key,
            title=raw_document.title,
            source_type=raw_document.source_type,
            official_url=raw_document.official_url,
            raw_path=str(raw_path),
            parsed_path=str(parsed_path),
            spans_path=str(spans_path),
            parser="cached",
            content_type=raw_document.content_type,
            text_hash=_sha256_text(text),
            char_count=len(text),
            span_count=span_count,
            parsed_at=utc_now_iso(),
            status="parsed",
        )
    try:
        text, parser_name = parse_raw_document_text(
            raw_path,
            raw_document.content_type,
            source_url=raw_document.official_url,
            title=raw_document.title,
        )
        spans = build_span_records(raw_document, text, max_span_chars=max_span_chars)
        parsed_path.parent.mkdir(parents=True, exist_ok=True)
        spans_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(parsed_path, text + "\n")
        _write_jsonl_atomic(spans_path, (span.model_dump(mode="json") for span in spans))
        return GuruParsedDocument(
            source_id=raw_document.source_id,
            author_key=raw_document.author_key,
            title=raw_document.title,
            source_type=raw_document.source_type,
            official_url=raw_document.official_url,
            raw_path=str(raw_path),
            parsed_path=str(parsed_path),
            spans_path=str(spans_path),
            parser=parser_name,
            content_type=raw_document.content_type,
            text_hash=_sha256_text(text),
            char_count=len(text),
            span_count=len(spans),
            parsed_at=utc_now_iso(),
            status="parsed",
        )
    except (OSError, GuruParseError, subprocess.SubprocessError) as exc:
        return GuruParsedDocument(
            source_id=raw_document.source_id,
            author_key=raw_document.author_key,
            title=raw_document.title,
            source_type=raw_document.source_type,
            official_url=raw_document.official_url,
            raw_path=str(raw_path),
            content_type=raw_document.content_type,
            parsed_at=utc_now_iso(),
            status="error",
            error=str(exc),
        )


def _html_to_markdown(content: bytes) -> str:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    body = soup.find("body") or soup
    try:
        from markdownify import markdownify as md

        return md(str(body), heading_style="ATX", bullets="-")
    except ImportError:
        return body.get_text(separator="\n")


def _extract_pdf_text(
    path: Path,
    *,
    source_url: str | None = None,
    title: str | None = None,
) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return _extract_pdf_text_with_pdftotext(path)

    try:
        reader = PdfReader(str(path))
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pragma: no cover - parser dependency errors are environment-specific.
        raise GuruParseError(f"pypdf failed for {path}: {exc}") from exc
    start_index = _pdf_page_start_index(source_url)
    stop_at_section = start_index is not None
    if start_index is None and title and "letter to shareholders" in title.lower():
        start_index = _find_pdf_letter_start_page(page_texts)
        stop_at_section = start_index is not None
    text_parts = _trim_pdf_page_texts(
        page_texts,
        start_index=start_index or 0,
        stop_at_section=stop_at_section,
    )
    return "\n\n".join(text_parts), "pypdf"


def _extract_pdf_text_with_pdftotext(path: Path) -> tuple[str, str]:
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise GuruParseError("PDF parsing requires pypdf or pdftotext") from exc
    return completed.stdout, "pdftotext"


def _pdf_page_start_index(source_url: str | None) -> int | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    candidates = []
    if parsed.fragment:
        fragment_params = parse_qs(parsed.fragment)
        candidates.extend(fragment_params.get("page", []))
        if parsed.fragment.lower().startswith("page="):
            candidates.append(parsed.fragment.split("=", 1)[1])
    query_params = parse_qs(parsed.query)
    candidates.extend(query_params.get("page", []))
    for candidate in candidates:
        try:
            page_number = int(str(candidate).strip())
        except ValueError:
            continue
        if page_number > 0:
            return page_number - 1
    return None


def _find_pdf_letter_start_page(page_texts: list[str]) -> int | None:
    for index, page_text in enumerate(page_texts[:40]):
        normalized = _normalize_text(page_text)
        if _PDF_LETTER_START_RE.search(normalized):
            return index
    return None


def _trim_pdf_page_texts(
    page_texts: list[str],
    *,
    start_index: int = 0,
    stop_at_section: bool = True,
) -> list[str]:
    bounded_start = max(0, min(start_index, len(page_texts)))
    selected: list[str] = []
    for index, page_text in enumerate(page_texts[bounded_start:]):
        normalized = _normalize_text(page_text)
        if stop_at_section and index > 0 and _PDF_STOP_SECTION_RE.search(normalized):
            break
        selected.append(page_text)
    return selected


def _paragraphs_with_sections(text: str) -> list[tuple[str | None, str]]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    section_title: str | None = None
    paragraphs: list[tuple[str | None, str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("#"):
            section_title = first.lstrip("#").strip() or section_title
            rest = "\n".join(lines[1:]).strip()
            if rest:
                paragraphs.append((section_title, rest))
            continue
        paragraphs.append((section_title, " ".join(lines)))
    return paragraphs


def _chunk_text(text: str, *, max_span_chars: int) -> list[str]:
    clean = text.strip()
    if len(clean) <= max_span_chars:
        return [clean]
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > max_span_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current.strip())
    return chunks or [clean[:max_span_chars]]


def _normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _parsed_path_for(document: GuruRawDocument, running_root: Path) -> Path:
    return running_root / "parsed" / document.author_key / f"{_safe_stem(document.source_id)}.md"


def _spans_path_for(document: GuruRawDocument, running_root: Path) -> Path:
    return running_root / "spans" / document.author_key / f"{_safe_stem(document.source_id)}.jsonl"


def _safe_stem(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-") or "source"


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _write_jsonl_atomic(path: Path, rows: Iterable[dict]) -> None:
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)
