from __future__ import annotations

from pathlib import Path

import httpx

from krw_ontology.guru.pipeline import run_guru_pipeline


def test_guru_pipeline_fetch_parse_extract_dry_run_end_to_end(tmp_path: Path):
    root = tmp_path / "guru"
    running_root = tmp_path / "guru-running"

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
                text="""
                <html><body>
                  <h1>2024 Shareholder Letter</h1>
                  <p>We prefer businesses that can deploy capital sensibly over long periods.</p>
                </body></html>
                """,
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = run_guru_pipeline(
            root,
            running_root=running_root,
            author_keys=["buffett"],
            limit_per_author=1,
            client=client,
        )

    assert payload["initialized"] is True
    assert payload["raw_documents"] == 2
    assert payload["parsed_documents"] >= 1
    assert payload["execution_mode"] == "dry_run"
    assert payload["agent_sdk_called"] is False
    assert payload["extraction_batches"] >= 1
    assert Path(payload["files"]["extraction_manifest"]).exists()
