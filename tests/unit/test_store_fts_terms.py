"""Singularized FTS prefix terms and the concept-OR strategy rung.

``object_fts`` uses the ``unicode61`` tokenizer (no stemming), so a plural
hint term emitted as ``buybacks*`` can never match the singular ``buyback``
token in filing claims (the META CY2025 share-buybacks miss).  Singularizing
the stem before the prefix star is a pure generalization — every token that
``buybacks*`` matched also starts with ``buyback`` — and a concept-OR rung
restricted to the clause's own declared terms recovers evidence that satisfies
only one concept of the conjunction, without opening the vocabulary the way
the ``allow_relaxed`` pass does.

The raw token contract is unchanged: ``_planned_query_terms`` keeps returning
the untouched tokens (they feed ``planned_evidence_terms`` and the atomic
visibility check in the response compiler, which requires exact token
identity).  Singularization happens only when prefix terms are emitted for
FTS queries.

Shard recipe mirrors ``tests/unit/test_store_alias_expansion.py`` (minimal
META/CY2025 release, quotes only) with the two diagnosed META anchor shapes:
a claim text carrying the singular ``buyback`` token and an authorization
text carrying ``share`` but no buyback token at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from krw_ontology.agent_index.spine_builder import build_spine_shard_release_outputs
from krw_ontology.agent_index.store import (
    OntologyStore,
    _planned_fts_original_prefix_terms,
    _planned_fts_prefix_terms,
    _planned_query_terms,
)
from krw_ontology.pipeline.stages.build_indexes import build_indexes
from krw_ontology.utils.io import atomic_write_json, write_jsonl

TICKER = "META"
DOCUMENT_TYPE = "10-K"
DOC_TYPE_KEY = "10K"
PERIOD = "CY2025"

SOURCE_DOCUMENT_ID = f"source:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}"

# Anchor 1 mirror: carries the singular ``buyback`` token (and ``share``) but
# never the plural ``buybacks`` — the strict ``share* buybacks*`` conjunction
# missed exactly this shape.
BUYBACK_QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0001"
BUYBACK_QUOTE_TEXT = (
    "Meta completed its share buyback program, repurchasing 26,264 million "
    "shares of Class A common stock during 2025."
)
# Anchor 2 mirror: ``share`` yes, ``buyback`` no — only the concept-OR rung
# over the clause's own declared terms can reach it.
AUTHORIZATION_QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0002"
AUTHORIZATION_QUOTE_TEXT = (
    "As of December 31, 2025, $25.03 billion remained available and "
    "authorized for share repurchases."
)
# Plural control: ``buybacks`` plural token — the singularized ``buyback*``
# prefix must keep matching it (pure generalization, no plural regression).
PLURAL_QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0003"
PLURAL_QUOTE_TEXT = "The board approved additional share buybacks for 2026."
# Unrelated control: matches none of the clause vocabulary.
CONTROL_QUOTE_ID = f"quote:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0004"
CONTROL_QUOTE_TEXT = "Server operating margin expanded because data center demand increased."

QUOTE_TEXTS = {
    BUYBACK_QUOTE_ID: BUYBACK_QUOTE_TEXT,
    AUTHORIZATION_QUOTE_ID: AUTHORIZATION_QUOTE_TEXT,
    PLURAL_QUOTE_ID: PLURAL_QUOTE_TEXT,
    CONTROL_QUOTE_ID: CONTROL_QUOTE_TEXT,
}
QUOTE_IDS = list(QUOTE_TEXTS)


# ---------------------------------------------------------------------------
# Unit level: prefix emission and the singularization rule
# ---------------------------------------------------------------------------


def test_prefix_terms_singularize_plural_stems():
    # The exact pipeline the store uses at FTS emission time.
    assert _planned_fts_prefix_terms(_planned_query_terms("share buybacks")) == [
        "share*",
        "buyback*",
    ]


def test_integral_and_irregular_s_endings_keep_their_token():
    # ``sales`` is the canonical revenue-clause plural-tantum; stripping its
    # ``s`` would change the token identity the filing vocabulary expects.
    assert _planned_fts_prefix_terms(["sales"]) == ["sales*"]
    # ``series`` is its own singular; ``serie*`` would be a broken stem.
    assert _planned_fts_prefix_terms(["series"]) == ["series*"]
    # Double-s class: stripping yields a non-word.
    for term in ("business", "access", "gross", "process"):
        assert _planned_fts_prefix_terms([term]) == [f"{term}*"]
    # -is / -us endings and short remainders: integral or malformed stems.
    for term in ("analysis", "paris", "status", "news", "this", "was", "keys", "does"):
        assert _planned_fts_prefix_terms([term]) == [f"{term}*"]


def test_non_plural_and_regular_plural_terms():
    assert _planned_fts_prefix_terms(["revenue"]) == ["revenue*"]
    assert _planned_fts_prefix_terms(["share"]) == ["share*"]
    assert _planned_fts_prefix_terms(["revenues"]) == ["revenue*"]
    assert _planned_fts_prefix_terms(["costs"]) == ["cost*"]
    assert _planned_fts_prefix_terms(["plans"]) == ["plan*"]
    assert _planned_fts_prefix_terms(["shareholders"]) == ["shareholder*"]


def test_singularized_prefix_still_covers_the_plural_token():
    """Prefix-superset guarantee: the emitted stem must be a prefix of the
    original plural token, so ``buyback*`` matches every token that the old
    ``buybacks*`` prefix matched (pure generalization, zero recall loss)."""
    for plural in ("buybacks", "revenues", "assets", "earnings", "costs", "regulators"):
        prefix_term = _planned_fts_prefix_terms([plural])[0]
        stem = prefix_term[:-1]
        assert plural.startswith(stem), f"{stem}* no longer matches the token {plural}"
        assert len(stem) < len(plural)


def test_prefix_term_dedupe_collapses_singular_plural_pairs():
    assert _planned_fts_prefix_terms(["cost", "costs"]) == ["cost*"]


def test_original_prefix_terms_keep_the_plural_token():
    """The original-term strict baseline carries the untouched plural token:
    ``constraints*`` matches the plural ``constraints`` token only, which is
    exactly the pre-singularization match set the original-first merge must
    preserve at the head of every singularized strict window."""
    assert _planned_fts_original_prefix_terms(["constraints"]) == ["constraints*"]
    assert _planned_fts_original_prefix_terms(["buybacks"]) == ["buybacks*"]
    # Non-plural terms emit identically in both forms (no merge needed).
    assert _planned_fts_original_prefix_terms(["revenue", "supply"]) == [
        "revenue*",
        "supply*",
    ]


# ---------------------------------------------------------------------------
# Mini shard (META anchor shapes)
# ---------------------------------------------------------------------------


def _write_minimal_artifacts(root: Path) -> None:
    ontology_dir = root / "companies" / TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    spans = []
    quotes = []
    for index, quote_id in enumerate(QUOTE_IDS):
        span_id = f"span:{TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:{index:04d}"
        text = QUOTE_TEXTS[quote_id]
        spans.append(
            {
                "id": span_id,
                "type": "SourceSpan",
                "ticker": TICKER,
                "source_document_id": SOURCE_DOCUMENT_ID,
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "section_name": "item7",
                "section_key": "item7",
                "span_index": index + 1,
                "text": text,
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )
        quotes.append(
            {
                "id": quote_id,
                "type": "EvidenceQuote",
                "ticker": TICKER,
                "source_document_id": SOURCE_DOCUMENT_ID,
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "source_span_id": span_id,
                "quote_text": text,
                "quote_type": "business_update",
                "section_name": "item7",
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )

    write_jsonl(ontology_dir / "spans.jsonl", spans)
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", quotes)
    write_jsonl(ontology_dir / "claims.jsonl", [])
    write_jsonl(ontology_dir / "support_links.jsonl", [])
    write_jsonl(ontology_dir / "metric_observations.jsonl", [])
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


@pytest.fixture(scope="module")
def shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("fts-terms-release")
    _write_minimal_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-fts-terms",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{TICKER}.sqlite"
    assert shard.is_file()
    return shard


def _query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "meta_share_buybacks",
        "retrieval_query": "Meta share buybacks during 2025 and remaining buyback authorization",
        "retrieval_terms": ["share buybacks"],
        "tickers": [TICKER],
        "limit": 5,
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def test_plural_hint_clause_matches_singular_buyback_token(shard_path):
    """RED pre-change: strict ``share* buybacks*`` misses the singular-token
    anchor entirely; the singularized ``buyback*`` prefix recovers it."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store)

    by_id = {row["id"]: row for row in rows}
    assert BUYBACK_QUOTE_ID in by_id
    assert by_id[BUYBACK_QUOTE_ID]["planned_match_mode"] == "strict"
    # The plural token keeps matching: pure generalization, no plural loss.
    assert PLURAL_QUOTE_ID in by_id
    assert by_id[PLURAL_QUOTE_ID]["planned_match_mode"] == "strict"
    assert diagnostics["lexical_terms"] == ["share", "buybacks"]


