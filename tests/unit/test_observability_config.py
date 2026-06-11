"""Tests for observability config rendering helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from krw_ontology.observability import (
    ALERTMANAGER_RECEIVER_ENV_VARS,
    PROMETHEUS_ALERT_ENV_VARS,
    render_alertmanager_config,
    render_prometheus_alerts_config,
    validate_observability_configs,
    write_observability_doctor_report,
)


ALERTS_TEMPLATE_PATH = Path("ops") / "observability" / "prometheus-krw-ontology-mcp-alerts.yml"
TEMPLATE_PATH = Path("ops") / "observability" / "alertmanager-krw-ontology-mcp.yml"


def test_render_alertmanager_config_injects_receiver_urls_from_env(tmp_path: Path) -> None:
    output_path = tmp_path / "alertmanager.yml"
    env = {
        "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default?token=secret-default",
        "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": "https://alerts.example/critical?token=secret-critical",
        "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL": "https://alerts.example/warning?token=secret-warning",
    }

    result = render_alertmanager_config(TEMPLATE_PATH, output_path, env=env)

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    receivers = {receiver["name"]: receiver for receiver in payload["receivers"]}
    assert receivers["krw-ontology-mcp-default"]["webhook_configs"][0]["url"] == env[
        "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL"
    ]
    assert receivers["krw-ontology-mcp-critical"]["webhook_configs"][0]["url"] == env[
        "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL"
    ]
    assert receivers["krw-ontology-mcp-warning"]["webhook_configs"][0]["url"] == env[
        "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL"
    ]
    assert result["receiver_sources"] == {
        "default": ALERTMANAGER_RECEIVER_ENV_VARS["default"],
        "critical": ALERTMANAGER_RECEIVER_ENV_VARS["critical"],
        "warning": ALERTMANAGER_RECEIVER_ENV_VARS["warning"],
    }


def test_render_alertmanager_config_rejects_missing_and_invalid_urls(tmp_path: Path) -> None:
    output_path = tmp_path / "alertmanager.yml"

    with pytest.raises(ValueError, match="missing receiver URLs"):
        render_alertmanager_config(
            TEMPLATE_PATH,
            output_path,
            env={
                "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default",
                "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": "not-a-url",
            },
        )

    assert not output_path.exists()


def test_render_prometheus_alerts_config_injects_thresholds_from_env(tmp_path: Path) -> None:
    output_path = tmp_path / "prometheus-alerts.yml"
    env = {
        "KRW_PROMETHEUS_MCP_DOWN_FOR": "3m",
        "KRW_PROMETHEUS_INDEX_MISSING_FOR": "90s",
        "KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR": "10m",
        "KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS": "900",
        "KRW_PROMETHEUS_RETIRED_LEASES_FOR": "20m",
        "KRW_PROMETHEUS_ROTATION_WINDOW": "30m",
        "KRW_PROMETHEUS_ROTATION_COUNT": "7",
        "KRW_PROMETHEUS_EXCESSIVE_ROTATIONS_FOR": "8m",
        "KRW_PROMETHEUS_PROD_EMPTY_FOR": "2m",
    }

    result = render_prometheus_alerts_config(ALERTS_TEMPLATE_PATH, output_path, env=env)

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    rules = {rule["alert"]: rule for rule in payload["groups"][0]["rules"]}
    assert rules["KRWOntologyMCPDown"]["for"] == "3m"
    assert rules["KRWOntologyMCPIndexMissing"]["for"] == "90s"
    assert rules["KRWOntologyMCPHotSwapStuck"]["for"] == "10m"
    assert "krw_ontology_mcp_store_retired_oldest_age_seconds > 900" in rules[
        "KRWOntologyMCPHotSwapStuck"
    ]["expr"]
    assert "15 minutes" in rules["KRWOntologyMCPHotSwapStuck"]["annotations"]["description"]
    assert rules["KRWOntologyMCPRetiredLeasesPresent"]["for"] == "20m"
    assert rules["KRWOntologyMCPExcessiveRotations"]["for"] == "8m"
    assert rules["KRWOntologyMCPExcessiveRotations"]["expr"] == (
        "increase(krw_ontology_mcp_store_rotations_total[30m]) > 7"
    )
    assert rules["KRWOntologyMCPProdReleaseEmpty"]["for"] == "2m"
    assert result["threshold_sources"]["rotation_count"] == PROMETHEUS_ALERT_ENV_VARS["rotation_count"]
    assert result["thresholds"]["hot_swap_retired_age_seconds"] == 900


def test_render_prometheus_alerts_config_rejects_invalid_thresholds(tmp_path: Path) -> None:
    output_path = tmp_path / "prometheus-alerts.yml"

    with pytest.raises(ValueError, match="rotation_count must be a positive integer"):
        render_prometheus_alerts_config(
            ALERTS_TEMPLATE_PATH,
            output_path,
            env={"KRW_PROMETHEUS_ROTATION_COUNT": "0"},
        )

    with pytest.raises(ValueError, match="mcp_down_for must be a Prometheus duration"):
        render_prometheus_alerts_config(
            ALERTS_TEMPLATE_PATH,
            output_path,
            env={"KRW_PROMETHEUS_MCP_DOWN_FOR": "five minutes"},
        )

    assert not output_path.exists()


def test_validate_observability_configs_accepts_rendered_production_configs(tmp_path: Path) -> None:
    alertmanager_path = tmp_path / "alertmanager.yml"
    render_alertmanager_config(
        TEMPLATE_PATH,
        alertmanager_path,
        env={
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default",
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": "https://alerts.example/critical",
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL": "https://alerts.example/warning",
        },
    )

    result = validate_observability_configs(
        prometheus_alerts_path=ALERTS_TEMPLATE_PATH,
        alertmanager_path=alertmanager_path,
        env_name="prod",
    )

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["prometheus_alerts"]["alert_count"] == 6
    assert len(result["prometheus_alerts"]["sha256"]) == 64
    assert result["alertmanager"]["receiver_count"] == 3
    assert len(result["alertmanager"]["sha256"]) == 64


def test_validate_observability_configs_rejects_prod_local_receivers() -> None:
    result = validate_observability_configs(
        prometheus_alerts_path=ALERTS_TEMPLATE_PATH,
        alertmanager_path=TEMPLATE_PATH,
        env_name="prod",
    )

    assert result["ok"] is False
    assert any("webhook_url_localhost" in error for error in result["errors"])


def test_write_observability_doctor_report_omits_receiver_urls(tmp_path: Path) -> None:
    alertmanager_path = tmp_path / "alertmanager.yml"
    secret_url = "https://alerts.example/critical?token=secret-critical"
    render_alertmanager_config(
        TEMPLATE_PATH,
        alertmanager_path,
        env={
            "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL": "https://alerts.example/default",
            "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL": secret_url,
            "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL": "https://alerts.example/warning",
        },
    )
    validation = validate_observability_configs(
        prometheus_alerts_path=ALERTS_TEMPLATE_PATH,
        alertmanager_path=alertmanager_path,
        env_name="prod",
    )

    report = write_observability_doctor_report(tmp_path / "observability-doctor.json", validation)

    report_text = Path(report["path"]).read_text(encoding="utf-8")
    assert report["format"] == "krw-ontology-observability-doctor/v1"
    assert len(report["audit_hash"]) == 64
    assert report["ok"] is True
    assert secret_url not in report_text
    assert "sha256" in report_text
