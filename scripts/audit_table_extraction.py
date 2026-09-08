#!/usr/bin/env python3
"""Audit shard table-extraction metrics against SEC XBRL companyfacts.

``audit`` samples accepted ``MetricObservation`` rows from a built release's
company shards, cross-validates each value against the SEC ``companyfacts``
API (cache-first; cache lives outside the release tree), and writes a JSON
report plus a stdout verdict summary.  Pass ``--tickers 30`` for a seeded
30-ticker sample or ``--tickers NVDA,AAPL`` for an explicit list.

Shards carry no CIK, so cache misses need ``--cik-map`` (a JSON object
mapping TICKER -> SEC CIK, e.g. ``{"NVDA": 1045810}``); without it a
cache-miss ticker is skipped with reason ``no_cik``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from krw_ontology.eval_gold.table_audit import (
    DEFAULT_CACHE_DIR,
    DEFAULT_SEED,
    DEFAULT_USER_AGENT,
    USER_AGENT_ENVVAR,
    audit_shard_metrics,
)

app = typer.Typer(add_completion=False, help=__doc__)


def _resolve_release_root(release_root: Optional[Path]) -> Path:
    if release_root is None:
        typer.secho(
            "--release-root is required (or set KRW_ONTOLOGY_RELEASE_ROOT)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if not release_root.is_dir():
        typer.secho(f"release root not found: {release_root}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return release_root


def _parse_tickers(tickers: Optional[str]) -> list[str] | int | None:
    if tickers is None:
        return None
    value = tickers.strip()
    if value.isdigit():
        return int(value)
    return [part for part in (part.strip() for part in value.split(",")) if part]


def _load_cik_map(cik_map_path: Optional[Path]) -> dict[str, int | str]:
    if cik_map_path is None:
        return {}
    try:
        payload = json.loads(cik_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.secho(f"--cik-map could not be read: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(payload, dict):
        typer.secho(
            "--cik-map must be a JSON object {TICKER: cik}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    return payload


@app.command()
def audit(
    release_root: Optional[Path] = typer.Option(
        None,
        "--release-root",
        envvar="KRW_ONTOLOGY_RELEASE_ROOT",
        help="Root of a built release (must contain indexes/shard_manifest.json).",
    ),
    tickers: Optional[str] = typer.Option(
        None,
        "--tickers",
        help="Ticker count for a seeded sample (e.g. 30) or a comma-separated list.",
    ),
    per_ticker: int = typer.Option(
        5, "--per-ticker", min=1, help="Metric rows sampled per ticker."
    ),
    seed: int = typer.Option(
        DEFAULT_SEED, "--seed", help="Sampling seed; same seed -> same report."
    ),
    cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--cache-dir",
        envvar="SEC_COMPANYFACTS_CACHE",
        help="companyfacts cache directory (kept outside the release tree).",
    ),
    ua: Optional[str] = typer.Option(
        None,
        "--ua",
        envvar=USER_AGENT_ENVVAR,
        help=(
            "SEC identifying User-Agent (an identification string, not a "
            f"credential; defaults to {DEFAULT_USER_AGENT!r})."
        ),
    ),
    cik_map_path: Optional[Path] = typer.Option(
        None,
        "--cik-map",
        exists=True,
        dir_okay=False,
        help="JSON object {TICKER: SEC CIK} enabling companyfacts fetch on cache miss.",
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Output path for the audit JSON report."
    ),
) -> None:
    root = _resolve_release_root(release_root)
    try:
        report = audit_shard_metrics(
            root,
            tickers=_parse_tickers(tickers),
            seed=seed,
            per_ticker=per_ticker,
            cache_dir=cache_dir,
            ua=ua,
            cik_map=_load_cik_map(cik_map_path),
        )
    except (ValueError, FileNotFoundError) as exc:
        typer.secho(f"audit failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        typer.echo(f"audit report: {len(report['rows'])} rows -> {out}")
    summary = report["summary"]
    typer.echo(
        "verdicts: "
        + ", ".join(
            f"{key}={summary[key]}"
            for key in (
                "match",
                "tolerance",
                "mismatch",
                "missing_xbrl",
                "no_tag_map",
                "no_cik",
            )
        )
        + f" (tickers={summary['tickers']}, total={summary['total']})"
    )


if __name__ == "__main__":
    app()
