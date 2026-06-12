"""Observability deployment config helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_PROMETHEUS_ALERTS_TEMPLATE_PATH = (
    Path("ops") / "observability" / "prometheus-krw-ontology-mcp-alerts.yml"
)
DEFAULT_ALERTMANAGER_TEMPLATE_PATH = (
    Path("ops") / "observability" / "alertmanager-krw-ontology-mcp.yml"
)
OBSERVABILITY_DOCTOR_REPORT_FORMAT = "krw-ontology-observability-doctor/v1"
PROMETHEUS_ALERT_ENV_VARS = {
    "mcp_down_for": "KRW_PROMETHEUS_MCP_DOWN_FOR",
    "global_spine_missing_for": "KRW_PROMETHEUS_GLOBAL_SPINE_MISSING_FOR",
    "hot_swap_stuck_for": "KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR",
    "hot_swap_retired_age_seconds": "KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS",
    "retired_leases_for": "KRW_PROMETHEUS_RETIRED_LEASES_FOR",
    "rotation_window": "KRW_PROMETHEUS_ROTATION_WINDOW",
    "rotation_count": "KRW_PROMETHEUS_ROTATION_COUNT",
    "excessive_rotations_for": "KRW_PROMETHEUS_EXCESSIVE_ROTATIONS_FOR",
    "prod_empty_for": "KRW_PROMETHEUS_PROD_EMPTY_FOR",
}
PROMETHEUS_REQUIRED_METRICS = {
    "krw_ontology_mcp_health_ok",
    "krw_ontology_mcp_global_spine_present",
    "krw_ontology_mcp_release_documents",
    "krw_ontology_mcp_release_objects",
    "krw_ontology_mcp_store_rotation_pending",
    "krw_ontology_mcp_store_retired_leased",
    "krw_ontology_mcp_store_retired_oldest_age_seconds",
    "krw_ontology_mcp_store_rotations_total",
}
ALERTMANAGER_RECEIVER_ENV_VARS = {
    "default": "KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL",
    "critical": "KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL",
    "warning": "KRW_ALERTMANAGER_WARNING_WEBHOOK_URL",
}
ALERTMANAGER_RECEIVER_NAMES = {
    "default": "krw-ontology-mcp-default",
    "critical": "krw-ontology-mcp-critical",
    "warning": "krw-ontology-mcp-warning",
}


def validate_observability_configs(
    *,
    prometheus_alerts_path: Path | str,
    alertmanager_path: Path | str,
    env_name: str = "prod",
    allow_local_receivers: bool = False,
) -> dict[str, Any]:
    """Validate rendered observability configs before deployment."""
    prometheus_path = Path(prometheus_alerts_path).expanduser()
    alertmanager_config_path = Path(alertmanager_path).expanduser()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        prometheus_payload = _load_yaml_mapping(prometheus_path, kind="Prometheus alert rules")
        prometheus_summary = _validate_prometheus_alerts_payload(prometheus_payload)
    except (FileNotFoundError, ValueError) as exc:
        prometheus_summary = {"path": str(prometheus_path), "alert_count": 0, "metrics": []}
        errors.append(f"prometheus_alerts:{exc}")

    try:
        alertmanager_payload = _load_yaml_mapping(alertmanager_config_path, kind="Alertmanager")
        alertmanager_summary = _validate_alertmanager_payload(
            alertmanager_payload,
            env_name=env_name,
            allow_local_receivers=allow_local_receivers,
        )
    except (FileNotFoundError, ValueError) as exc:
        alertmanager_summary = {"path": str(alertmanager_config_path), "receiver_count": 0, "receivers": []}
        errors.append(f"alertmanager:{exc}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "env": env_name,
        "prometheus_alerts": {
            **prometheus_summary,
            "path": str(prometheus_path.resolve()),
            "sha256": _file_sha256(prometheus_path),
        },
        "alertmanager": {
            **alertmanager_summary,
            "path": str(alertmanager_config_path.resolve()),
            "sha256": _file_sha256(alertmanager_config_path),
        },
    }


def write_observability_doctor_report(
    path: Path | str,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Write an audit report for observability config validation."""
    output = Path(path).expanduser()
    comparable = {
        "ok": bool(validation.get("ok")),
        "errors": list(validation.get("errors") or []),
        "warnings": list(validation.get("warnings") or []),
        "env": validation.get("env"),
        "prometheus_alerts": validation.get("prometheus_alerts") or {},
        "alertmanager": validation.get("alertmanager") or {},
    }
    audit_hash = hashlib.sha256(
        json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = {
        "format": OBSERVABILITY_DOCTOR_REPORT_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_hash": audit_hash,
        **comparable,
    }
    _write_json_atomic(output, report)
    return {**report, "path": str(output.resolve())}


def render_prometheus_alerts_config(
    template_path: Path | str,
    output_path: Path | str,
    *,
    overrides: Mapping[str, str | int | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Render Prometheus alert rules with deployment-specific thresholds."""
    template = Path(template_path).expanduser()
    output = Path(output_path).expanduser()
    environment = env if env is not None else os.environ
    explicit_overrides = overrides or {}
    payload = _load_yaml_mapping(template, kind="Prometheus alert rules")
    rules = _prometheus_alert_rule_map(payload)
    values, sources = _prometheus_alert_thresholds(
        explicit_overrides,
        env=environment,
    )

    rules["KRWOntologyMCPDown"]["for"] = values["mcp_down_for"]
    rules["KRWOntologyMCPGlobalSpineMissing"]["for"] = values["global_spine_missing_for"]
    rules["KRWOntologyMCPHotSwapStuck"]["for"] = values["hot_swap_stuck_for"]
    rules["KRWOntologyMCPHotSwapStuck"]["expr"] = (
        "krw_ontology_mcp_store_rotation_pending == 1 "
        "and on(env, release_id) "
        "krw_ontology_mcp_store_retired_oldest_age_seconds "
        f"> {values['hot_swap_retired_age_seconds']}"
    )
    rules["KRWOntologyMCPHotSwapStuck"]["annotations"]["description"] = (
        "A retired store lease for env={{ $labels.env }} "
        "release={{ $labels.release_id }} has been pending for more than "
        f"{_seconds_description(int(values['hot_swap_retired_age_seconds']))}."
    )
    rules["KRWOntologyMCPRetiredLeasesPresent"]["for"] = values["retired_leases_for"]
    rules["KRWOntologyMCPExcessiveRotations"]["for"] = values["excessive_rotations_for"]
    rules["KRWOntologyMCPExcessiveRotations"]["expr"] = (
        "increase("
        f"krw_ontology_mcp_store_rotations_total[{values['rotation_window']}]"
        f") > {values['rotation_count']}"
    )
    rules["KRWOntologyMCPExcessiveRotations"]["annotations"]["description"] = (
        "MCP store rotations exceeded "
        f"{values['rotation_count']} events over {values['rotation_window']} "
        "for env={{ $labels.env }} release={{ $labels.release_id }}."
    )
    rules["KRWOntologyMCPProdReleaseEmpty"]["for"] = values["prod_empty_for"]

    _write_yaml_atomic(output, payload)
    return {
        "path": str(output.resolve()),
        "template_path": str(template.resolve()),
        "sha256": _file_sha256(output),
        "threshold_sources": sources,
        "thresholds": values,
    }


def render_alertmanager_config(
    template_path: Path | str,
    output_path: Path | str,
    *,
    receiver_urls: Mapping[str, str | None] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Render Alertmanager config with deployment-specific receiver URLs."""
    template = Path(template_path).expanduser()
    output = Path(output_path).expanduser()
    environment = env if env is not None else os.environ
    explicit_urls = receiver_urls or {}
    payload = _load_alertmanager_template(template)
    receivers = _receiver_map(payload)
    sources: dict[str, str] = {}
    missing: list[str] = []
    invalid: list[str] = []

    for kind, receiver_name in ALERTMANAGER_RECEIVER_NAMES.items():
        env_name = ALERTMANAGER_RECEIVER_ENV_VARS[kind]
        raw_url = explicit_urls.get(kind)
        source = "argument"
        if raw_url is None or str(raw_url).strip() == "":
            raw_url = environment.get(env_name)
            source = env_name
        url = str(raw_url or "").strip()
        if not url:
            missing.append(f"{kind}:{env_name}")
            continue
        if not _valid_webhook_url(url):
            invalid.append(f"{kind}:{source}")
            continue
        receiver = receivers[receiver_name]
        webhook_configs = receiver.get("webhook_configs")
        webhook_configs[0]["url"] = url
        sources[kind] = source

    if missing or invalid:
        parts: list[str] = []
        if missing:
            parts.append(f"missing receiver URLs: {', '.join(missing)}")
        if invalid:
            parts.append(f"invalid receiver URLs: {', '.join(invalid)}")
        raise ValueError("; ".join(parts))

    _write_yaml_atomic(output, payload)
    return {
        "path": str(output.resolve()),
        "template_path": str(template.resolve()),
        "sha256": _file_sha256(output),
        "receiver_sources": sources,
        "receiver_names": dict(ALERTMANAGER_RECEIVER_NAMES),
    }


def _validate_prometheus_alerts_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rules = _prometheus_alert_rule_map(payload)
    errors: list[str] = []
    metric_hits: set[str] = set()
    for alert_name, rule in rules.items():
        labels = rule.get("labels")
        if not isinstance(labels, dict) or labels.get("service") != "krw-ontology-mcp":
            errors.append(f"{alert_name}:service_label_missing")
        severity = labels.get("severity") if isinstance(labels, dict) else None
        if severity not in {"critical", "warning"}:
            errors.append(f"{alert_name}:severity_invalid")
        duration = rule.get("for")
        try:
            _parse_prometheus_duration(f"{alert_name}.for", str(duration or ""))
        except ValueError:
            errors.append(f"{alert_name}:for_duration_invalid")
        expr = str(rule.get("expr") or "")
        for metric in PROMETHEUS_REQUIRED_METRICS:
            if metric in expr:
                metric_hits.add(metric)
    missing_metrics = sorted(PROMETHEUS_REQUIRED_METRICS - metric_hits)
    errors.extend(f"metric_missing:{metric}" for metric in missing_metrics)
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "alert_count": len(rules),
        "alerts": sorted(rules),
        "metrics": sorted(metric_hits),
    }


def _validate_alertmanager_payload(
    payload: dict[str, Any],
    *,
    env_name: str,
    allow_local_receivers: bool,
) -> dict[str, Any]:
    route = payload.get("route")
    if not isinstance(route, dict):
        raise ValueError("Alertmanager config must contain route mapping")
    receivers = _receiver_map(payload)
    errors: list[str] = []
    receiver_urls: dict[str, str] = {}
    for kind, receiver_name in ALERTMANAGER_RECEIVER_NAMES.items():
        receiver = receivers[receiver_name]
        webhook_configs = receiver.get("webhook_configs")
        webhook = webhook_configs[0]
        url = str(webhook.get("url") or "").strip()
        if not _valid_webhook_url(url):
            errors.append(f"{receiver_name}:webhook_url_invalid")
            continue
        if env_name == "prod" and not allow_local_receivers and _is_local_webhook_url(url):
            errors.append(f"{receiver_name}:webhook_url_localhost")
        if not webhook.get("send_resolved"):
            errors.append(f"{receiver_name}:send_resolved_disabled")
        receiver_urls[kind] = url
    route_receivers = {str(child.get("receiver")) for child in route.get("routes") or [] if isinstance(child, dict)}
    expected_route_receivers = {
        ALERTMANAGER_RECEIVER_NAMES["critical"],
        ALERTMANAGER_RECEIVER_NAMES["warning"],
    }
    if not expected_route_receivers <= route_receivers:
        errors.append("route:critical_warning_routes_missing")
    inhibit_rules = payload.get("inhibit_rules")
    if not isinstance(inhibit_rules, list) or not inhibit_rules:
        errors.append("inhibit_rules:missing")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "receiver_count": len(receivers),
        "receivers": sorted(receivers),
        "receiver_kinds": sorted(receiver_urls),
        "allow_local_receivers": allow_local_receivers,
    }


def _is_local_webhook_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _prometheus_alert_thresholds(
    explicit_overrides: Mapping[str, str | int | None],
    *,
    env: Mapping[str, str],
) -> tuple[dict[str, str | int], dict[str, str]]:
    defaults: dict[str, str | int] = {
        "mcp_down_for": "2m",
        "global_spine_missing_for": "1m",
        "hot_swap_stuck_for": "5m",
        "hot_swap_retired_age_seconds": 300,
        "retired_leases_for": "15m",
        "rotation_window": "15m",
        "rotation_count": 3,
        "excessive_rotations_for": "5m",
        "prod_empty_for": "5m",
    }
    values: dict[str, str | int] = {}
    sources: dict[str, str] = {}
    for key, default in defaults.items():
        env_name = PROMETHEUS_ALERT_ENV_VARS[key]
        raw_value = explicit_overrides.get(key)
        source = "argument"
        if raw_value is None or str(raw_value).strip() == "":
            raw_value = env.get(env_name)
            source = env_name
        if raw_value is None or str(raw_value).strip() == "":
            raw_value = default
            source = "default"
        if isinstance(default, int):
            value = _parse_positive_int(key, raw_value)
        else:
            value = _parse_prometheus_duration(key, raw_value)
        values[key] = value
        sources[key] = source
    return values, sources


def _prometheus_alert_rule_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("Prometheus alert rules must contain groups list")
    rules: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            alert_name = rule.get("alert")
            if isinstance(alert_name, str) and alert_name:
                if alert_name in rules:
                    raise ValueError(f"duplicate Prometheus alert rule: {alert_name}")
                rules[alert_name] = rule
    required = {
        "KRWOntologyMCPDown",
        "KRWOntologyMCPGlobalSpineMissing",
        "KRWOntologyMCPHotSwapStuck",
        "KRWOntologyMCPRetiredLeasesPresent",
        "KRWOntologyMCPExcessiveRotations",
        "KRWOntologyMCPProdReleaseEmpty",
    }
    missing = sorted(required - set(rules))
    if missing:
        raise ValueError(f"missing Prometheus alert rules: {', '.join(missing)}")
    for alert_name in required:
        annotations = rules[alert_name].setdefault("annotations", {})
        if not isinstance(annotations, dict):
            raise ValueError(f"Prometheus alert rule {alert_name} annotations must be a mapping")
    return rules


def _parse_positive_int(key: str, value: str | int) -> int:
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


def _parse_prometheus_duration(key: str, value: str | int) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"(?:[1-9][0-9]*)(?:ms|s|m|h|d|w|y)", text):
        raise ValueError(f"{key} must be a Prometheus duration such as 30s, 5m, or 1h")
    return text