def test_concept_or_recovers_evidence_satisfying_one_concept(shard_path):
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(store)

    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    by_id = {row["id"]: row for row in rows}
    # The authorization anchor satisfies only the ``share`` concept: strict
    # AND cannot reach it, the concept-OR rung over the declared terms can.
    assert AUTHORIZATION_QUOTE_ID in by_id
    assert by_id[AUTHORIZATION_QUOTE_ID]["planned_match_mode"] == "relaxed"
    # The rung never leaves the clause vocabulary: the unrelated control row
    # matches neither declared concept.
    assert CONTROL_QUOTE_ID not in by_id

    assert diagnostics["concept_or_attempted"] is True
    assert diagnostics["concept_or_result_count"] == 1
    # Default uncertainty is LOW, so the broader relaxed pass stays off.
    assert diagnostics["relaxed_enabled"] is False


def test_concept_or_never_runs_after_strict_success(shard_path):
    """The ladder stops at the first success: a clause whose strict AND fills
    the window must never execute the concept-OR attempt."""
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(
            store,
            clause_id="margin",
            retrieval_query="SO server operating margin",
            retrieval_terms=["server margin"],
            limit=1,
        )

    assert [row["id"] for row in rows] == [CONTROL_QUOTE_ID]
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    assert diagnostics["concept_or_attempted"] is False
    assert diagnostics["concept_or_result_count"] == 0


