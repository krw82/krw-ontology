"""Typer CLI surface for the standalone guru ontology pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from krw_ontology.guru.background import (
    guru_background_status,
    run_background_worker,
    start_background_guru_run,
)
from krw_ontology.guru.curation import curate_guru_candidates
from krw_ontology.guru.eval_quality import (
    run_guru_answer_eval,
    run_guru_answer_eval_batch,
    run_guru_quality_eval,
)
from krw_ontology.guru.extractor import DEFAULT_AGENT_SDK_CONCURRENCY, extract_guru_ontology
from krw_ontology.guru.fetcher import fetch_guru_sources
from krw_ontology.guru.lens_selector import select_guru_lenses
from krw_ontology.guru.parser import parse_guru_sources
from krw_ontology.guru.pipeline import run_guru_pipeline
from krw_ontology.guru.planner import build_collection_plan
from krw_ontology.guru.sources import parse_author_keys
from krw_ontology.guru.verifier import verify_guru_workspace
from krw_ontology.guru.workspace import (
    guru_root,
    guru_running_root,
    guru_workspace_status,
    initialize_guru_workspace,
)


guru_app = typer.Typer(
    name="guru",
    help=(
        "Plan standalone investor-letter ontology collection. This does not extend "
        "the company filing ontology and does not register MCP tools."
    ),
    no_args_is_help=True,
)


@guru_app.command(
    "run",
    epilog=(
        "Examples:\n"
        "  krw-ontology guru run\n"
        "  krw-ontology guru run --background\n"
        "  krw-ontology guru run --authors buffett,marks --limit-per-author 3\n"
        "  krw-ontology guru run --max-batches 2\n"
        "  krw-ontology guru run --execute-agent-sdk\n\n"
        "By default this fetches/parses sources and prepares extraction batches without "
        "calling Claude Agent SDK. Use --background for a detached run."
    ),
)
def guru_run(
    root: Path | None = typer.Option(
        None,
        "--root",
        help="Long-lived guru data root. Defaults to ~/krw-ontology-guru.",
    ),
    running_root: Path | None = typer.Option(
        None,
        "--running-root",
        help="Mutable raw/parsed/Agent workspace. Defaults to ~/krw-ontology-guru-running.",
    ),
    authors: str | None = typer.Option(
        None,
        "--authors",
        help="Comma-separated author keys. Defaults to buffett,marks,ackman,flatt,terry_smith.",
    ),
    limit_per_author: int | None = typer.Option(
        None,
        "--limit-per-author",
        min=1,
        help="Optional cap for discovered documents fetched per author.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite planning manifests and rerun fetch/parse outputs.",
    ),
    discover: bool = typer.Option(
        True,
        "--discover/--no-discover",
        help="Discover PDF/HTML document links from official index pages.",
    ),
    execute_agent_sdk: bool = typer.Option(
        False,
        "--execute-agent-sdk",
        help="Actually call Claude Agent SDK. Omitted by default for a dry-run batch plan.",
    ),
    model: str | None = typer.Option(None, "--model", help="Claude model override for execution."),
    max_batches: int | None = typer.Option(
        None,
        "--max-batches",
        min=1,
        help="Optional cap for extraction batches.",
    ),
    concurrency: int = typer.Option(
        DEFAULT_AGENT_SDK_CONCURRENCY,
        "--concurrency",
        min=1,
        help="Maximum concurrent Claude Agent SDK extraction calls.",
    ),
    max_span_chars: int = typer.Option(
        2400,
        "--max-span-chars",
        min=200,
        help="Maximum character size for private Agent input spans.",
    ),
    background: bool = typer.Option(
        False,
        "--background",
        help="Launch the full guru run in the background and return pid/log paths.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Run the standalone guru pipeline in the foreground."""
    if background:
        try:
            payload = start_background_guru_run(
                root,
                running_root=running_root,
                authors=authors,
                limit_per_author=limit_per_author,
                force=force,
                discover=discover,
                execute_agent_sdk=execute_agent_sdk,
                model=model,
                max_batches=max_batches,
                concurrency=concurrency,
                max_span_chars=max_span_chars,
            )
        except RuntimeError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from exc
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return
        typer.echo(f"Started guru background run pid={payload['pid']}")
        typer.echo(f"root: {payload['root']}")
        typer.echo(f"running_root: {payload['running_root']}")
        typer.echo(f"log: {payload['log_path']}")
        typer.echo(f"state: {payload['state_path']}")
        return

    payload = run_guru_pipeline(
        root,
        running_root=running_root,
        author_keys=parse_author_keys(authors),
        force=force,
        limit_per_author=limit_per_author,
        discover=discover,
        execute_agent_sdk=execute_agent_sdk,
        model=model,
        max_batches=max_batches,
        concurrency=concurrency,
        max_span_chars=max_span_chars,
    )
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru pipeline complete")
    typer.echo(f"root: {payload['root']}")
    typer.echo(f"running_root: {payload['running_root']}")
    typer.echo(f"initialized: {str(payload['initialized']).lower()}")
    typer.echo(f"raw_documents: {payload['raw_documents']}")
    typer.echo(f"parsed_documents: {payload['parsed_documents']}")
    typer.echo(f"extraction_batches: {payload['extraction_batches']}")
    typer.echo(f"execution_mode: {payload['execution_mode']}")
    typer.echo(f"agent_sdk_called: {str(payload['agent_sdk_called']).lower()}")
    if execute_agent_sdk:
        typer.echo(f"concurrency: {concurrency}")


