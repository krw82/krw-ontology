# KRW Ontology

10-K Evidence Ontology Workspace — 파일 기반 증거 중심 주식 리서치 파이프라인.

## 프로젝트 구조

```
krw-ontology/
├── src/krw_ontology/
│   ├── cli/                  # Typer CLI (4 commands)
│   ├── config/               # PipelineConfig, constants
│   ├── schema/               # Pydantic models, ID generation
│   ├── pipeline/             # Orchestrator, 15 stages, checkpoint
│   │   └── stages/           # Individual stage modules
│   ├── extraction/           # AI extraction worker, prompts, schemas
│   │   └── prompts/          # Prompt templates for Claude
│   ├── validators/           # 7 deterministic validators
│   └── utils/                # JSONL I/O, logging
├── ontology/schema/          # 7 YAML schema files
├── tests/                    # Unit + integration tests
├── dev_spec_v1.md            # Implementation specification
└── pyproject.toml
```

## CLI Commands

```bash
krw-ontology init-workspace                           # Create workspace with YAML schemas
krw-ontology build-evidence-ontology --ticker AAPL --document-type 10-K --period FY2024
krw-ontology validate --ontology-dir ./workspace
krw-ontology build-report --company-dir ./workspace/companies/AAPL
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/unit/ -v
pytest tests/ -v
```

## Architecture

- **File-canonical**: MD + JSONL + YAML as source of truth (no database)
- **EvidenceQuote-centered**: All interpretations trace back to exact source text
- **AI extracts, code validates**: Claude Agent SDK for extraction, deterministic Python validators
- **15-stage pipeline** with checkpoint/resume
- **12 object types**, **7 validators**, **25 canonical metrics**