def test_single_term_clause_skips_concept_or(shard_path):
    with OntologyStore(shard_path) as store:
        rows, diagnostics = _query(
            store,
            clause_id="demand",
            retrieval_query="data center demand",
            retrieval_terms=["demand"],
        )

    assert [row["id"] for row in rows] == [CONTROL_QUOTE_ID]
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    assert diagnostics["concept_or_attempted"] is False


def test_concept_or_not_gated_by_allow_relaxed(shard_path):
    """concept_or is concept-scoped so ``allow_relaxed`` must not suppress it,
    while the relaxed pass itself stays gated exactly as before."""
    with OntologyStore(shard_path) as store:
        gated, gated_diagnostics = _query(store)
        relaxed, relaxed_diagnostics = _query(store, allow_relaxed=True)

    # Both runs recover the single-concept anchor through the concept-OR rung.
    for rows, diagnostics in ((gated, gated_diagnostics), (relaxed, relaxed_diagnostics)):
        ids = {row["id"] for row in rows}
        assert {BUYBACK_QUOTE_ID, PLURAL_QUOTE_ID, AUTHORIZATION_QUOTE_ID} <= ids
        assert diagnostics["concept_or_result_count"] >= 1

    assert gated_diagnostics["relaxed_enabled"] is False
    assert relaxed_diagnostics["relaxed_enabled"] is True
    # The relaxed pass only appends unseen rows: same window, no duplicates.
    assert {row["id"] for row in gated} == {row["id"] for row in relaxed}
    assert len(relaxed) == len({row["id"] for row in relaxed})


