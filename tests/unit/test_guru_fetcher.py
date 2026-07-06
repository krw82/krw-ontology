from __future__ import annotations

import json
from pathlib import Path

import httpx
import brotli

from krw_ontology.guru.fetcher import (
    discover_source_documents_from_html,
    fetch_guru_sources,
)
from krw_ontology.guru.planner import build_planned_source_documents
from krw_ontology.guru.workspace import initialize_guru_workspace


def test_discover_source_documents_from_official_index_html():
    index_document = build_planned_source_documents(["buffett"])[0]
    html = """
    <html><body>
      <a href="/letters/2024-letter.html">2024 Shareholder Letter</a>
      <a href="/logo.png">logo</a>
      <a href="mailto:test@example.com">email</a>
    </body></html>
    """

    discovered = discover_source_documents_from_html(index_document, html)

    assert len(discovered) == 1
    assert discovered[0].author_key == "buffett"
    assert discovered[0].source_id.startswith("buffett:2024")
    assert discovered[0].official_url == "https://www.berkshirehathaway.com/letters/2024-letter.html"


def test_discover_source_documents_from_oaktree_data_items():
    index_document = build_planned_source_documents(["marks"])[0]
    html = """
    <html><body>
      <div data-items='[
        {
          "Title": "AI Hurtles Ahead",
          "CategoryText": "memos",
          "CssClassType": "article",
          "ReadMoreText": "Read",
          "IsoDate": "2026-02-26T07:00:00Z",
          "MoreLink": "/insights/memo/ai-hurtles-ahead"
        },
        {
          "Title": "AI Hurtles Ahead (Audio)",
          "CategoryText": "memos",
          "CssClassType": "audio",
          "ReadMoreText": "Listen",
          "IsoDate": "2026-02-26T07:00:00Z",
          "MoreLink": "/insights/memo-podcast/ai-hurtles-ahead"
        }
      ]'></div>
    </body></html>
    """

    discovered = discover_source_documents_from_html(index_document, html)

    assert [item.source_id for item in discovered] == ["marks:ai-hurtles-ahead"]
    assert discovered[0].official_url == "https://www.oaktreecapital.com/insights/memo/ai-hurtles-ahead"


def test_discover_source_documents_prefers_shareholder_letters_over_fact_sheets():
    index_document = build_planned_source_documents(["ackman"])[0]
    html = """
    <html><body><ul>
      <li>
        <span>June 12, 2026</span>
        <span>May 2026 Fact Sheet</span>
        <span>Fact Sheets</span>
        <a href="https://pershingsquareholdings.com/fact-sheet.pdf">PDF</a>
      </li>
      <li>
        <span>February 18, 2026</span>
        <span>Letter to Shareholders in the 2025 Annual Report</span>
        <span>Letters &amp; Presentations</span>
        <a href="https://assets.pershingsquareholdings.com/2025-annual-report.pdf#page=9">
          PDF
        </a>
      </li>
    </ul></body></html>
    """

    discovered = discover_source_documents_from_html(index_document, html)

    assert len(discovered) == 1
    assert "Letter to Shareholders" in discovered[0].title
    assert discovered[0].official_url.endswith("2025-annual-report.pdf#page=9")


def test_fetch_guru_sources_fetches_index_and_discovered_document(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    initialize_guru_workspace(root, running_root=running_root, author_keys=["buffett"])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.berkshirehathaway.com/letters/letters.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/letters/2024-letter.html">2024 Shareholder Letter</a>',
            )
        if url == "https://www.berkshirehathaway.com/letters/2024-letter.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body><p>We think about capital allocation.</p></body></html>",
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = fetch_guru_sources(
            root,
            running_root=running_root,
            limit_per_author=1,
            client=client,
        )

    assert [item.status for item in manifest.raw_documents] == ["fetched", "fetched"]
    assert (running_root / "raw_manifest.json").exists()
    assert (running_root / "discovery_manifest.json").exists()
    discovery_payload = json.loads((running_root / "discovery_manifest.json").read_text())
    assert len(discovery_payload["discovered_sources"]) == 1
    assert all(Path(item.raw_path or "").exists() for item in manifest.raw_documents)


def test_fetch_guru_sources_decodes_brotli_index_html(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    initialize_guru_workspace(root, running_root=running_root, author_keys=["buffett"])
    html = b'<a href="/letters/2004ltr.pdf">2004</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.berkshirehathaway.com/letters/letters.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html", "content-encoding": "br"},
                content=brotli.compress(html),
            )
        if url == "https://www.berkshirehathaway.com/letters/2004ltr.pdf":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 test",
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = fetch_guru_sources(
            root,
            running_root=running_root,
            limit_per_author=1,
            client=client,
        )

    assert [item.source_id for item in manifest.raw_documents] == [
        "buffett:official_index",
        "buffett:2004",
    ]
    index_path = Path(manifest.raw_documents[0].raw_path or "")
    assert index_path.read_bytes() == html


def test_fetch_guru_sources_uses_direct_seed_when_index_is_blocked(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"
    initialize_guru_workspace(root, running_root=running_root, author_keys=["terry_smith"])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.fundsmith.co.uk/documents/":
            return httpx.Response(403, headers={"content-type": "text/html"}, text="blocked")
        if url == "https://www.fundsmith.co.uk/media/4hcfd1pg/2025-fef-annual-letter-web.pdf":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.4 test",
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        manifest = fetch_guru_sources(
            root,
            running_root=running_root,
            limit_per_author=1,
            client=client,
        )

    assert [item.source_id for item in manifest.raw_documents] == [
        "terry_smith:official_index",
        "terry_smith:2025-annual-letter",
    ]
    assert [item.status for item in manifest.raw_documents] == ["error", "fetched"]
