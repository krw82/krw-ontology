import sqlite3
from pathlib import Path

from krw_ontology.quality.models import DIRECT_XBRL_METRIC_GAP
from krw_ontology.quality.scanner import QualityShardScanner


def test_quality_plan_creates_one_direct_xbrl_metric_gap_job_per_document(
    tmp_path: Path, monkeypatch
) -> None:
    ontology_dir = tmp_path / "running" / "companies" / "ACME" / "ontology" / "10K" / "CY2025"
    ontology_dir.mkdir(parents=True)
    index_path = tmp_path / "indexes" / "companies" / "ACME.sqlite"
    index_path.parent.mkdir(parents=True)
    with sqlite3.connect(index_path) as conn:
        conn.execute(
            """
            CREATE TABLE documents(
                ticker TEXT, document_type TEXT, doc_type_key TEXT, period TEXT,
                ontology_dir TEXT, artifact_index_path TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("ACME", "10-K", "10K", "CY2025", str(ontology_dir), "artifact_index.json"),
        )
        conn.commit()

    monkeypatch.setattr(
        "krw_ontology.quality.scanner.discover_direct_xbrl_metric_gaps",
        lambda _path: {
            "contract": "krw-ontology-direct-xbrl-metric-gap/v1",
            "document_fingerprint": "sha256:test",
            "summary": {"eligible": 2, "present": 1, "blocked": 0},
            "eligible": [
                {"candidate_id": "metric_observation:one", "source_fact_id": "xbrl:one"},
                {"candidate_id": "metric_observation:two", "source_fact_id": "xbrl:two"},
            ],
        },
    )

    jobs = QualityShardScanner(index_path).build_repair_jobs(
        plan_id="qr_test",
        kinds=[DIRECT_XBRL_METRIC_GAP],
    )

    assert len(jobs) == 1
    assert jobs[0].kind == DIRECT_XBRL_METRIC_GAP
    assert jobs[0].count == 2
    assert jobs[0].payload["candidate_ids"] == [
        "metric_observation:one",
        "metric_observation:two",
    ]
    assert jobs[0].payload["document_fingerprint"] == "sha256:test"
