#!/usr/bin/env python3
"""Benchmark the real Claude Agent SDK + KRW plugin answer path safely.

Each case runs in its own process group. A hard timeout kills the complete
group so a stalled SDK, Claude CLI, or stdio MCP server cannot remain alive.
The benchmark never sets model, effort, max turns, or a replacement system
prompt. It selects Claude Code's normal system-prompt preset and loads an
ephemeral byte-for-byte plugin copy whose only changed file is ``.mcp.json``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


REPORT_FORMAT = "krw-ontology-agent-sdk-answer-benchmark/v1"
GOLD_FORMAT = "krw-ontology-agent-sdk-answer-gold/v1"
GOLD_CONTRACT = "raw-korean-user-question-to-plugin-grounded-answer"
WORKER_FORMAT = "krw-ontology-agent-sdk-answer-worker/v1"
QUALIFYING_TOOL_PREFIX = "krw_ontology_"
INTERNAL_LEAK_RE = re.compile(
    r"\b(?:MCP|ResearchState|SearchPlan|query_context|object_id|SQLite|FTS|"
    r"ontology|sidecar|router|EvidenceQuote|ResearchClaim)\b|온톨로지|객체\s*ID",
    re.IGNORECASE,
)
FOLLOW_UP_HEADING = "이어서 볼 질문"


class BenchmarkInputError(ValueError):
    """Raised when the benchmark contract or release binding is invalid."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkInputError(f"invalid JSON file {path}: {exc}") from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.parent / f".{resolved.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hashes(root: Path, *, exclude: set[str] | None = None) -> dict[str, str]:
    excluded = exclude or set()
    return {
        path.relative_to(root).as_posix(): _file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def _validate_gold(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise BenchmarkInputError("gold must be a JSON object")
    if payload.get("format") != GOLD_FORMAT or payload.get("contract") != GOLD_CONTRACT:
        raise BenchmarkInputError("gold format or contract mismatch")
    source = payload.get("source_release")
    if not isinstance(source, Mapping) or not source.get("source_manifest_hash"):
        raise BenchmarkInputError("gold source_release is incomplete")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise BenchmarkInputError("gold cases must be a non-empty array")
    seen: set[str] = set()
    normalized_cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, Mapping):
            raise BenchmarkInputError(f"gold case {index} must be an object")
        case_id = str(raw_case.get("id") or "").strip()
        question = str(raw_case.get("user_question_ko") or "").strip()
        tickers = raw_case.get("expected_tickers")
        aliases = raw_case.get("expected_company_aliases")
        if not case_id or case_id in seen or not question:
            raise BenchmarkInputError(f"gold case {index} has invalid id or question")
        if not isinstance(tickers, list) or not tickers:
            raise BenchmarkInputError(f"gold case {case_id} has no expected_tickers")
        if not isinstance(aliases, list) or not aliases:
            raise BenchmarkInputError(f"gold case {case_id} has no company aliases")
        seen.add(case_id)
        normalized_cases.append(
            {
                "id": case_id,
                "user_question_ko": question,
                "expected_tickers": [str(item).strip().upper() for item in tickers],
                "expected_company_aliases": [str(item).strip() for item in aliases],
                "review_focus": [str(item).strip() for item in raw_case.get("review_focus") or []],
            }
        )
    return {
        "format": GOLD_FORMAT,
        "contract": GOLD_CONTRACT,
        "source_release": dict(source),
        "cases": normalized_cases,
    }


def _release_binding(release_root: Path, gold: Mapping[str, Any]) -> dict[str, str]:
    manifest_path = release_root / "manifest.json"
    source_manifest_path = release_root / "source_manifest.json"
    manifest = _read_json(manifest_path)
    source_manifest = _read_json(source_manifest_path)
    if not isinstance(manifest, Mapping) or not isinstance(source_manifest, Mapping):
        raise BenchmarkInputError("release manifests must be JSON objects")
    release_id = str(manifest.get("release_id") or release_root.name)
    source_hash = str(
        manifest.get("source_manifest_hash")
        or source_manifest.get("manifest_hash")
        or source_manifest.get("source_manifest_hash")
        or ""
    )
    expected_hash = str(gold["source_release"]["source_manifest_hash"])
    if source_hash != expected_hash:
        raise BenchmarkInputError(
            f"gold source hash mismatch: release={source_hash!r} gold={expected_hash!r}"
        )
    return {
        "release_id": release_id,
        "source_manifest_hash": source_hash,
        "manifest_sha256": _file_sha256(manifest_path),
    }


def _provider_metadata() -> dict[str, str | None]:
    raw = os.getenv("ANTHROPIC_BASE_URL") or os.getenv("ANTHROPIC_API_URL") or ""
    parsed = urlparse(raw)
    return {
        "endpoint_host": parsed.hostname,
        "sdk_version": _sdk_version(),
    }


def _sdk_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("claude-agent-sdk")
    except Exception:  # pragma: no cover - metadata availability is environment-specific
        return None


def _ontology_tool_count() -> int:
    from krw_ontology.mcp_server.server import EXPECTED_TOOL_NAMES

    return len(EXPECTED_TOOL_NAMES)


def _prepare_plugin_copy(
    plugin_root: Path, release_root: Path, destination: Path
) -> dict[str, Any]:
    shutil.copytree(plugin_root, destination)
    mcp_path = destination / ".mcp.json"
    payload = _read_json(mcp_path)
    try:
        server = payload["mcpServers"]["krw-ontology"]
    except (KeyError, TypeError) as exc:
        raise BenchmarkInputError("plugin .mcp.json has no krw-ontology server") from exc
    env = dict(server.get("env") or {})
    resolved = str(release_root.resolve())
    env.update(
        {
            "KRW_ONTOLOGY_ENV": "dev",
            "KRW_ONTOLOGY_RELEASE_ROOT": resolved,
            "KRW_ONTOLOGY_ROOT": resolved,
            "KRW_ONTOLOGY_MANIFEST_PATH": str(release_root / "manifest.json"),
            "KRW_ONTOLOGY_GLOBAL_SPINE_PATH": str(release_root / "indexes" / "global_spine.sqlite"),
        }
    )
    server["env"] = env
    _atomic_write_json(mcp_path, payload)
    original_hashes = _tree_hashes(plugin_root, exclude={".mcp.json"})
    copied_hashes = _tree_hashes(destination, exclude={".mcp.json"})
    if copied_hashes != original_hashes:
        raise BenchmarkInputError("ephemeral plugin differs outside .mcp.json")
    return {
        "source": str(plugin_root.resolve()),
        "ephemeral_non_mcp_files_identical": True,
        "non_mcp_file_count": len(original_hashes),
        "source_mcp_sha256": _file_sha256(plugin_root / ".mcp.json"),
        "ephemeral_mcp_sha256": _file_sha256(mcp_path),
    }


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=5)


def _run_worker_case(
    *,
    case: Mapping[str, Any],
    plugin_root: Path,
    repo_root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="krw-agent-case-") as temp_dir:
        case_path = Path(temp_dir) / "case.json"
        _atomic_write_json(case_path, case)
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--worker-case",
            str(case_path),
            "--plugin-root",
            str(plugin_root),
            "--repo-root",
            str(repo_root),
        ]
        started = time.perf_counter()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            return {
                "format": WORKER_FORMAT,
                "case_id": case["id"],
                "status": "timeout",
                "duration_ms": elapsed_ms,
                "wall_duration_ms": elapsed_ms,
                "error": f"hard timeout after {timeout_seconds:g} seconds",
                "answer": "",
                "tool_names": [],
                "num_turns": None,
                "model": None,
            }
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        if process.returncode != 0:
            return {
                "format": WORKER_FORMAT,
                "case_id": case["id"],
                "status": "error",
                "duration_ms": elapsed_ms,
                "wall_duration_ms": elapsed_ms,
                "error": _safe_error(stderr or stdout),
                "answer": "",
                "tool_names": [],
                "num_turns": None,
                "model": None,
            }
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return {
                "format": WORKER_FORMAT,
                "case_id": case["id"],
                "status": "error",
                "duration_ms": elapsed_ms,
                "wall_duration_ms": elapsed_ms,
                "error": "worker returned invalid JSON",
                "answer": "",
                "tool_names": [],
                "num_turns": None,
                "model": None,
            }
        payload["wall_duration_ms"] = elapsed_ms
        return payload


