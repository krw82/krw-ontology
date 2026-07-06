"""Official-source discovery and raw download for guru letters."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import html as html_lib
import json
import re
from pathlib import Path
import zlib
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
import httpx
import yaml

from krw_ontology.guru.models import (
    GuruDiscoveredSourceManifest,
    GuruRawDocument,
    GuruRawManifest,
    GuruSourceDocument,
    GuruSourceManifest,
    utc_now_iso,
)
from krw_ontology.guru.workspace import (
    _write_json_atomic,
    guru_root,
    guru_running_root,
)


GURU_USER_AGENT = "krw-ontology-guru/0.1 official-source-research"
DISCOVERY_FILENAME = "discovery_manifest.json"
RAW_MANIFEST_FILENAME = "raw_manifest.json"

_LETTER_LINK_RE = re.compile(
    r"\b(letter|letters|shareholder|memo|owner'?s manual|chairman)\b",
    re.IGNORECASE,
)
_STRONG_DOCUMENT_RE = re.compile(
    r"\b("
    r"letter\s+to\s+shareholders?|shareholder\s+letter|"
    r"(?:semi\s+annual|annual)\s+letter|memo"
    r")\b",
    re.IGNORECASE,
)
_NEGATIVE_DOCUMENT_RE = re.compile(
    r"\b("
    r"fact\s*sheet|notice|articles?\s+of\s+incorporation|proxy|circular|"
    r"meeting|presentation|slides|transcript|press\s+release|modern\s+slavery|"
    r"podcast|audio|video|webcast"
    r")\b",
    re.IGNORECASE,
)
_GENERIC_LINK_TEXT = {
    "pdf",
    "link",
    "read",
    "read more",
    "view",
    "download",
    "listen",
    "watch",
}
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_TWO_DIGIT_QUARTER_YEAR_RE = re.compile(
    r"\b(?:q[1-4][\s_-]?(\d{2})|(\d{2})[\s_-]?q[1-4])\b",
    re.IGNORECASE,
)
_BLOCKED_SCHEMES = {"mailto", "javascript", "tel", "data"}
_BLOCKED_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".zip",
    ".xls",
    ".xlsx",
    ".csv",
}


def load_source_manifest(root: Path | str | None = None) -> GuruSourceManifest:
    """Load the seed source manifest written by ``guru start``."""
    path = guru_root(root) / "source_manifest.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GuruSourceManifest.model_validate(payload)


def discover_source_documents_from_html(
    index_document: GuruSourceDocument,
    html: str | bytes,
    *,
    base_url: str | None = None,
) -> list[GuruSourceDocument]:
    """Discover likely letter/memo PDF or HTML documents from an official index page."""
    soup = BeautifulSoup(html, "lxml")
    discovered_with_order: list[tuple[int, GuruSourceDocument]] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    order = 0

    def add_document(title: str, absolute_url: str, *, context: str = "") -> None:
        nonlocal order
        if not _is_allowed_document_url(absolute_url):
            return
        if not _looks_like_letter_link(absolute_url, title, context=context):
            return
        url_key = urldefrag(absolute_url).url
        if url_key in seen_urls:
            return
        seen_urls.add(url_key)
        source_title = _document_title(title, absolute_url, context)
        source_id = _unique_source_id(
            _source_id_for_link(index_document.author_key, source_title, absolute_url),
            seen_ids,
        )
        seen_ids.add(source_id)
        year = _extract_year(f"{source_title} {context} {absolute_url}")
        discovered_with_order.append(
            (
                order,
                GuruSourceDocument(
                    source_id=source_id,
                    author_key=index_document.author_key,
                    title=source_title,
                    source_type=index_document.source_type,
                    official_url=absolute_url,
                    publication_date=year,
                    rights_policy=index_document.rights_policy,
                    collection_status="planned",
                ),
            )
        )
        order += 1

    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        absolute_url = urljoin(base_url or index_document.official_url, href)
        title = _normalize_title(link.get_text(" ", strip=True)) or _title_from_url(absolute_url)
        add_document(
            title,
            absolute_url,
            context=_link_context_text(link),
        )

    for item in _data_item_documents(soup):
        add_document(
            item["title"],
            urljoin(base_url or index_document.official_url, item["url"]),
            context=item["context"],
        )

    discovered_with_order.sort(
        key=lambda item: _discovery_sort_key(item[1], item[0]),
        reverse=True,
    )
    return [document for _, document in discovered_with_order]


def fetch_guru_sources(
    root: Path | str | None = None,
    *,
    running_root: Path | str | None = None,
    limit_per_author: int | None = None,
    overwrite: bool = False,
    discover: bool = True,
    include_index_documents: bool = False,
    client: httpx.Client | None = None,
) -> GuruRawManifest:
    """Fetch official indexes, discover source documents, then fetch discovered raw files."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    (running_path / "raw").mkdir(parents=True, exist_ok=True)
    source_manifest = load_source_manifest(root_path)

    own_client = client is None
    http_client = client or httpx.Client(follow_redirects=True, timeout=120.0)
    raw_documents: list[GuruRawDocument] = []
    discovered_sources: list[GuruSourceDocument] = []
    fetched_urls: set[str] = set()
    try:
        for index_document in source_manifest.planned_sources:
            raw = _fetch_one(index_document, running_path, http_client, overwrite=overwrite)
            raw_documents.append(raw)
            fetched_urls.add(index_document.official_url)
            if discover and raw.status == "fetched" and raw.raw_path and _is_html_raw(raw):
                html = Path(raw.raw_path).read_bytes()
                discovered_sources.extend(
                    discover_source_documents_from_html(index_document, html, base_url=raw.official_url)
                )

        candidate_sources = [*source_manifest.seed_sources, *discovered_sources]
        selected_sources = _limit_per_author(candidate_sources, limit_per_author)
        for document in selected_sources:
            if document.official_url in fetched_urls:
                continue
            raw_documents.append(
                _fetch_one(document, running_path, http_client, overwrite=overwrite)
            )
            fetched_urls.add(document.official_url)

        if include_index_documents and not selected_sources:
            selected_sources = list(source_manifest.planned_sources)
    finally:
        if own_client:
            http_client.close()

    discovery_manifest = GuruDiscoveredSourceManifest(
        generated_at=utc_now_iso(),
        root=str(root_path),
        running_root=str(running_path),
        index_source_ids=[source.source_id for source in source_manifest.planned_sources],
        discovered_sources=discovered_sources,
    )
    raw_manifest = GuruRawManifest(
        generated_at=utc_now_iso(),
        root=str(root_path),
        running_root=str(running_path),
        source_count=len(raw_documents),
        raw_documents=raw_documents,
    )
    _write_json_atomic(
        running_path / DISCOVERY_FILENAME,
        discovery_manifest.model_dump(mode="json"),
    )
    _write_json_atomic(
        running_path / RAW_MANIFEST_FILENAME,
        raw_manifest.model_dump(mode="json"),
    )
    return raw_manifest


