"""Stage 7.3: Download source document with retry and SHA-256."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from krw_ontology.config.settings import PipelineConfig
from krw_ontology.errors import PipelineStageError
from krw_ontology.utils.io import compute_sha256

logger = logging.getLogger("krw_ontology")


def download_source(
    source_url: str,
    output_path: Path,
    config: PipelineConfig,
) -> dict:
    """Download the source HTML filing with retry.

    Returns dict with: raw_html_path, sha256, bytes.
    """
    headers = {"User-Agent": config.sec_user_agent}
    max_retries = config.max_retries
    delay = config.retry_base_delay_seconds

    last_error = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(source_url, headers=headers, timeout=120.0, follow_redirects=True)
            resp.raise_for_status()
            content = resp.content

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)

            sha256 = compute_sha256(content)
            logger.info(
                "download_source: downloaded %d bytes, sha256=%s",
                len(content), sha256[:16],
                extra={"stage": "download_source_document"},
            )
            return {
                "raw_html_path": output_path,
                "sha256": sha256,
                "bytes": len(content),
            }
        except (httpx.HTTPError, OSError) as e:
            last_error = e
            logger.warning(
                "download_source: attempt %d/%d failed: %s",
                attempt + 1, max_retries, e,
                extra={"stage": "download_source_document"},
            )
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2

    raise PipelineStageError(
        f"download_source: failed after {max_retries} attempts: {last_error}"
    ) from last_error