# ---------------------------------------------------------------------------
# Original-first merge for the singularized strict path (the nvda multispan
# window regression).  Singularization is a pure superset in MATCH terms, but
# under a fixed window + bm25 a superset re-ranks: newly-matching rows
# (singular-token carriers) can displace original-term carriers that the
# pre-singularization query ranked inside the window.  The strict FTS now
# executes with the ORIGINAL prefixes AND the SINGULARIZED prefixes; the
# original-term rows keep their bm25 order first (stable object-id dedupe)
# and singularized-only rows append within the remaining budget.  A window
# the original terms already filled is therefore identical to the
# pre-singularization order — a generalized prefix never displaces an
# original carrier — while a sparse original window still recovers
# singular-token carriers in the tail (the META buyback anchor shape).
#
# Shard recipe: one plural carrier pair (P1 outranks P2 under the original
# ``supply* constraints*`` conjunction) plus a singular-token carrier whose
# dense ``supply constraint`` repetition outranks BOTH plural carriers under
# the singularized ``supply* constraint*`` conjunction.
# ---------------------------------------------------------------------------

SNG_TICKER = "SNG"

# Plural carriers: match both the original and the singularized prefix AND.
SNG_P1_QUOTE_ID = f"quote:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0001"
SNG_P1_QUOTE_TEXT = (
    "Supply constraints widened in the outlook as supply constraints "
    "persisted across component vendors."
)
SNG_P2_QUOTE_ID = f"quote:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0002"
SNG_P2_QUOTE_TEXT = (
    "Supply constraints appeared once in the annual report of the "
    "diversified manufacturer."
)
# Singular-token carrier: matches ONLY the singularized prefix AND and would
# rank first under it, displacing P2 from a full window pre-merge.
SNG_SINGULAR_QUOTE_ID = f"quote:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}:0003"
SNG_SINGULAR_QUOTE_TEXT = (
    "Supply constraint risk supply constraint risk supply constraint risk "
    "supply constraint risk supply constraint risk."
)

SNG_QUOTE_TEXTS = {
    SNG_P1_QUOTE_ID: SNG_P1_QUOTE_TEXT,
    SNG_P2_QUOTE_ID: SNG_P2_QUOTE_TEXT,
    SNG_SINGULAR_QUOTE_ID: SNG_SINGULAR_QUOTE_TEXT,
}