@guru_app.command("run-worker", hidden=True)
def guru_run_worker(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    authors: str | None = typer.Option(None, "--authors", help="Comma-separated author keys."),
    limit_per_author: int | None = typer.Option(None, "--limit-per-author", min=1),
    force: bool = typer.Option(False, "--force"),
    discover: bool = typer.Option(True, "--discover/--no-discover"),
    execute_agent_sdk: bool = typer.Option(False, "--execute-agent-sdk"),
    model: str | None = typer.Option(None, "--model"),
    max_batches: int | None = typer.Option(None, "--max-batches", min=1),
    concurrency: int = typer.Option(
        DEFAULT_AGENT_SDK_CONCURRENCY,
        "--concurrency",
        min=1,
    ),
    max_span_chars: int = typer.Option(2400, "--max-span-chars", min=200),
) -> None:
    """Hidden worker command for detached guru runs."""
    payload = run_background_worker(
        root,
        running_root=running_root,
        author_keys=parse_author_keys(authors),
        force=force,
        limit_per_author=limit_per_author,
        discover=discover,
        execute_agent_sdk=execute_agent_sdk,
        model=model,
        max_batches=max_batches,
        concurrency=concurrency,
        max_span_chars=max_span_chars,
    )
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@guru_app.command("start")
def guru_start(
    root: Path | None = typer.Option(
        None,
        "--root",
        help="Long-lived guru data root. Defaults to ~/krw-ontology-guru.",
    ),
    running_root: Path | None = typer.Option(
        None,
        "--running-root",
        help="Mutable raw/parsed/Agent workspace. Defaults to ~/krw-ontology-guru-running.",
    ),
    authors: str | None = typer.Option(
        None,
        "--authors",
        help="Comma-separated author keys. Defaults to buffett,marks,ackman,flatt,terry_smith.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the collection plan without writing workspace files.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing planning manifests.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print machine-readable JSON.",
    ),
) -> None:
    """Create a planned guru ontology workspace without collecting documents."""
    root_path = guru_root(root)
    running_path = guru_running_root(running_root)
    author_keys = parse_author_keys(authors)
    if dry_run:
        plan = build_collection_plan(root_path, author_keys, running_root=running_path)
        payload = {
            "dry_run": True,
            "collection_started": False,
            "extraction_started": False,
            "plan": plan.model_dump(mode="json"),
        }
    else:
        payload = {
            "dry_run": False,
            **initialize_guru_workspace(
                root_path,
                running_root=running_path,
                author_keys=author_keys,
                force=force,
            ),
        }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru ontology collection plan created")
    typer.echo(f"root: {root_path}")
    typer.echo(f"running_root: {running_path}")
    typer.echo(f"authors: {', '.join(payload.get('author_keys') or author_keys)}")
    typer.echo("collection_started: false")
    typer.echo("extraction_started: false")
    if dry_run:
        typer.echo("dry_run: true")
    else:
        typer.echo(f"next: krw-ontology guru fetch --root {root_path} --running-root {running_path}")