def _safe_error(value: str, *, limit: int = 1000) -> str:
    compact = " ".join(str(value or "").split())
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        secret = os.getenv(key)
        if secret:
            compact = compact.replace(secret, "<redacted>")
    return compact[:limit] or "worker failed without diagnostics"


def _contains_expected_company(answer: str, case: Mapping[str, Any]) -> bool:
    haystack = answer.casefold()
    needles = [*case["expected_tickers"], *case["expected_company_aliases"]]
    return any(str(item).casefold() in haystack for item in needles)


def _evaluate_case(case: Mapping[str, Any], worker: Mapping[str, Any]) -> dict[str, Any]:
    answer = str(worker.get("answer") or "").strip()
    raw_tool_names = [str(item) for item in worker.get("tool_names") or []]
    ontology_tools = [name for name in raw_tool_names if QUALIFYING_TOOL_PREFIX in name]
    checks = {
        "worker_ok": worker.get("status") == "ok",
        "answer_nonempty": len(answer) >= 80,
        "expected_company_mentioned": _contains_expected_company(answer, case),
        "ontology_tool_used": bool(ontology_tools),
        "no_internal_implementation_leak": INTERNAL_LEAK_RE.search(answer) is None,
        "korean_follow_up_heading": FOLLOW_UP_HEADING in answer,
    }
    # Only hard-grounding checks block qualification. The follow-up heading is
    # recorded as a tone/format parity diagnostic because skill docs are frozen
    # during this phase and provider behavior can vary.
    required = (
        "worker_ok",
        "answer_nonempty",
        "expected_company_mentioned",
        "ontology_tool_used",
        "no_internal_implementation_leak",
    )
    return {
        "checks": checks,
        "passed": all(checks[name] for name in required),
        "required_checks": list(required),
        "ontology_tool_names": ontology_tools,
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "answer_chars": len(answer),
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return round(float(ordered[rank]), 3)


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    durations = [float(row["worker"].get("wall_duration_ms") or 0.0) for row in records]
    tool_counts = [len(row["evaluation"]["ontology_tool_names"]) for row in records]
    answer_chars = [int(row["evaluation"]["answer_chars"]) for row in records]
    passed = sum(bool(row["evaluation"]["passed"]) for row in records)
    return {
        "case_count": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "pass_rate": round(passed / len(records), 6) if records else 0.0,
        "latency_ms": {
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
        },
        "ontology_tool_calls": {
            "median": statistics.median(tool_counts) if tool_counts else None,
            "max": max(tool_counts) if tool_counts else None,
        },
        "answer_chars": {
            "median": statistics.median(answer_chars) if answer_chars else None,
        },
    }


def _baseline_delta(
    summary: Mapping[str, Any], baseline_path: Path | None
) -> dict[str, Any] | None:
    if baseline_path is None:
        return None
    baseline = _read_json(baseline_path)
    if not isinstance(baseline, Mapping) or baseline.get("format") != REPORT_FORMAT:
        raise BenchmarkInputError("agent SDK baseline format mismatch")
    old = baseline.get("summary")
    if not isinstance(old, Mapping):
        raise BenchmarkInputError("agent SDK baseline summary missing")
    old_p95 = float((old.get("latency_ms") or {}).get("p95") or 0.0)
    new_p95 = float((summary.get("latency_ms") or {}).get("p95") or 0.0)
    return {
        "baseline_path": str(baseline_path.resolve()),
        "pass_rate_delta": round(float(summary["pass_rate"]) - float(old["pass_rate"]), 6),
        "p95_latency_ratio": round(new_p95 / old_p95, 6) if old_p95 else None,
    }


def _write_markdown(path: Path, report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Agent SDK plugin answer benchmark",
        "",
        f"- Release: `{report['release']['release_id']}`",
        f"- Provider endpoint: `{report['provider'].get('endpoint_host') or 'default'}`",
        f"- Model observed: `{report.get('observed_model') or 'unknown'}`",
        f"- Passed: `{summary['passed']}/{summary['case_count']}`",
        f"- Latency p50/p95: `{summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} ms`",
        "",
        "## Blind-review answers",
        "",
    ]
    for row in report["records"]:
        lines.extend(
            [
                f"### {row['case']['id']}",
                "",
                f"Question: {row['case']['user_question_ko']}",
                "",
                f"Automatic gate: `{'PASS' if row['evaluation']['passed'] else 'FAIL'}`",
                "",
                str(row["worker"].get("answer") or "_No answer generated._"),
                "",
                f"Review focus: {', '.join(row['case'].get('review_focus') or [])}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _main_benchmark(args: argparse.Namespace) -> int:
    release_root = args.release_root.expanduser().resolve()
    plugin_root = args.plugin_root.expanduser().resolve()
    repo_root = args.repo_root.expanduser().resolve()
    gold = _validate_gold(_read_json(args.gold))
    release = _release_binding(release_root, gold)
    cases = gold["cases"][: args.case_limit] if args.case_limit else gold["cases"]
    if not cases:
        raise BenchmarkInputError("--case-limit selected no cases")
    with tempfile.TemporaryDirectory(prefix="krw-agent-plugin-") as temp_dir:
        ephemeral_plugin = Path(temp_dir) / "krw-ontology"
        plugin = _prepare_plugin_copy(plugin_root, release_root, ephemeral_plugin)
        records: list[dict[str, Any]] = []
        for case in cases:
            worker = _run_worker_case(
                case=case,
                plugin_root=ephemeral_plugin,
                repo_root=repo_root,
                timeout_seconds=args.timeout_seconds,
            )
            records.append(
                {
                    "case": case,
                    "worker": worker,
                    "evaluation": _evaluate_case(case, worker),
                }
            )
            # Provider, SDK-control-stream, and MCP-session failures are not
            # answer-quality samples. One isolated attempt is enough to prove
            # the qualification cannot proceed; do not spend the same timeout
            # on every remaining gold case.
            if worker.get("status") in {"timeout", "error"}:
                break
    summary = _summary(records)
    models = sorted(
        {str(row["worker"].get("model")) for row in records if row["worker"].get("model")}
    )
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release": release,
        "gold": {
            "path": str(args.gold.resolve()),
            "sha256": _file_sha256(args.gold),
            "format": GOLD_FORMAT,
            "case_count": len(cases),
            "executed_case_count": len(records),
        },
        "configuration": {
            "system_prompt": "claude_code_preset_unchanged",
            "model_override": None,
            "effort_override": None,
            "max_turns_override": None,
            "ontology_tool_count": _ontology_tool_count(),
            "case_process_isolation": True,
            "hard_timeout_seconds": args.timeout_seconds,
        },
        "plugin": plugin,
        "provider": _provider_metadata(),
        "observed_model": models[0] if len(models) == 1 else models,
        "summary": summary,
        "baseline_comparison": _baseline_delta(summary, args.baseline),
        "gate": {"passed": summary["failed"] == 0},
        "records": records,
    }
    _atomic_write_json(args.output, report)
    markdown_path = args.output.with_suffix(".md")
    _write_markdown(markdown_path, report)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "markdown": str(markdown_path.resolve()),
                "summary": summary,
                "gate": report["gate"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["gate"]["passed"] else 2


def _plain(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


async def _agent_worker(
    case: Mapping[str, Any], plugin_root: Path, repo_root: Path
) -> dict[str, Any]:
    from claude_agent_sdk import ClaudeAgentOptions, query
    from claude_agent_sdk.types import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolUseBlock,
    )

    options = ClaudeAgentOptions(
        cwd=repo_root,
        plugins=[{"type": "local", "path": str(plugin_root)}],
        strict_mcp_config=True,
        permission_mode="bypassPermissions",
        setting_sources=["user", "project", "local"],
        system_prompt={"type": "preset", "preset": "claude_code"},
    )
    answer_parts: list[str] = []
    tool_names: list[str] = []
    result_text = ""
    num_turns: int | None = None
    model: str | None = None
    started = time.perf_counter()
    async for message in query(prompt=str(case["user_question_ko"]), options=options):
        if isinstance(message, SystemMessage):
            payload = _plain(message)
            data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
            model = str(data.get("model")) if data.get("model") else model
        elif isinstance(message, AssistantMessage):
            model = str(getattr(message, "model", None) or model or "") or None
            for block in message.content:
                if isinstance(block, TextBlock):
                    answer_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_names.append(block.name)
        elif isinstance(message, ResultMessage):
            result_text = str(message.result or "")
            num_turns = message.num_turns
            usage = getattr(message, "model_usage", None)
            if isinstance(usage, Mapping) and len(usage) == 1:
                model = str(next(iter(usage)))
    answer = result_text.strip() or "\n".join(part.strip() for part in answer_parts if part.strip())
    return {
        "format": WORKER_FORMAT,
        "case_id": case["id"],
        "status": "ok",
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "error": None,
        "answer": answer,
        "tool_names": tool_names,
        "num_turns": num_turns,
        "model": model,
    }


def _main_worker(args: argparse.Namespace) -> int:
    case = _read_json(args.worker_case)
    if not isinstance(case, Mapping):
        raise BenchmarkInputError("worker case must be a JSON object")
    try:
        payload = asyncio.run(_agent_worker(case, args.plugin_root, args.repo_root))
    except Exception as exc:  # pragma: no cover - external SDK/provider behavior
        payload = {
            "format": WORKER_FORMAT,
            "case_id": case.get("id"),
            "status": "error",
            "duration_ms": None,
            "error": _safe_error(f"{type(exc).__name__}: {exc}"),
            "answer": "",
            "tool_names": [],
            "num_turns": None,
            "model": None,
        }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "plugins" / "krw-ontology",
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "agent_sdk_answer_gold_v1.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-case", type=Path, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.worker:
            if not args.worker_case:
                raise BenchmarkInputError("--worker requires --worker-case")
            raise SystemExit(_main_worker(args))
        if not args.release_root or not args.output:
            raise BenchmarkInputError("benchmark requires --release-root and --output")
        if args.case_limit is not None and args.case_limit <= 0:
            raise BenchmarkInputError("--case-limit must be positive")
        if args.timeout_seconds <= 0:
            raise BenchmarkInputError("--timeout-seconds must be positive")
        raise SystemExit(_main_benchmark(args))
    except BenchmarkInputError as exc:
        print(f"FAILED agent SDK benchmark: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