def _write_sng_artifacts(root: Path) -> None:
    ontology_dir = root / "companies" / SNG_TICKER / "ontology" / DOC_TYPE_KEY / PERIOD
    sources_dir = root / "companies" / SNG_TICKER / "sources" / DOC_TYPE_KEY / PERIOD
    ontology_dir.mkdir(parents=True)
    sources_dir.mkdir(parents=True)

    spans = []
    quotes = []
    for index, (quote_id, text) in enumerate(SNG_QUOTE_TEXTS.items()):
        span_id = f"span:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}:item7:{index:04d}"
        spans.append(
            {
                "id": span_id,
                "type": "SourceSpan",
                "ticker": SNG_TICKER,
                "source_document_id": f"source:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}",
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "section_name": "item7",
                "section_key": "item7",
                "span_index": index + 1,
                "text": text,
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )
        quotes.append(
            {
                "id": quote_id,
                "type": "EvidenceQuote",
                "ticker": SNG_TICKER,
                "source_document_id": f"source:{SNG_TICKER}:{PERIOD}:{DOC_TYPE_KEY}",
                "document_type": DOCUMENT_TYPE,
                "period": PERIOD,
                "source_span_id": span_id,
                "quote_text": text,
                "quote_type": "business_update",
                "section_name": "item7",
                "review_status": "accepted",
                "schema_version": "0.1.0",
            }
        )

    write_jsonl(ontology_dir / "spans.jsonl", spans)
    write_jsonl(ontology_dir / "evidence_quotes.jsonl", quotes)
    write_jsonl(ontology_dir / "claims.jsonl", [])
    write_jsonl(ontology_dir / "support_links.jsonl", [])
    write_jsonl(ontology_dir / "metric_observations.jsonl", [])
    atomic_write_json(
        ontology_dir / "section_quality.json",
        {"status": "pass", "missing_core_sections": [], "fail_reasons": []},
    )
    build_indexes(
        ticker=SNG_TICKER,
        period=PERIOD,
        doc_type_key=DOC_TYPE_KEY,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=root,
        document_type=DOCUMENT_TYPE,
    )


@pytest.fixture(scope="module")
def sng_shard_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("fts-original-first-release")
    _write_sng_artifacts(root)
    result = build_spine_shard_release_outputs(
        root,
        release_id="test-store-fts-original-first",
        workers=1,
        no_cache=True,
    )
    shard = result.global_spine_path.parent / "companies" / f"{SNG_TICKER}.sqlite"
    assert shard.is_file()
    return shard


def _sng_query(store: OntologyStore, **overrides):
    kwargs = {
        "clause_id": "sng_supply_constraints",
        "retrieval_query": "supply constraints outlook",
        "retrieval_terms": ["supply constraints"],
        "tickers": [SNG_TICKER],
    }
    kwargs.update(overrides)
    return store.query_planned_compact_with_diagnostics(**kwargs)


def test_full_original_window_is_not_displaced_by_singularized_rows(sng_shard_path):
    """Regression pin (nvda multispan shape): the original ``supply*
    constraints*`` conjunction fills the window (limit=2 → P1, P2 in bm25
    order), and the singular-token carrier — which the singularized
    conjunction ranks FIRST — must NOT displace P2.  The window content and
    order are identical to the pre-singularization window."""
    with OntologyStore(sng_shard_path) as store:
        rows, diagnostics = _sng_query(store, limit=2)

    assert [row["id"] for row in rows] == [SNG_P1_QUOTE_ID, SNG_P2_QUOTE_ID]
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    # The window is full of original rows: no lower rung fires.
    assert diagnostics["concept_or_attempted"] is False
    assert diagnostics["fts_strict_result_count"] == 2


def test_sparse_original_window_appends_singularized_only_rows(sng_shard_path):
    """Recovery pin (META buyback shape): the original conjunction returns
    only two rows for a three-slot window, so the singularized-only carrier
    is APPENDED after them — original rows keep their order and the anchor
    enters the tail instead of outranking them."""
    with OntologyStore(sng_shard_path) as store:
        rows, diagnostics = _sng_query(store, limit=3)

    assert [row["id"] for row in rows] == [
        SNG_P1_QUOTE_ID,
        SNG_P2_QUOTE_ID,
        SNG_SINGULAR_QUOTE_ID,
    ]
    # The appended carrier is a strict conjunction hit, not a relaxed unit.
    by_id = {row["id"]: row for row in rows}
    assert by_id[SNG_SINGULAR_QUOTE_ID]["planned_match_mode"] == "strict"
    assert diagnostics["concept_or_attempted"] is False


def test_non_plural_clause_runs_a_single_strict_execution(sng_shard_path):
    """Clauses whose prefixes singularize to themselves (``supply*``) skip the
    second FTS execution: the merged window equals the plain singularized
    window — every carrier is strict, in plain bm25 order."""
    with OntologyStore(sng_shard_path) as store:
        rows, diagnostics = _sng_query(
            store,
            clause_id="sng_supply_exposure",
            retrieval_query="supplier concentration exposure",
            retrieval_terms=["supply"],
            limit=3,
        )

    # Every row carries the token; nothing is displaced or appended and the
    # dense singular-token carrier legitimately ranks first under bm25.
    assert [row["id"] for row in rows] == [
        SNG_SINGULAR_QUOTE_ID,
        SNG_P1_QUOTE_ID,
        SNG_P2_QUOTE_ID,
    ]
    assert {row["planned_match_mode"] for row in rows} == {"strict"}
    # Single declared term: the concept-OR rung never fires.
    assert diagnostics["concept_or_attempted"] is False