@guru_app.command("plan")
def guru_plan(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    authors: str | None = typer.Option(None, "--authors", help="Comma-separated author keys."),
) -> None:
    """Print the planned guru ontology DAG without writing files."""
    plan = build_collection_plan(
        guru_root(root),
        parse_author_keys(authors),
        running_root=guru_running_root(running_root),
    )
    typer.echo(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))


@guru_app.command("verify")
def guru_verify(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
) -> None:
    """Verify planned guru ontology artifacts."""
    result = verify_guru_workspace(
        root,
        running_root=running_root,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["ok"]:
        raise typer.Exit(1)


@guru_app.command("status")
def guru_status(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
) -> None:
    """Show whether a guru planning workspace exists."""
    payload = guru_workspace_status(root, running_root=running_root)
    payload["background"] = guru_background_status(running_root)
    typer.echo(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


@guru_app.command("select-lenses")
def guru_select_lenses(
    question: str = typer.Option(
        ...,
        "--question",
        "-q",
        help="Investor consultation question to route through the guru ontology.",
    ),
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    authors: str | None = typer.Option(
        None,
        "--authors",
        help="Optional comma-separated author keys. Omit to infer from the question.",
    ),
    ticker: str | None = typer.Option(
        None,
        "--ticker",
        help="Optional company ticker when the question is company-specific.",
    ),
    intent_family: str | None = typer.Option(
        None,
        "--intent-family",
        help="Optional explicit guru consultation intent family.",
    ),
    limit: int = typer.Option(
        5,
        "--limit",
        min=1,
        help="Maximum number of guru lens objects to return.",
    ),
    data_need_limit: int = typer.Option(
        6,
        "--data-need-limit",
        min=1,
        help="Maximum number of related data-need objects to return.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Select relevant guru ontology lenses for one investor question."""
    payload = select_guru_lenses(
        question=question,
        root=root,
        author_keys=parse_author_keys(authors) if authors else None,
        ticker=ticker,
        intent_family=intent_family,
        limit=limit,
        data_need_limit=data_need_limit,
    )
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"selection_status: {payload['selection_status']}")
    typer.echo(f"intent_family: {payload.get('intent_family')}")
    typer.echo(f"requires_company_evidence: {str(payload['requires_company_evidence']).lower()}")
    typer.echo(f"authors: {', '.join(payload['selected_author_keys'])}")
    typer.echo(f"lenses: {payload['count']}")
    for lens in payload["selected_lenses"]:
        typer.echo(f"- {lens.get('label_ko')} ({lens.get('author_name')})")
    bridge = payload["company_bridge"]
    if bridge.get("filing_evidence_requirements"):
        typer.echo(
            "filing_evidence_requirements: "
            + ", ".join(bridge["filing_evidence_requirements"])
        )


@guru_app.command("fetch")
def guru_fetch(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    limit_per_author: int | None = typer.Option(
        None,
        "--limit-per-author",
        min=1,
        help="Optional cap for discovered documents fetched per author.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-download existing raw files."),
    discover: bool = typer.Option(
        True,
        "--discover/--no-discover",
        help="Discover PDF/HTML document links from official index pages.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Fetch official indexes and discovered PDF/HTML source documents."""
    manifest = fetch_guru_sources(
        root,
        running_root=running_root,
        limit_per_author=limit_per_author,
        overwrite=overwrite,
        discover=discover,
    )
    payload = manifest.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    errors = [item for item in manifest.raw_documents if item.status == "error"]
    typer.echo(f"fetched_raw_documents: {len(manifest.raw_documents) - len(errors)}")
    typer.echo(f"errors: {len(errors)}")
    typer.echo(f"raw_manifest: {guru_running_root(running_root) / 'raw_manifest.json'}")


@guru_app.command("parse")
def guru_parse(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    source_ids: str | None = typer.Option(
        None,
        "--source-ids",
        help="Comma-separated source ids to parse. Defaults to every fetched source.",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Re-parse existing parsed files."),
    max_span_chars: int = typer.Option(
        2400,
        "--max-span-chars",
        min=200,
        help="Maximum character size for private Agent input spans.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Parse fetched raw PDF/HTML/text files into private spans."""
    selected_source_ids = _parse_csv_set(source_ids)
    manifest = parse_guru_sources(
        root,
        running_root=running_root,
        source_ids=selected_source_ids,
        overwrite=overwrite,
        max_span_chars=max_span_chars,
    )
    payload = manifest.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    parsed = [item for item in manifest.parsed_documents if item.status == "parsed"]
    errors = [item for item in manifest.parsed_documents if item.status == "error"]
    typer.echo(f"parsed_documents: {len(parsed)}")
    typer.echo(f"errors: {len(errors)}")
    typer.echo(f"parsed_manifest: {guru_running_root(running_root) / 'parsed_manifest.json'}")


@guru_app.command("extract")
def guru_extract(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    execute_agent_sdk: bool = typer.Option(
        False,
        "--execute-agent-sdk",
        help="Actually call Claude Agent SDK. Omitted by default for a dry-run batch plan.",
    ),
    model: str | None = typer.Option(None, "--model", help="Claude model override for execution."),
    max_batches: int | None = typer.Option(
        None,
        "--max-batches",
        min=1,
        help="Optional cap for extraction batches.",
    ),
    concurrency: int = typer.Option(
        DEFAULT_AGENT_SDK_CONCURRENCY,
        "--concurrency",
        min=1,
        help="Maximum concurrent Claude Agent SDK extraction calls.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Build Agent SDK extraction batches; dry-run unless explicitly executed."""
    manifest = extract_guru_ontology(
        root,
        running_root=running_root,
        execute_agent_sdk=execute_agent_sdk,
        model=model,
        max_batches=max_batches,
        concurrency=concurrency,
    )
    payload = manifest.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo(f"execution_mode: {manifest.execution_mode}")
    typer.echo(f"agent_sdk_called: {str(manifest.agent_sdk_called).lower()}")
    typer.echo(f"concurrency: {manifest.concurrency}")
    typer.echo(f"batches: {len(manifest.batches)}")
    typer.echo(f"extraction_manifest: {guru_running_root(running_root) / 'generated' / 'extraction_manifest.json'}")


@guru_app.command("curate")
def guru_curate(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
    candidates_path: Path | None = typer.Option(
        None,
        "--candidates-path",
        help="Raw ontology_candidates.jsonl path. Defaults to <running-root>/generated/ontology_candidates.jsonl.",
    ),
    reviewed_dir: Path | None = typer.Option(
        None,
        "--reviewed-dir",
        help="Reviewed output directory. Defaults to <root>/reviewed.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Curate raw guru extraction candidates into reviewed ontology artifacts."""
    report = curate_guru_candidates(
        root,
        running_root=running_root,
        candidates_path=candidates_path,
        reviewed_dir=reviewed_dir,
    )
    payload = report.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru curation complete")
    typer.echo(f"input_candidates: {report.input_candidates}")
    typer.echo(f"accepted_guru_objects: {report.accepted_guru_objects}")
    typer.echo(f"accepted_consultation_objects: {report.accepted_consultation_objects}")
    typer.echo(f"accepted_data_needs: {report.accepted_data_needs}")
    typer.echo(f"accepted_corpus_metadata: {report.accepted_corpus_metadata}")
    typer.echo(f"relationships: {report.relationships}")
    typer.echo(f"rejected_candidates: {report.rejected_candidates}")
    typer.echo(f"curation_report: {report.files['curation_report']}")


@guru_app.command("eval-quality")
def guru_eval_quality(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    eval_path: Path | None = typer.Option(
        None,
        "--eval-path",
        help="Gold eval JSONL path. Defaults to plugin references/gold-eval.jsonl.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output-path",
        help="Quality report JSON path. Defaults to <root>/reports/guru_eval_quality_report.json.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Optional cap for eval cases.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Evaluate guru ResearchPack retrieval quality against a gold question set."""
    report = run_guru_quality_eval(
        root,
        eval_path=eval_path,
        output_path=output_path,
        limit=limit,
    )
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru quality eval complete")
    typer.echo(f"cases: {report['cases']}")
    typer.echo(f"passed: {report['passed']}")
    typer.echo(f"failed: {report['failed']}")
    typer.echo(f"mean_score: {report['mean_score']}")
    typer.echo(f"quality_grade: {report['quality_grade']}")
    typer.echo(f"report: {report['output_path']}")


@guru_app.command("eval-answer")
def guru_eval_answer(
    question: str = typer.Option(
        ...,
        "--question",
        "-q",
        help="Investor question that produced the answer.",
    ),
    answer: str | None = typer.Option(
        None,
        "--answer",
        help="Inline generated answer text. Prefer --answer-file for long answers.",
    ),
    answer_file: Path | None = typer.Option(
        None,
        "--answer-file",
        help="Path to a generated answer Markdown/text file.",
    ),
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    research_payload_path: Path | None = typer.Option(
        None,
        "--research-payload",
        help="Optional krw_guru_query_context/select-lenses JSON payload used by the answer.",
    ),
    output_path: Path | None = typer.Option(
        None,
        "--output-path",
        help="Answer quality report JSON path. Defaults to <root>/reports/guru_answer_eval_report.json.",
    ),
    filing_evidence_provided: bool = typer.Option(
        False,
        "--filing-evidence-provided",
        help="Set when the answer was composed with KRW company filing evidence.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Evaluate one generated guru advisor answer against output-contract boundaries."""
    answer_text = answer
    if answer_file is not None:
        answer_text = answer_file.read_text(encoding="utf-8")
    if not answer_text:
        typer.echo("Provide --answer or --answer-file.")
        raise typer.Exit(1)
    report = run_guru_answer_eval(
        root,
        question=question,
        answer=answer_text,
        research_payload_path=research_payload_path,
        output_path=output_path,
        filing_evidence_provided=filing_evidence_provided,
    )
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru answer eval complete")
    typer.echo(f"passed: {str(report['passed']).lower()}")
    typer.echo(f"score_ratio: {report['score_ratio']}")
    typer.echo(f"failures: {len(report['failures'])}")
    typer.echo(f"report: {report['output_path']}")


@guru_app.command("eval-answer-batch")
def guru_eval_answer_batch(
    cases_path: Path = typer.Option(
        ...,
        "--cases-path",
        help="JSONL cases with question plus answer/answer_path and optional research payload.",
    ),
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    output_path: Path | None = typer.Option(
        None,
        "--output-path",
        help="Batch answer quality report JSON path. Defaults to <root>/reports/guru_answer_eval_batch_report.json.",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        min=1,
        help="Optional cap for answer eval cases.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Evaluate many generated guru advisor answers from a JSONL case file."""
    report = run_guru_answer_eval_batch(
        root,
        cases_path=cases_path,
        output_path=output_path,
        limit=limit,
    )
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    typer.echo("Guru answer batch eval complete")
    typer.echo(f"cases: {report['cases']}")
    typer.echo(f"passed: {report['passed']}")
    typer.echo(f"failed: {report['failed']}")
    typer.echo(f"mean_score: {report['mean_score']}")
    typer.echo(f"quality_grade: {report['quality_grade']}")
    typer.echo(f"report: {report['output_path']}")


@guru_app.command("statu", hidden=True)
def guru_statu_alias(
    root: Path | None = typer.Option(None, "--root", help="Long-lived guru data root."),
    running_root: Path | None = typer.Option(None, "--running-root", help="Mutable workspace root."),
) -> None:
    """Hidden compatibility alias for a common status typo."""
    guru_status(root=root, running_root=running_root)


def _parse_csv_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}
