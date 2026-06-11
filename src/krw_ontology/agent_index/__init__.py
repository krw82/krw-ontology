"""Agent-facing ontology retrieval index and SDK."""

from krw_ontology.agent_index.builder import (
    AGENT_INDEX_BUILDER_VERSION,
    ArtifactPlanItem,
    CompanyPlanItem,
    FragmentCompileResult,
    IndexBuildPlan,
    IndexFragmentCacheEntry,
    build_agent_index,
    build_index_shards,
    compile_artifact_fragment,
    compile_artifact_fragments,
    diff_source_artifact_manifests,
    gc_index_fragment_cache,
    inspect_index_fragment_cache,
    merge_fragments,
    plan_agent_index,
    verify_index_fragment,
    verify_index_shards,
    verify_source_artifact_manifest,
    write_index_fragment_metadata,
    write_source_artifact_manifest,
)
from krw_ontology.agent_index.claude_sdk import ClaudeAgentQueryPlanner, ClaudeAgentReranker
from krw_ontology.agent_index.retriever import AgentRetriever, DefaultQueryPlanner, QueryPlan
from krw_ontology.agent_index.router import OntologyStoreRouter, open_ontology_store
from krw_ontology.agent_index.store import OntologyStore

__all__ = [
    "AgentRetriever",
    "AGENT_INDEX_BUILDER_VERSION",
    "ArtifactPlanItem",
    "ClaudeAgentQueryPlanner",
    "ClaudeAgentReranker",
    "CompanyPlanItem",
    "DefaultQueryPlanner",
    "FragmentCompileResult",
    "IndexBuildPlan",
    "IndexFragmentCacheEntry",
    "OntologyStore",
    "OntologyStoreRouter",
    "QueryPlan",
    "build_agent_index",
    "build_index_shards",
    "compile_artifact_fragment",
    "compile_artifact_fragments",
    "diff_source_artifact_manifests",
    "gc_index_fragment_cache",
    "inspect_index_fragment_cache",
    "merge_fragments",
    "open_ontology_store",
    "plan_agent_index",
    "verify_index_fragment",
    "verify_index_shards",
    "verify_source_artifact_manifest",
    "write_index_fragment_metadata",
    "write_source_artifact_manifest",
]
