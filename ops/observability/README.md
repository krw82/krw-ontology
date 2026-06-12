# KRW Ontology MCP Observability

This directory contains deployment-ready monitoring assets for the MCP release
serving process.

## Metrics Endpoint

Configure Prometheus to scrape the MCP HTTP server:

```yaml
scrape_configs:
  - job_name: krw-ontology-mcp
    metrics_path: /metrics
    static_configs:
      - targets:
          - 127.0.0.1:8000
```

The endpoint exports release labels:

- `env`
- `release_id`

Core metrics:

- `krw_ontology_mcp_health_ok`
- `krw_ontology_mcp_global_spine_present`
- `krw_ontology_mcp_release_documents`
- `krw_ontology_mcp_release_objects`
- `krw_ontology_mcp_company_shards`
- `krw_ontology_mcp_global_topic_spine_rows`
- `krw_ontology_mcp_store_rotation_pending`
- `krw_ontology_mcp_store_retired_leased`
- `krw_ontology_mcp_store_retired_oldest_age_seconds`
- `krw_ontology_mcp_store_rotations_total`

## Alerts

Render `prometheus-krw-ontology-mcp-alerts.yml` with deployment-specific
thresholds, then load the rendered file into Prometheus or your rule manager.
The rules cover:

- MCP health failure
- missing global spine
- stuck hot-swap retired leases
- excessive store rotations
- empty production release

The default hot-swap stuck threshold is intentionally conservative: a retired
lease older than 5 minutes after rotation should be investigated. Override
thresholds through environment variables when staging and production need
different sensitivity:

```bash
export KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS=600
export KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR=10m
export KRW_PROMETHEUS_ROTATION_WINDOW=30m
export KRW_PROMETHEUS_ROTATION_COUNT=5

krw-ontology observability render-prometheus-alerts \
  --output /etc/prometheus/rules/krw-ontology-mcp-alerts.yml
```

Supported threshold variables:

- `KRW_PROMETHEUS_MCP_DOWN_FOR`
- `KRW_PROMETHEUS_GLOBAL_SPINE_MISSING_FOR`
- `KRW_PROMETHEUS_HOT_SWAP_STUCK_FOR`
- `KRW_PROMETHEUS_HOT_SWAP_RETIRED_AGE_SECONDS`
- `KRW_PROMETHEUS_RETIRED_LEASES_FOR`
- `KRW_PROMETHEUS_ROTATION_WINDOW`
- `KRW_PROMETHEUS_ROTATION_COUNT`
- `KRW_PROMETHEUS_EXCESSIVE_ROTATIONS_FOR`
- `KRW_PROMETHEUS_PROD_EMPTY_FOR`

## Deployment Check

Run the observability doctor before loading rendered configs:

```bash
krw-ontology observability doctor \
  --prometheus-alerts /etc/prometheus/rules/krw-ontology-mcp-alerts.yml \
  --alertmanager /etc/alertmanager/krw-ontology-mcp.yml \
  --env prod \
  --write-report /var/log/krw-ontology/observability-doctor.json
```

For `prod`, the doctor rejects localhost/loopback Alertmanager webhook URLs,
missing required alert rules, invalid Prometheus durations, missing required
metrics, disabled `send_resolved`, and missing critical/warning routing.
The JSON report records config paths, config SHA-256 hashes, counts, errors, and
a deterministic `audit_hash`; it does not include webhook URLs.

## Alert Routing

Render `alertmanager-krw-ontology-mcp.yml` with deployment-specific receiver
URLs, then load the rendered file into Alertmanager or merge the route and
receivers into your existing Alertmanager config.

The template:

- groups by `alertname`, `env`, `release_id`, and `service`
- routes `critical` alerts separately from `warning` alerts
- sends resolved notifications
- inhibits warning notifications for the same release while a critical MCP alert is firing

Production receiver URLs should come from the environment so secrets are not
committed to the repo or printed in CLI output:

```bash
export KRW_ALERTMANAGER_DEFAULT_WEBHOOK_URL="https://alerts.example/default"
export KRW_ALERTMANAGER_CRITICAL_WEBHOOK_URL="https://alerts.example/critical"
export KRW_ALERTMANAGER_WARNING_WEBHOOK_URL="https://alerts.example/warning"

krw-ontology observability render-alertmanager \
  --output /etc/alertmanager/krw-ontology-mcp.yml
```

The renderer fails if any receiver URL is missing or not an HTTP(S) URL. It
writes the output atomically and prints only the output path plus the source
environment variable names, not the webhook URLs.

## Dashboard

Import `grafana-krw-ontology-mcp-dashboard.json` into Grafana and bind the
`${DS_PROMETHEUS}` datasource variable to your Prometheus datasource.

The dashboard focuses on release serving safety:

- current release health
- document/object coverage
- shard/global topic coverage
- hot-swap pending state
- retired lease age
- store churn
