from pathlib import Path

from krw_ontology.quality import metric_gap
from krw_ontology.quality.models import DIRECT_XBRL_METRIC_GAP, RepairJob, RepairPlan
from krw_ontology.quality.queue import QualityRepairStore
from krw_ontology.quality.runner import run_repair_jobs
from krw_ontology.utils.io import read_jsonl, write_jsonl


def _source_document() -> dict:
    return {
        "id": "source:ACME:CY2025:10K",
        "type": "SourceDocument",
        "ticker": "ACME",
        "document_type": "10-K",
        "doc_type_key": "10K",
        "period": "CY2025",
    }


def _fact(object_id: str = "xbrl:revenue:2025") -> dict:
    return {
        "id": object_id,
        "type": "XBRLFact",
        "taxonomy_tag": "Revenue",
        "value": 1_250_000,
        "unit": "USD",
        "context": {
            "fiscal_year": 2025,
            "duration_days": 365,
            "start_date": "2025-01-01",
            "end_date": "2025-12-31",
            "has_dimensions": False,
            "dimensions": [],
        },
    }


def _observation(source_fact_id: str = "xbrl:revenue:2025") -> dict:
    return {
        "id": "metric_observation:ACME:CY2025:10K:revenue",
        "type": "MetricObservation",
        "object_type": "MetricObservation",
        "ticker": "ACME",
        "source_document_id": "source:ACME:CY2025:10K",
        "document_type": "10-K",
        "period": "CY2025",
        "metric_term_id": "term:metric:revenue",
        "metric_name": "revenue",
        "value": 1_250_000,
        "unit": "USD",
        "scale": "ones",
        "fiscal_year": 2025,
        "fiscal_period": None,
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "period_type": "annual",
        "source_type": "xbrl",
        "source_fact_ids": [source_fact_id],
        "source_metric_ids": None,
        "normalization": "reported",
        "dimensions": {},
        "calculation_id": None,
        "confidence": 1.0,
        "schema_version": "krw-ontology/v1",
    }


def _write_document(root: Path, facts: list[dict]) -> None:
    write_jsonl(root / "source_documents.jsonl", [_source_document()])
    write_jsonl(root / "xbrl_facts.jsonl", facts)
    write_jsonl(root / "metric_observations.jsonl", [])


def _patch_canonical_generator(monkeypatch, observation: dict) -> None:
    monkeypatch.setattr(metric_gap, "_metric_observations", lambda **_kwargs: ([observation], []))
    monkeypatch.setattr(
        metric_gap,
        "_load_metric_specs",
        lambda _root: {"revenue": {"xbrl_tags": ["Revenue"], "unit": "USD"}},
    )


def test_direct_xbrl_metric_gap_is_eligible_and_appends_only_metric_observation(
    tmp_path: Path, monkeypatch
) -> None:
    _write_document(tmp_path, [_fact()])
    _patch_canonical_generator(monkeypatch, _observation())

    discovered = metric_gap.discover_direct_xbrl_metric_gaps(tmp_path)

    assert discovered["summary"] == {"eligible": 1, "present": 0, "blocked": 0}
    candidate_id = discovered["eligible"][0]["candidate_id"]
    applied = metric_gap.apply_direct_xbrl_metric_gaps(
        tmp_path,
        candidate_ids=[candidate_id],
        expected_document_fingerprint=discovered["document_fingerprint"],
    )

    assert applied["added_count"] == 1
    assert [row["id"] for row in read_jsonl(tmp_path / "metric_observations.jsonl")] == [
        candidate_id
    ]


def test_duplicate_matching_xbrl_facts_are_blocked(tmp_path: Path, monkeypatch) -> None:
    _write_document(tmp_path, [_fact(), _fact("xbrl:revenue:2025:duplicate")])
    _patch_canonical_generator(monkeypatch, _observation())

    discovered = metric_gap.discover_direct_xbrl_metric_gaps(tmp_path)

    assert discovered["summary"]["eligible"] == 0
    assert discovered["summary"]["blocked"] == 1
    assert discovered["blocked"][0]["status"] == "ambiguous_direct_xbrl_fact"


def test_executor_commits_only_metric_observations_to_running_root(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "running"
    ontology_dir = root / "companies" / "ACME" / "ontology" / "10K" / "CY2025"
    ontology_dir.mkdir(parents=True)
    _write_document(ontology_dir, [_fact()])
    _patch_canonical_generator(monkeypatch, _observation())
    discovered = metric_gap.discover_direct_xbrl_metric_gaps(ontology_dir)

    job = RepairJob(
        job_id="qr_test-direct-xbrl",
        plan_id="qr_test",
        kind=DIRECT_XBRL_METRIC_GAP,
        ticker="ACME",
        document_type="10-K",
        doc_type_key="10K",
        period="CY2025",
        stage="metric_gap",
        ontology_dir=str(ontology_dir),
        count=1,
        payload={
            "document_fingerprint": discovered["document_fingerprint"],
            "candidate_ids": [discovered["eligible"][0]["candidate_id"]],
        },
    )
    store = QualityRepairStore(root)
    plan = store.add_plan(
        RepairPlan(
            plan_id="qr_test",
            global_spine_path="test",
            release_label="test",
            min_docs=1,
            job_ids=[],
            summary={},
        ),
        [job],
    )

    result = run_repair_jobs(store=store, jobs=store.list_jobs(plan_id=plan.plan_id), root=root)

    repaired = store.load_job(job.job_id)
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert repaired.status == "succeeded"
    assert repaired.payload["auto_created_metric_observations"] == 1
    assert repaired.payload["publish_required"] is True
    assert [row["id"] for row in read_jsonl(ontology_dir / "metric_observations.jsonl")] == [
        job.payload["candidate_ids"][0]
    ]
    assert (root / ".krw_pipeline" / "quality" / "transactions" / job.job_id / "backup").is_dir()
