#!/usr/bin/env python3
"""Run the evidence-gold harness headlessly and write gated JSON+MD reports.

Wraps ``krw_ontology.eval_gold.harness.run_harness`` (which executes every
gold case through the real ``query_context_tool`` path). Writes
``evidence_gold_<label>_<YYYYmmdd_HHMMSS>.json`` plus a ``.md`` summary into
``--report-dir``. When ``--baseline`` points at a previous report JSON, the
new report is gated with ``compare_to_baseline`` and the process exits with
code 2 when any metric regressed (and only then).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from krw_ontology.eval_gold.harness import (
    compare_to_baseline,
    render_report_markdown,
    run_harness,
)

app = typer.Typer(add_completion=False)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.secho(f"invalid JSON {path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(payload, dict):
        typer.secho(f"expected a JSON object in {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return payload


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.")
    return cleaned or "candidate"


@app.command()
def main(
    gold: Path = typer.Option(
        ...,
        "--gold",
        help="Path to the evidence-gold JSON file (format krw-ontology-evidence-gold/v1).",
    ),
    label: str = typer.Option(
        "candidate",
        "--label",
        help="Report label; used in the output filename.",
    ),
    baseline: Optional[Path] = typer.Option(
        None,
        "--baseline",
        help="Previous evidence-gold report JSON to gate against.",
    ),
    report_dir: Path = typer.Option(
        Path("benchmarks") / "reports",
        "--report-dir",
        help="Directory where the JSON and markdown reports are written.",
    ),
    tolerance: float = typer.Option(
        0.0,
        "--tolerance",
        min=0.0,
        help="Allowed metric drop below the baseline before it counts as regression.",
    ),
) -> None:
    if not gold.is_file():
        typer.secho(f"gold file not found: {gold}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    report = run_harness(gold, label=label)

    gated = False
    if baseline is not None:
        if not baseline.is_file():
            typer.secho(
                f"baseline report not found: {baseline}", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=1)
        baseline_payload = _read_json(baseline)
        ok, regressions = compare_to_baseline(
            report, baseline_payload, tolerance=tolerance
        )
        report["baseline_gate"] = {
            "baseline_path": str(baseline),
            "tolerance": tolerance,
            "ok": ok,
            "regressions": regressions,
        }
        gated = True

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"evidence_gold_{_safe_label(label)}_{stamp}"
    json_path = report_dir / f"{stem}.json"
    markdown_path = report_dir / f"{stem}.md"
    _atomic_write_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(markdown_path, render_report_markdown(report))

    overall = report["overall"]
    typer.echo(
        f"evidence-gold[{label}] cases={overall['cases']} "
        f"pass_rate={overall['pass_rate']:.4f} "
        f"mean_recall={overall['mean_recall']:.4f} "
        f"zero_hit_rate={overall['zero_hit_rate']:.4f}"
    )
    typer.echo(f"report: {json_path}")
    typer.echo(f"markdown: {markdown_path}")

    if gated and not report["baseline_gate"]["ok"]:
        for item in report["baseline_gate"]["regressions"]:
            typer.secho(f"regression: {item}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