def _seconds_description(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} second" if seconds == 1 else f"{seconds} seconds"


def _load_yaml_mapping(path: Path, *, kind: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{kind} template not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{kind} template must be a YAML mapping: {path}")
    return payload


def _file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_alertmanager_template(path: Path) -> dict[str, Any]:
    return _load_yaml_mapping(path, kind="Alertmanager")


def _receiver_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_receivers = payload.get("receivers")
    if not isinstance(raw_receivers, list):
        raise ValueError("Alertmanager template must contain receivers list")

    receivers: dict[str, dict[str, Any]] = {}
    for item in raw_receivers:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in receivers:
            raise ValueError(f"duplicate Alertmanager receiver: {name}")
        receivers[name] = item

    missing_receivers = [
        name for name in ALERTMANAGER_RECEIVER_NAMES.values() if name not in receivers
    ]
    if missing_receivers:
        raise ValueError(f"missing Alertmanager receivers: {', '.join(missing_receivers)}")

    for receiver_name in ALERTMANAGER_RECEIVER_NAMES.values():
        webhook_configs = receivers[receiver_name].get("webhook_configs")
        if not isinstance(webhook_configs, list) or not webhook_configs:
            raise ValueError(f"receiver {receiver_name} must contain webhook_configs")
        if not isinstance(webhook_configs[0], dict):
            raise ValueError(f"receiver {receiver_name} first webhook_config must be a mapping")
    return receivers


def _valid_webhook_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
