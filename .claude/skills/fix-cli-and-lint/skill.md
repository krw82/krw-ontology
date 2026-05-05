---
name: fix-cli-and-lint
description: "CLI stub 명령어(validate, build-report) 실제 구현 연결, Edge ID 포맷 P2 수정, ruff lint 31건 에러 수정. CLI 수정, 린트 수정, 코드 정리 관련 작업 시 반드시 이 스킬을 사용할 것."
---

# Fix CLI and Lint — Skill

CLI 명령어 구현, Edge ID 포맷, ruff lint 에러를 수정한다.

## Task 1: CLI validate 명령어 구현

**파일:** `src/krw_ontology/cli/main.py`

**현재 상태:** `validate` 명령어가 `"validate not yet implemented for {ticker}"`만 출력.

**수정 방법:**

```python
@app.command("validate")
def validate_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type"),
    period: Optional[str] = typer.Option(None, "--period", help="Filing period"),
) -> None:
    """Re-run validation on existing ontology artifacts."""
    validate_document_type(document_type)
    from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
    from krw_ontology.pipeline.stages.validate_ontology import run_validate_ontology

    ticker = ticker.upper()
    doc_type_key = DOCUMENT_TYPE_KEY

    # Auto-detect latest period if not specified
    if period is None:
        ontology_base = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted([d.name for d in ontology_base.iterdir() if d.is_dir()])
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key / period
    if not ontology_dir.exists():
        typer.echo(f"Ontology directory not found: {ontology_dir}")
        raise typer.Exit(1)

    result = run_validate_ontology(ontology_dir)
    stats = result.get("stats", {})
    typer.echo(f"Validation complete: {stats.get('total_accepted', 0)} accepted, {stats.get('total_rejected', 0)} rejected")
```

## Task 2: CLI build-report 명령어 구현

**파일:** `src/krw_ontology/cli/main.py`

**수정 방법:**

```python
@app.command("build-report")
def build_report_cmd(
    ticker: str = typer.Argument(..., help="Stock ticker symbol"),
    document_type: str = typer.Option("10-K", "--document-type", help="Document type"),
    period: Optional[str] = typer.Option(None, "--period", help="Filing period"),
) -> None:
    """Regenerate graph_report.md and audit_report.md from existing artifacts."""
    validate_document_type(document_type)
    from krw_ontology.config.constants import DOCUMENT_TYPE_KEY
    from krw_ontology.pipeline.stages.build_indexes import build_indexes
    from krw_ontology.utils.io import read_jsonl

    ticker = ticker.upper()
    doc_type_key = DOCUMENT_TYPE_KEY

    # Auto-detect latest period if not specified
    if period is None:
        ontology_base = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key
        if not ontology_base.exists():
            typer.echo(f"No ontology data found for {ticker}")
            raise typer.Exit(1)
        periods = sorted([d.name for d in ontology_base.iterdir() if d.is_dir()])
        if not periods:
            typer.echo(f"No periods found for {ticker}")
            raise typer.Exit(1)
        period = periods[-1]
        typer.echo(f"Using latest period: {period}")

    ontology_dir = Path.cwd() / "companies" / ticker / "ontology" / doc_type_key / period
    sources_dir = Path.cwd() / "companies" / ticker / "sources" / doc_type_key / period
    if not ontology_dir.exists():
        typer.echo(f"Ontology directory not found: {ontology_dir}")
        raise typer.Exit(1)

    # Rebuild indexes
    build_indexes(
        ticker=ticker,
        period=period,
        doc_type_key=doc_type_key,
        ontology_dir=ontology_dir,
        sources_dir=sources_dir,
        output_dir=Path.cwd(),
    )

    # Generate graph report
    from datetime import datetime, timezone
    report_path = ontology_dir / "graph_report.md"
    audit_path = ontology_dir / "audit_report.md"

    # Count objects
    counts = {}
    for key, filename in [("spans", "spans.jsonl"), ("quotes", "evidence_quotes.jsonl"),
                           ("claims", "claims.jsonl"), ("risks", "risks.jsonl"),
                           ("drivers", "growth_drivers.jsonl"), ("headwinds", "headwinds.jsonl"),
                           ("assumptions", "assumption_candidates.jsonl"), ("edges", "edges.jsonl")]:
        fp = ontology_dir / filename
        counts[key] = len(read_jsonl(fp)) if fp.exists() else 0

    report_path.write_text(
        f"# Evidence Ontology Graph Report\n\n"
        f"**Ticker:** {ticker}\n"
        f"**Period:** {period}\n"
        f"**Document Type:** {document_type}\n"
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n\n"
        f"## Object Counts\n\n"
        f"| Type | Count |\n|------|-------|\n"
        + "\n".join(f"| {k} | {v} |" for k, v in counts.items())
        + "\n"
    )
    audit_path.write_text(
        f"# Audit Report\n\n"
        f"**Ticker:** {ticker}\n"
        f"**Period:** {period}\n"
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Run `krw-ontology validate {ticker}` for detailed validation results.\n"
    )
    typer.echo(f"Report generated at {ontology_dir}")
```

`main.py` 최상단 import에서 `Optional` 제거 필요 없음 (이미 사용 중). 추가로 필요한 import는 함수 내부에서 수행.

## Task 3: Edge ID 포맷 P2 수정

**파일:** `ontology/schema/objects.yaml`

**문제:** Schema는 `edge:{relation_id}:{hash10}`를 요구하지만 구현은 `edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}` 생성.

**해결:** Schema YAML의 Edge ID 패턴을 구현에 맞게 업데이트. 구현이 더 구체적이고 다른 객체 타입(Span, Quote, Claim 등)도 모두 scoped ID를 사용 중.

`ontology/schema/objects.yaml`에서 Edge의 id 필드 설명을:
```yaml
id:
  type: string
  description: "Unique identifier, format: edge:{relation_id}:{hash10}"
```
에서:
```yaml
id:
  type: string
  description: "Unique scoped identifier, format: edge:{ticker}:{period}:{doc_type_key}:{relation_id}:{hash10}"
```
으로 변경.

## Task 4: ruff lint 에러 수정

**실행:** `cd ~/krw-ontology && uv run ruff check .`

출력되는 31건 에러를 전부 수정. 주요 패턴:
- Unused import → 제거
- Unused variable → `_` 접두사로 변경하거나 제거

ruff가 자동 수정 가능한 경우 `uv run ruff check --fix .` 사용 후 수동 검증.

## 완료 조건

- [ ] `krw-ontology validate AAPL` 명령어가 실제 validation 실행
- [ ] `krw-ontology build-report AAPL` 명령어가 실제 report 생성
- [ ] `ruff check .` 결과 0 에러
- [ ] Edge ID schema 설명이 구현과 일치
