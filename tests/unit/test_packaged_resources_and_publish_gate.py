"""Regression tests for packaged runtime resources and publish quality gates."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import krw_ontology.factor_taxonomy as factor_taxonomy
import krw_ontology.registry as ontology_registry
import krw_ontology.sector_packs as sector_packs
import krw_ontology.validators.metric_validator as metric_validator
import krw_ontology.validators.relation_validator as relation_validator
from krw_ontology.cli.main import _assert_company_context_publishable
from krw_ontology.pipeline.stages.build_governance import build_governance_artifacts
from krw_ontology.utils.io import atomic_write_json


def test_factor_taxonomy_falls_back_to_package_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    taxonomy_dir = package_dir / "resources" / "taxonomy"
    taxonomy_dir.mkdir(parents=True)
    (taxonomy_dir / "factors.yaml").write_text(
        """
factors:
  custom_factor:
    category: test
    aliases: ["custom factor"]
    benchmarks: ["CUSTOM"]
    default_channels: ["revenue"]
""".strip()
    )

    monkeypatch.setattr(factor_taxonomy, "__file__", str(package_dir / "factor_taxonomy.py"))
    monkeypatch.setattr(factor_taxonomy, "_repo_root", lambda: tmp_path / "missing-repo")
    factor_taxonomy.load_factor_taxonomy.cache_clear()

    loaded = factor_taxonomy.load_factor_taxonomy()

    assert loaded.path == taxonomy_dir / "factors.yaml"
    assert "custom_factor" in loaded.factors
    assert loaded.alias_to_key["custom_factor"] == "custom_factor"

    factor_taxonomy.load_factor_taxonomy.cache_clear()


def test_sector_packs_fall_back_to_package_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    packs_dir = package_dir / "resources" / "sector_packs"
    packs_dir.mkdir(parents=True)
    (packs_dir / "generic.yaml").write_text(
        """
sector: generic
business_activities:
  product_sales:
    name: Product sales
    aliases: ["revenue"]
    related_metrics: ["revenue"]
factors:
  demand:
    aliases: ["demand"]
    channels: ["revenue"]
""".strip()
    )

    monkeypatch.setattr(sector_packs, "__file__", str(package_dir / "sector_packs.py"))

    loaded = sector_packs.load_sector_packs()
    chosen = sector_packs.choose_sector_pack("customer demand increased", loaded)
    merged = sector_packs.merged_pack_for_text("revenue increased", loaded)

    assert [(pack.sector, pack.path) for pack in loaded] == [("generic", packs_dir / "generic.yaml")]
    assert chosen.sector == "generic"
    assert merged.business_activities["product_sales"]["name"] == "Product sales"


def test_sector_pack_empty_resource_error_is_explicit(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="No sector pack YAML files found"):
        sector_packs.choose_sector_pack("anything", packs=[])


def test_metric_dictionary_falls_back_to_package_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    schema_dir = package_dir / "resources" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "metric_dictionary.yaml").write_text(
        """
canonical_metrics:
  custom_metric:
    description: test metric
""".strip()
    )

    monkeypatch.setattr(metric_validator, "__file__", str(package_dir / "validators" / "metric_validator.py"))
    monkeypatch.setattr(metric_validator, "_METRIC_DICT_PATH", tmp_path / "missing" / "metric_dictionary.yaml")

    loaded = metric_validator._load_metric_names(tmp_path / "also-missing" / "metric_dictionary.yaml")

    assert loaded == {"custom_metric"}


def test_relations_fall_back_to_package_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    schema_dir = package_dir / "resources" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "relations.yaml").write_text(
        """
relations:
  - id: rel:test
    name: supports
    from: ResearchClaim
    to: EvidenceQuote
""".strip()
    )

    monkeypatch.setattr(relation_validator, "__file__", str(package_dir / "validators" / "relation_validator.py"))
    monkeypatch.setattr(relation_validator, "_RELATIONS_PATH", tmp_path / "missing" / "relations.yaml")

    loaded = relation_validator._load_relations(tmp_path / "also-missing" / "relations.yaml")

    assert loaded == [
        {
            "id": "rel:test",
            "name": "supports",
            "from": "ResearchClaim",
            "to": "EvidenceQuote",
        }
    ]


def test_ontology_registry_falls_back_to_package_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    (package_dir / "resources").mkdir(parents=True)
    (package_dir / "resources" / "registry.yaml").write_text(
        """
registry_version: test-registry
objects:
  ResearchClaim:
    artifact_file: research_claims.jsonl
    text_fields: [claim_text, summary]