def _fetch_one(
    document: GuruSourceDocument,
    running_root: Path,
    client: httpx.Client,
    *,
    overwrite: bool,
) -> GuruRawDocument:
    existing_path = _existing_raw_path(document, running_root)
    if existing_path is not None and not overwrite:
        content = existing_path.read_bytes()
        return GuruRawDocument(
            source_id=document.source_id,
            author_key=document.author_key,
            title=document.title,
            source_type=document.source_type,
            official_url=document.official_url,
            content_type=_content_type_from_path(existing_path),
            raw_path=str(existing_path),
            sha256=_sha256(content),
            byte_count=len(content),
            fetched_at=utc_now_iso(),
            status="fetched",
        )

    try:
        response = client.get(
            document.official_url,
            headers={"User-Agent": GURU_USER_AGENT},
            follow_redirects=True,
        )
        response.raise_for_status()
        content_type = _clean_content_type(response.headers.get("content-type"))
        content = _decoded_response_content(response)
        raw_path = _raw_path_for_document(document, running_root, content_type=content_type)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(content)
        return GuruRawDocument(
            source_id=document.source_id,
            author_key=document.author_key,
            title=document.title,
            source_type=document.source_type,
            official_url=document.official_url,
            content_type=content_type,
            raw_path=str(raw_path),
            sha256=_sha256(content),
            byte_count=len(content),
            fetched_at=utc_now_iso(),
            http_status_code=response.status_code,
            status="fetched",
        )
    except (httpx.HTTPError, OSError) as exc:
        return GuruRawDocument(
            source_id=document.source_id,
            author_key=document.author_key,
            title=document.title,
            source_type=document.source_type,
            official_url=document.official_url,
            status="error",
            fetched_at=utc_now_iso(),
            error=str(exc),
        )


