"""Shard schema v3: SupportLink is a derived table, not a first-class object.

Serving joins quotes through claims' embedded ``supported_by_quotes`` fields;
SupportLink rows stay auditable as JSONL artifacts and as a derived
``support_links`` shard table, but they no longer inflate the ``objects``
table (51% of indexed rows) or the spine locator.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from krw_ontology.agent_index.router import open_ontology_store
from krw_ontology.agent_index.spine_builder import (
    COMPANY_SHARD_SCHEMA_VERSION,
    SPINE_PROJECTION_VERSION,
    build_spine_shard_release_outputs,
)
from krw_ontology.agent_index.store import OntologyStore
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

TICKER = "SO"
DOCUMENT_TYPE = "10-K"
DOC_TYPE_KEY = "10K"
PERIOD = "CY2023"

SOURCE_DOCUMENT_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"
SPAN_ID = f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:0001"
QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0001"
CLAIM_ID = f"claim:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:revenue-growth"
SUPPORT_LINK_ID = "support_link:SO:CY2023:10K:direct_quote_support:36ff437050"

TEXT = "Server operating margin expanded because data center demand increased."


def _write_minimal_artifacts(root: Path) -> None:
    ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    span = {
        "id": SPAN_ID,
        "type": "SourceSpan",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "section_name": "item7",
        "section_key": "item7",
        "span_index": 1,
        "text": TEXT,
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    quote = {
        "id": QUOTE_ID,
        "type": "EvidenceQuote",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "source_span_id": SPAN_ID,
        "quote_text": TEXT,
        "quote_type": "business_update",
        "section_name": "item7",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    claim = {
        "id": CLAIM_ID,
        "type": "ResearchClaim",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "claim_text": "Server operating margin expanded on data center demand.",
        "claim_type": "business_update",
        "supported_by_quotes": [QUOTE_ID],
        "related_metrics": ["operating_margin"],
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }
    support_link = {
        "id": SUPPORT_LINK_ID,
        "type": "SupportLink",
        "ticker": TICKER,
        "source_document_id": SOURCE_DOCUMENT_ID,
        "document_type": DOCUMENT_TYPE,
        "period": PERIOD,
        "from_id": QUOTE_ID,
        "to_id": CLAIM_ID,
        "support_object_id": QUOTE_ID,
        "support_object_type": "EvidenceQuote",
        "target_object_id": CLAIM_ID,
        "target_object_type": "ResearchClaim",
        "support_type": "direct_quote_support",
        "support_role": "quote_support",
        "stance": "supports",
        "support_strength": "direct",
        "inference_level": "direct_quote",
        "evidence_grade": "direct",
        "evidence_strength": "direct",
        "requires_inference": False,
        "created_by": "deterministic_projection",
        "confidence": "high",
        "review_status": "accepted",
        "schema_version": "0.1.0",
    }

    write_jsonl(ontology_dir / "spans.jsonl", [span])
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", [quote])
    write_jsonl(ontology_dir / "claims.jsonl", [claim])
    write_jsonl(ontology_dir / "support_links.jsonl", [support_link])
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=TICKER,
        period=PERIOD,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


def build_minimal_release(tmp_path: Path) -> tuple[Path, Path]:
    """Build v3 release outputs; return (company_shard_path, global_spine_path)."""
    _write_minimal_artifacts(tmp_path)
    result = build_spine_shard_release_outputs(
        tmp_path,
        release_id="test-shard-schema-v3",
        workers=1,
        no_cache=True,
    )
    shard_path = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard_path.is_file()
    return shard_path, result.global_spine_path


def build_minimal_shard_with_one_claim_and_quote(tmp_path: Path) -> Path:
    """Build one v3 company shard holding claim+quote+support_link artifacts."""
    return build_minimal_release(tmp_path)[0]


def open_store(shard: Path) -> OntologyStore:
    return OntologyStore(shard)


def test_support_links_live_in_derived_table_not_objects(tmp_path):
    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with sqlite3.connect(f"file:{shard}?mode=ro", uri=True) as conn:
        object_rows = conn.execute(
            "SELECT COUNT(*) FROM objects WHERE type='SupportLink'"
        ).fetchone()[0]
        link_rows = conn.execute("SELECT COUNT(*) FROM support_links").fetchone()[0]
    assert object_rows == 0
    assert link_rows >= 1
    # trace still resolves a support link id
    store = open_store(shard)
    traced = store.trace(SUPPORT_LINK_ID)
    assert traced is not None


def test_support_links_table_columns_match_v3_ddl(tmp_path):
    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with sqlite3.connect(f"file:{shard}?mode=ro", uri=True) as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(support_links)").fetchall()
        }
        row = conn.execute(
            """
            SELECT object_id, ticker, from_id, to_id, support_type, support_role,
                   stance, support_strength, inference_level, requires_inference,
                   evidence_grade, evidence_strength, json
            FROM support_links
            WHERE object_id = ?
            """,
            (SUPPORT_LINK_ID,),
        ).fetchone()
    assert columns == {
        "object_id",
        "ticker",
        "from_id",
        "to_id",
        "support_type",
        "support_role",
        "stance",
        "support_strength",
        "inference_level",
        "requires_inference",
        "evidence_grade",
        "evidence_strength",
        "json",
    }
    assert row is not None
    (
        object_id,
        ticker,
        from_id,
        to_id,
        support_type,
        support_role,
        stance,
        support_strength,
        inference_level,
        requires_inference,
        evidence_grade,
        evidence_strength,
        payload_json,
    ) = row
    assert object_id == SUPPORT_LINK_ID
    assert ticker == TICKER
    assert from_id == QUOTE_ID
    assert to_id == CLAIM_ID
    assert support_type == "direct_quote_support"
    assert support_role == "quote_support"
    assert stance == "supports"
    assert support_strength == "direct"
    assert inference_level == "direct_quote"
    assert requires_inference == 0
    assert evidence_grade == "direct"
    assert evidence_strength == "direct"
    assert CLAIM_ID in str(payload_json)


def test_support_link_trace_output_remains_equivalent(tmp_path):
    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with open_store(shard) as store:
        traced = store.trace(SUPPORT_LINK_ID)
    assert traced is not None
    support_link = traced["object"]
    assert support_link["id"] == SUPPORT_LINK_ID
    assert support_link["type"] == "SupportLink"
    assert support_link["from_id"] == QUOTE_ID
    assert support_link["to_id"] == CLAIM_ID
    assert support_link["stance"] == "supports"
    assert support_link["evidence_strength"] == "direct"
    assert traced["document"]["ticker"] == TICKER


def test_support_link_joins_still_drive_evidence_expansion(tmp_path):
    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with open_store(shard) as store:
        claim = store.get_object(CLAIM_ID)
        assert claim is not None
        quotes = store._support_objects_for(CLAIM_ID, support_types={"EvidenceQuote"})
        targets = store._support_targets_for(QUOTE_ID, target_types={"ResearchClaim"})
        trace = store.trace(CLAIM_ID)
    assert [quote["id"] for quote in quotes] == [QUOTE_ID]
    assert [target["id"] for target in targets] == [CLAIM_ID]
    assert trace is not None
    assert [quote["id"] for quote in trace["evidence"]["quotes"]] == [QUOTE_ID]


def test_object_traceability_support_link_count_preserved(tmp_path):
    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with sqlite3.connect(f"file:{shard}?mode=ro", uri=True) as conn:
        row = conn.execute(
            """
            SELECT support_link_count, trace_status
            FROM object_traceability
            WHERE object_id = ?
            """,
            (CLAIM_ID,),
        ).fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[1] == "traceable"


def test_shard_versions_bumped_and_spine_locator_excludes_support_links(tmp_path):
    assert COMPANY_SHARD_SCHEMA_VERSION == "krw-ontology-company-shard/v3"
    assert SPINE_PROJECTION_VERSION == "spine-projection/v7"

    shard = build_minimal_shard_with_one_claim_and_quote(tmp_path)
    with sqlite3.connect(f"file:{shard}?mode=ro", uri=True) as conn:
        metadata_json = conn.execute("SELECT value FROM metadata WHERE key = 'build'").fetchone()[0]
    assert "krw-ontology-company-shard/v3" in str(metadata_json)

    import json

    global_spine = shard.parent.parent / "global_spine.sqlite"
    with sqlite3.connect(f"file:{global_spine}?mode=ro", uri=True) as conn:
        locator_rows = conn.execute(
            "SELECT COUNT(*) FROM global_object_locator WHERE object_type = 'SupportLink'"
        ).fetchone()[0]
        replica_rows = conn.execute(
            "SELECT COUNT(*) FROM global_object_replica WHERE object_type = 'SupportLink'"
        ).fetchone()[0]
        claim_rows = conn.execute(
            "SELECT COUNT(*) FROM global_object_locator WHERE object_type = 'ResearchClaim'"
        ).fetchone()[0]
    assert locator_rows == 0
    assert replica_rows == 0
    assert claim_rows == 1
    assert json.loads(str(metadata_json))["spine_projection_version"] == "spine-projection/v7"


def test_router_trace_resolves_support_link_ids(tmp_path):
    shard, global_spine = build_minimal_release(tmp_path)
    assert shard.is_file()
    with open_ontology_store(global_spine) as router:
        # Locator carries no SupportLink rows; the router must fall back to
        # the owning shard's derived support_links table.
        traced = router.trace(SUPPORT_LINK_ID)
        assert traced is not None
        support_link = traced["object"]
        assert support_link["id"] == SUPPORT_LINK_ID
        assert support_link["type"] == "SupportLink"
        assert support_link["from_id"].startswith(f"quote:{TICKER}:")
        assert support_link["to_id"].startswith(f"claim:{TICKER}:")
        assert support_link["stance"] == "supports"
        assert support_link["evidence_strength"] == "direct"
        assert traced["document"]["ticker"] == TICKER
        assert traced["routing"]["tickers"] == [TICKER]

        # Explicit ticker param must resolve through the same fallback.
        traced_with_ticker = router.trace(SUPPORT_LINK_ID, ticker=TICKER)
        assert traced_with_ticker is not None
        assert traced_with_ticker["object"]["id"] == SUPPORT_LINK_ID
        assert traced_with_ticker["object"]["type"] == "SupportLink"

        # Unknown support-link ids and non-support-link misses stay not_found.
        assert router.trace("support_link:SO:CY2023:10K:direct_quote_support:deadbeef") is None
        assert router.trace("support_link:ZZZ:CY2023:10K:direct_quote_support:deadbeef") is None
        assert router.trace(f"claim:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:missing") is None
