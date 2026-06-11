"""Tests for deployable observability assets."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


OBSERVABILITY_DIR = Path("ops") / "observability"
ALERTS_PATH = OBSERVABILITY_DIR / "prometheus-krw-ontology-mcp-alerts.yml"
ALERTMANAGER_PATH = OBSERVABILITY_DIR / "alertmanager-krw-ontology-mcp.yml"
DASHBOARD_PATH = OBSERVABILITY_DIR / "grafana-krw-ontology-mcp-dashboard.json"
README_PATH = OBSERVABILITY_DIR / "README.md"


REQUIRED_METRICS = {
    "krw_ontology_mcp_health_ok",
    "krw_ontology_mcp_index_present",
    "krw_ontology_mcp_release_documents",
    "krw_ontology_mcp_release_objects",
    "krw_ontology_mcp_company_shards",
    "krw_ontology_mcp_global_topics",
    "krw_ontology_mcp_store_rotation_pending",
    "krw_ontology_mcp_store_retired_leased",
    "krw_ontology_mcp_store_retired_oldest_age_seconds",
    "krw_ontology_mcp_store_rotations_total",
}

ALERT_METRICS = {
    "krw_ontology_mcp_health_ok",
    "krw_ontology_mcp_index_present",
    "krw_ontology_mcp_release_documents",
    "krw_ontology_mcp_release_objects",
    "krw_ontology_mcp_store_rotation_pending",
    "krw_ontology_mcp_store_retired_leased",
    "krw_ontology_mcp_store_retired_oldest_age_seconds",
    "krw_ontology_mcp_store_rotations_total",
}


def test_prometheus_alert_rules_cover_release_and_hot_swap_metrics() -> None:
    payload = yaml.safe_load(ALERTS_PATH.read_text(encoding="utf-8"))

    assert payload["groups"][0]["name"] == "krw-ontology-mcp"
    rules = payload["groups"][0]["rules"]
    alert_names = {rule["alert"] for rule in rules}
    assert alert_names >= {
        "KRWOntologyMCPDown",
        "KRWOntologyMCPIndexMissing",
        "KRWOntologyMCPHotSwapStuck",
        "KRWOntologyMCPRetiredLeasesPresent",
        "KRWOntologyMCPExcessiveRotations",
        "KRWOntologyMCPProdReleaseEmpty",
    }

    rule_text = ALERTS_PATH.read_text(encoding="utf-8")
    for metric in ALERT_METRICS:
        assert metric in rule_text
    assert 'env="prod"' in rule_text
    assert "release_id" in rule_text


def test_grafana_dashboard_uses_mcp_metrics_and_release_template() -> None:
    dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "krw-ontology-mcp-release"
    assert dashboard["title"] == "KRW Ontology MCP Release"
    assert dashboard["templating"]["list"][0]["name"] == "env"
    assert "label_values(krw_ontology_mcp_health_ok, env)" == dashboard["templating"]["list"][0]["query"]

    panel_exprs = {
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    }
    panel_expr_text = "\n".join(sorted(panel_exprs))
    for metric in REQUIRED_METRICS - {"krw_ontology_mcp_index_present"}:
        assert metric in panel_expr_text
    assert 'env="$env"' in panel_expr_text


def test_alertmanager_template_routes_critical_and_warning_notifications() -> None:
    payload = yaml.safe_load(ALERTMANAGER_PATH.read_text(encoding="utf-8"))

    route = payload["route"]
    assert route["receiver"] == "krw-ontology-mcp-default"
    assert route["group_by"] == ["alertname", "env", "release_id", "service"]
    child_routes = {child["receiver"]: child for child in route["routes"]}
    assert set(child_routes) == {
        "krw-ontology-mcp-critical",
        "krw-ontology-mcp-warning",
    }
    assert 'severity="critical"' in child_routes["krw-ontology-mcp-critical"]["matchers"]
    assert 'severity="warning"' in child_routes["krw-ontology-mcp-warning"]["matchers"]

    receivers = {receiver["name"]: receiver for receiver in payload["receivers"]}
    assert set(receivers) == {
        "krw-ontology-mcp-default",
        "krw-ontology-mcp-critical",
        "krw-ontology-mcp-warning",
    }
    for receiver in receivers.values():
        webhook = receiver["webhook_configs"][0]
        assert webhook["send_resolved"] is True
        assert webhook["url"].startswith("http://127.0.0.1:9099/alertmanager/krw-ontology-mcp/")

    inhibit = payload["inhibit_rules"][0]
    assert 'severity="critical"' in inhibit["source_matchers"]
    assert 'severity="warning"' in inhibit["target_matchers"]
    assert inhibit["equal"] == ["env", "release_id"]


def test_observability_readme_references_scrape_alerts_and_dashboard() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "metrics_path: /metrics" in readme
    assert "prometheus-krw-ontology-mcp-alerts.yml" in readme
    assert "alertmanager-krw-ontology-mcp.yml" in readme
    assert "grafana-krw-ontology-mcp-dashboard.json" in readme
    assert "krw-ontology observability render-prometheus-alerts" in readme
    assert "krw-ontology observability doctor" in readme
    assert "KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS" in readme
    assert "krw-ontology observability render-alertmanager" in readme
    assert "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL" in readme
    for metric in REQUIRED_METRICS:
        assert metric in readme