def _limit_per_author(
    documents: Iterable[GuruSourceDocument],
    limit_per_author: int | None,
) -> list[GuruSourceDocument]:
    if limit_per_author is None:
        return list(documents)
    counts: dict[str, int] = defaultdict(int)
    selected: list[GuruSourceDocument] = []
    for document in documents:
        if counts[document.author_key] >= limit_per_author:
            continue
        selected.append(document)
        counts[document.author_key] += 1
    return selected


def _data_item_documents(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract JS-backed insight cards from official index pages."""
    documents: list[dict[str, str]] = []
    for element in soup.select("[data-items]"):
        raw_items = str(element.get("data-items") or "")
        if not raw_items:
            continue
        try:
            items = json.loads(raw_items)
        except json.JSONDecodeError:
            try:
                items = json.loads(html_lib.unescape(raw_items))
            except json.JSONDecodeError:
                continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = _normalize_title(str(item.get("Title") or ""))
            url = _normalize_title(str(item.get("MoreLink") or ""))
            category = _normalize_title(str(item.get("CategoryText") or ""))
            css_class = _normalize_title(str(item.get("CssClassType") or ""))
            read_more = _normalize_title(str(item.get("ReadMoreText") or ""))
            date = _normalize_title(
                str(item.get("IsoDate") or item.get("FormattedDate") or "")
            )
            if not title or not url:
                continue
            context = _normalize_title(
                f"{title} {category} {css_class} {read_more} {date}"
            )
            if _NEGATIVE_DOCUMENT_RE.search(context) and "/insights/memo/" not in url:
                continue
            documents.append({"title": title, "url": url, "context": context})
    return documents


def _is_allowed_document_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() in _BLOCKED_SCHEMES:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    suffix = Path(parsed.path.lower()).suffix
    return suffix not in _BLOCKED_SUFFIXES


def _looks_like_letter_link(url: str, title: str, *, context: str = "") -> bool:
    parsed = urlparse(url)
    suffix = Path(parsed.path.lower()).suffix
    haystack = f"{title} {context} {parsed.path}".replace("_", " ").replace("-", " ")
    if _NEGATIVE_DOCUMENT_RE.search(haystack) and not _STRONG_DOCUMENT_RE.search(haystack):
        return False
    if suffix in {".html", ".htm", ""}:
        return bool(_LETTER_LINK_RE.search(haystack))
    if suffix == ".pdf":
        return bool(_LETTER_LINK_RE.search(haystack))
    return False


def _link_context_text(link: object) -> str:
    get_text = getattr(link, "get_text", None)
    if get_text is None:
        return ""
    find_parent = getattr(link, "find_parent", None)
    parent = (
        find_parent(["li", "tr", "article", "section", "div"])
        if find_parent is not None
        else None
    )
    if parent is None:
        return _normalize_title(get_text(" ", strip=True))
    return _normalize_title(parent.get_text(" ", strip=True))


def _document_title(title: str, url: str, context: str) -> str:
    normalized = _normalize_title(title)
    if normalized.lower() in _GENERIC_LINK_TEXT and context:
        cleaned = re.sub(
            r"\b(PDF|Link|Read More|Read|View|Download|Listen|Watch)\b\s*$",
            "",
            context,
            flags=re.IGNORECASE,
        )
        cleaned = _normalize_title(cleaned)
        if cleaned:
            return cleaned[:180]
    return normalized or _title_from_url(url)


def _discovery_sort_key(document: GuruSourceDocument, order: int) -> tuple[int, int, int]:
    year = int(document.publication_date) if document.publication_date else 0
    return (_document_priority(document), year, -order)


def _document_priority(document: GuruSourceDocument) -> int:
    haystack = f"{document.title} {document.official_url}".replace("_", " ").replace("-", " ")
    score = 0
    if "/insights/memo/" in document.official_url or _STRONG_DOCUMENT_RE.search(haystack):
        score = 100
    elif _LETTER_LINK_RE.search(haystack):
        score = 70
    if Path(urlparse(document.official_url).path.lower()).suffix == ".pdf":
        score += 10
    if _NEGATIVE_DOCUMENT_RE.search(haystack) and not _STRONG_DOCUMENT_RE.search(haystack):
        score -= 200
    return score


def _source_id_for_link(author_key: str, title: str, url: str) -> str:
    year = _extract_year(f"{title} {url}")
    slug_source = title or _title_from_url(url)
    slug = _slugify(slug_source)
    if year and not slug.startswith(year):
        slug = f"{year}-{slug}"
    return f"{author_key}:{slug[:96] or 'source'}"


def _unique_source_id(source_id: str, seen: set[str]) -> str:
    if source_id not in seen:
        return source_id
    index = 2
    while f"{source_id}-{index}" in seen:
        index += 1
    return f"{source_id}-{index}"


def _extract_year(value: str) -> str | None:
    match = _YEAR_RE.search(value)
    if match:
        return match.group(1)
    two_digit_match = _TWO_DIGIT_QUARTER_YEAR_RE.search(value)
    if not two_digit_match:
        return None
    raw_year = two_digit_match.group(1) or two_digit_match.group(2)
    year = int(raw_year)
    return str(1900 + year if year >= 80 else 2000 + year)


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    stem = Path(parsed.path).stem or "source"
    return _normalize_title(stem.replace("_", " ").replace("-", " "))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "source"


def _raw_path_for_document(
    document: GuruSourceDocument,
    running_root: Path,
    *,
    content_type: str | None,
) -> Path:
    extension = _extension_for(document.official_url, content_type)
    return running_root / "raw" / document.author_key / f"{_slugify(document.source_id)}{extension}"


def _existing_raw_path(document: GuruSourceDocument, running_root: Path) -> Path | None:
    directory = running_root / "raw" / document.author_key
    stem = _slugify(document.source_id)
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"{stem}.*"))
    return matches[0] if matches else None


def _extension_for(url: str, content_type: str | None) -> str:
    normalized = _clean_content_type(content_type)
    if normalized == "application/pdf":
        return ".pdf"
    if normalized in {"text/html", "application/xhtml+xml"}:
        return ".html"
    if normalized and normalized.startswith("text/"):
        return ".txt"
    suffix = Path(urlparse(url).path.lower()).suffix
    if suffix in {".pdf", ".html", ".htm", ".txt"}:
        return ".html" if suffix == ".htm" else suffix
    return ".bin"


def _content_type_from_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".html":
        return "text/html"
    if suffix == ".txt":
        return "text/plain"
    return None


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def _is_html_raw(raw: GuruRawDocument) -> bool:
    content_type = _clean_content_type(raw.content_type)
    path = Path(raw.raw_path or "")
    return content_type in {"text/html", "application/xhtml+xml"} or path.suffix.lower() == ".html"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _decoded_response_content(response: httpx.Response) -> bytes:
    content = response.content
    encodings = [
        encoding.strip().lower()
        for encoding in str(response.headers.get("content-encoding") or "").split(",")
        if encoding.strip()
    ]
    for encoding in reversed(encodings):
        if encoding in {"identity", "none"}:
            continue
        if _looks_decoded(content):
            continue
        if encoding == "br":
            import brotli

            content = brotli.decompress(content)
        elif encoding == "gzip":
            content = zlib.decompress(content, zlib.MAX_WBITS | 16)
        elif encoding == "deflate":
            content = zlib.decompress(content)
    return content


def _looks_decoded(content: bytes) -> bool:
    prefix = content[:64].lstrip().lower()
    return (
        prefix.startswith(b"<")
        or prefix.startswith(b"<!doctype")
        or prefix.startswith(b"%pdf")
        or prefix.startswith(b"{")
        or prefix.startswith(b"[")
    )
