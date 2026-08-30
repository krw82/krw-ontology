"""registry.yaml is the only artifact-file authority; builder keys must match."""

from pathlib import Path

from krw_ontology.agent_index.builder import OBJECT_FILE_KEYS
from krw_ontology.pipeline.stages.validate_ontology import (
    assert_registry_matches_object_file_keys,
)
from krw_ontology.registry import load_ontology_registry

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_registry_artifact_files_match_builder_object_file_keys():
    registry = load_ontology_registry()
    registry_files = registry.artifact_files()
    assert registry_files == set(OBJECT_FILE_KEYS), (
        f"drift: only-registry={registry_files - set(OBJECT_FILE_KEYS)} "
        f"only-builder={set(OBJECT_FILE_KEYS) - registry_files}"
    )


def test_assert_registry_matches_object_file_keys_passes():
    assert_registry_matches_object_file_keys()


def test_objects_yaml_is_not_shipped():
    assert not (REPO_ROOT / "ontology" / "schema" / "objects.yaml").exists()