""".strip()
    )

    monkeypatch.setattr(ontology_registry, "__file__", str(package_dir / "registry.py"))

    loaded = ontology_registry.load_ontology_registry(tmp_path / "missing" / "registry.yaml")

    assert loaded.path == package_dir / "resources" / "registry.yaml"
    assert loaded.version == "test-registry"
    assert loaded.canonical_artifacts == {"ResearchClaim": "research_claims.jsonl"}
    assert loaded.text_fields_by_type == {"ResearchClaim": ["claim_text", "summary"]}


def test_build_governance_artifacts_uses_packaged_registry_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    package_dir = tmp_path / "site-packages" / "krw_ontology"
    (package_dir / "resources").mkdir(parents=True)
    (package_dir / "resources" / "registry.yaml").write_text(
        """
registry_version: governance-registry
objects:
  EvidenceQuote:
    artifact_file: evidence_quotes.jsonl
    text_fields: [quote_text]
""".strip()
    )
    ontology_dir = tmp_path / "company" / "ontology" / "10K" / "CY2025"
    ontology_dir.mkdir(parents=True)

    monkeypatch.setattr(ontology_registry, "__file__", str(package_dir / "registry.py"))

    build_governance_artifacts(
        ontology_dir=ontology_dir,
        ticker="TEST",
        period="CY2025",
        document_type="10-K",
        source_document_id="source_document:TEST:CY2025:10K",
        config=object(),
    )

    snapshot = json.loads((ontology_dir / "ontology_registry_snapshots.jsonl").read_text())

    assert snapshot["registry_version"] == "governance-registry"
    assert snapshot["canonical_artifacts"] == {"EvidenceQuote": "evidence_quotes.jsonl"}
    assert snapshot["text_fields_by_type"] == {"EvidenceQuote": ["quote_text"]}


def test_pyproject_force_includes_runtime_resource_files():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert force_include["ontology/taxonomy/factors.yaml"] == (
        "krw_ontology/resources/taxonomy/factors.yaml"
    )
    assert force_include["ontology/taxonomy/sector_expectations.yaml"] == (
        "krw_ontology/resources/taxonomy/sector_expectations.yaml"
    )
    assert force_include["ontology/registry.yaml"] == "krw_ontology/resources/registry.yaml"
    assert force_include["ontology/schema/metric_dictionary.yaml"] == (
        "krw_ontology/resources/schema/metric_dictionary.yaml"
    )
    assert force_include["ontology/schema/relations.yaml"] == (
        "krw_ontology/resources/schema/relations.yaml"
    )
    assert force_include["ontology/sector_packs/generic.yaml"] == (
        "krw_ontology/resources/sector_packs/generic.yaml"
    )
    assert force_include["ontology/sector_packs/energy_lng.yaml"] == (
        "krw_ontology/resources/sector_packs/energy_lng.yaml"
    )


def test_publish_gate_accepts_healthy_company_context(tmp_path: Path):
    _write_context_index(
        tmp_path,
        "MSFT",
        {
            "company_business_profiles": 1,
            "temporal_links": 10,
            "trend_observations": 2,
            "change_events": 2,
            "quality_events": 0,
            "edges": 12,
            "rejected_objects": 0,
        },
    )

    _assert_company_context_publishable(tmp_path, "MSFT")


@pytest.mark.parametrize(
    ("bad_counts", "message"),
    [
        ({"company_business_profiles": 0}, "company_business_profiles=0"),
        ({"temporal_links": 0}, "temporal_links=0"),
        ({"trend_observations": 0}, "trend_observations=0"),
        ({"change_events": 0}, "change_events=0"),
        ({"edges": 0}, "edges=0"),
        ({"quality_events": 1}, "quality_events=1"),
        ({"rejected_objects": 1}, "rejected_objects=1"),
    ],
)
def test_publish_gate_blocks_unhealthy_company_context_counts(
    tmp_path: Path,
    bad_counts: dict[str, int],
    message: str,
):
    counts = {
        "company_business_profiles": 1,
        "temporal_links": 10,
        "trend_observations": 2,
        "change_events": 2,
        "quality_events": 0,
        "edges": 12,
        "rejected_objects": 0,
    }
    counts.update(bad_counts)
    _write_context_index(tmp_path, "MSFT", counts)

    with pytest.raises(RuntimeError, match=message):
        _assert_company_context_publishable(tmp_path, "MSFT")


def test_publish_gate_blocks_missing_company_context(tmp_path: Path):
    with pytest.raises(RuntimeError, match="missing company context artifact index"):
        _assert_company_context_publishable(tmp_path, "MSFT")


def _write_context_index(root: Path, ticker: str, counts: dict[str, int]) -> None:
    context_dir = root / "companies" / ticker / "context"
    context_dir.mkdir(parents=True)
    atomic_write_json(
        context_dir / "artifact_index.json",
        {
            "ticker": ticker,
            "document_type": "COMPANY",
            "period": "ALL",
            "counts": counts,
        },
    )
