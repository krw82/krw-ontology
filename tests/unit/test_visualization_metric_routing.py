from krw_ontology.agent_index.spine_router import _should_attach_chart_series


def test_metric_questions_attach_verified_series_without_requiring_chart_language() -> None:
    assert _should_attach_chart_series("AAPL 매출과 영업이익을 정리해줘")
    assert _should_attach_chart_series("Show Apple's revenue and margin")


def test_non_metric_questions_do_not_attach_series_sidecar() -> None:
    assert not _should_attach_chart_series("AAPL 경영진의 최근 발언을 요약해줘")
