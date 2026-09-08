"""Deterministic grader for the evidence-gold harness.

Pure functions only: no I/O, no clocks, no randomness. Units are iterated in
the order given, and ``aggregate`` emits strata blocks in sorted key order so
results are fully reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from krw_ontology.eval_gold.schema import EvidenceGoldCase, ExpectedEvidence


def normalize_text(s: str) -> str:
    """Lowercase and collapse all whitespace runs to single spaces."""
    return " ".join(s.lower().split())


def _unit_text(unit: dict[str, Any]) -> str:
    parts = [unit.get("title") or "", unit.get("summary") or ""]
    return normalize_text(" ".join(parts))


def match_expected(
    item: ExpectedEvidence, units: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the first unit satisfying ``item``'s anchors, or ``None``.

    A unit is a candidate when its ticker equals ``item.ticker``; the period
    filter applies only when ``item.period`` is set. The anchor is either the
    unit's ``object_id`` being listed in ``item.object_ids`` or any normalized
    ``item.text_fragments`` substring appearing in the unit's title+summary.
    """
    for unit in units:
        if unit.get("ticker") != item.ticker:
            continue
        if item.period and unit.get("period") != item.period:
            continue
        if item.object_ids and unit.get("object_id") in item.object_ids:
            return unit
        if item.text_fragments:
            hay = _unit_text(unit)
            if any(normalize_text(f) in hay for f in item.text_fragments):
                return unit
    return None


@dataclass
class CaseResult:
    case_id: str
    strata: list[str]
    recall: float
    matched_object_ids: list[str] = field(default_factory=list)
    zero_hit: bool = False
    not_disclosed_violation: bool = False
    passed: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


def grade_case(case: EvidenceGoldCase, research_state: dict[str, Any]) -> CaseResult:
    """Grade one gold case against a research-state view.

    ``research_state`` is the adapter-built view (produced by the harness):
    ``"evidence_units"`` is a list of unit dicts (ticker, period, object_id,
    evidence_id, title, summary) and ``"clause_coverage"`` carries the
    clause-hit payload. Only ``evidence_units`` drives grading: the brief's
    aggregate test grades an empty-units state whose clause_coverage is
    non-empty as zero-hit, so empty units dominate (the sketch's
    ``not units and not coverage`` conjunction would fail that test).
    """
    units = research_state.get("evidence_units") or []
    zero_hit = not units

    matched: list[str] = []
    missing: list[str] = []
    required = [e for e in case.expected if e.required]
    optional = [e for e in case.expected if not e.required]
    hit = 0
    denom = 0
    for item in required:
        denom += 1
        m = match_expected(item, units)
        if m is not None:
            hit += 1
            matched.append(m.get("object_id") or m.get("evidence_id") or "")
        else:
            missing.append(item.model_dump_json())
    for item in optional:
        m = match_expected(item, units)
        if m is not None:
            matched.append(m.get("object_id") or m.get("evidence_id") or "")

    violation = False
    if case.expect_not_disclosed:
        for unit in units:
            hay = _unit_text(unit)
            if any(normalize_text(f) in hay for f in case.forbidden_fragments):
                violation = True
                break

    # Recall is vacuously 1.0 when there is no required item (denom == 0):
    # a not-disclosed case decides pass/fail via the violation flag instead.
    recall = (hit / denom) if denom else 1.0
    # Simplified `passed` (authorized by the brief): the sketch's trailing
    # `all(...)` term re-derives `not violation`, so it reduces to this form.
    passed = (not missing) and (not case.expect_not_disclosed or not violation)

    return CaseResult(
        case_id=case.id,
        strata=list(case.strata),
        recall=recall,
        matched_object_ids=matched,
        zero_hit=zero_hit,
        not_disclosed_violation=violation,
        passed=passed,
        detail={"missing_required": missing, "unit_count": len(units)},
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    """Summarize pass rate, mean recall, and zero-hit rate overall and per stratum.

    Results with no strata are grouped under ``"untagged"``; stratum keys are
    emitted in sorted order. Empty inputs yield zero-count blocks rather than
    dividing by zero.
    """

    def _block(rs: list[CaseResult]) -> dict[str, Any]:
        n = len(rs) or 1
        return {
            "cases": len(rs),
            "pass_rate": sum(1 for r in rs if r.passed) / n,
            "mean_recall": sum(r.recall for r in rs) / n,
            "zero_hit_rate": sum(1 for r in rs if r.zero_hit) / n,
        }

    out: dict[str, Any] = {"overall": _block(results), "strata": {}}
    strata: dict[str, list[CaseResult]] = {}
    for r in results:
        for s in r.strata or ["untagged"]:
            strata.setdefault(s, []).append(r)
    out["strata"] = {s: _block(rs) for s, rs in sorted(strata.items())}
    return out
