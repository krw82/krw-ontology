#!/usr/bin/env python3
"""Generate deterministic template evidence-gold from a built release.

``generate`` samples (ticker, metric, period) triples from the release's
company shards (``indexes/shard_manifest.json``) and writes an
``krw-ontology-evidence-gold/v1`` JSON file whose expected anchors are the
shard's own accepted metric objects.

``inspect-objects TICKER METRIC PERIOD`` prints the matching object rows from
that ticker's shard (id, period, section, review_status, first 80 chars of
text) as a curation helper for hand-written gold cases.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from krw_ontology.eval_gold.templates import (
    DEFAULT_SEED,
    generate_template_gold,
    inspect_objects,
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


@app.command()
def generate(
    release_root: Optional[Path] = typer.Option(
        None,
        "--release-root",
        envvar="KRW_ONTOLOGY_RELEASE_ROOT",
        help="Root of a built release (must contain indexes/shard_manifest.json).",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        help="Output path for the evidence-gold JSON file.",
    ),
    per_ticker: int = typer.Option(
        3,
        "--per-ticker",
        min=1,
        help="Maximum cases sampled per ticker.",
    ),
    seed: int = typer.Option(
        DEFAULT_SEED,
        "--seed",
        help="Random seed; the same seed reproduces the same gold byte-for-byte.",
    ),
    tickers: Optional[str] = typer.Option(
        None,
        "--tickers",
        help="Optional comma-separated ticker filter (e.g. NVDA,AAPL).",
    ),
) -> None:
    root = _resolve_release_root(release_root)
    ticker_list = (
        [value.strip() for value in tickers.split(",") if value.strip()]
        if tickers
        else None
    )
    try:
        gold = generate_template_gold(
            root, seed=seed, per_ticker=per_ticker, tickers=ticker_list
        )
    except (ValueError, FileNotFoundError) as exc:
        typer.secho(f"generation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(gold.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    typer.echo(
        f"template gold: {len(gold.cases)} cases "
        f"(release={gold.source_release['release_id']}, seed={seed}) -> {out}"
    )


@app.command("inspect-objects")
def inspect_objects_command(
    ticker: str = typer.Argument(..., help="Ticker, e.g. NVDA."),
    metric: str = typer.Argument(..., help="Canonical metric, e.g. revenue."),
    period: str = typer.Argument(..., help="Filing period, e.g. CY2024."),
    release_root: Optional[Path] = typer.Option(
        None,
        "--release-root",
        envvar="KRW_ONTOLOGY_RELEASE_ROOT",
        help="Root of a built release (must contain indexes/shard_manifest.json).",
    ),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum rows to print."),
) -> None:
    root = _resolve_release_root(release_root)
    try:
        rows = inspect_objects(root, ticker, metric, period, limit=limit)
    except (ValueError, FileNotFoundError) as exc:
        typer.secho(f"inspect failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not rows:
        typer.echo(f"no objects for ({ticker}, {metric}, {period})")
        raise typer.Exit(code=0)
    typer.echo(
        f"{'id':<64} {'period':<10} {'section':<12} {'status':<12} text"
    )
    for row in rows:
        typer.echo(
            f"{row['id']:<64} {row['period']:<10} {str(row['section'] or ''):<12} "
            f"{str(row['review_status'] or ''):<12} {row['text_preview']}"
        )


if __name__ == "__main__":
    app()
