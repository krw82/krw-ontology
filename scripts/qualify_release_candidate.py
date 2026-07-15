#!/usr/bin/env python3
"""Run the one-time post-build qualification for an immutable candidate.

The script does not build, poll, promote, or mutate ``current``. It executes
the deterministic Router release gate first and then the isolated real Agent
SDK + plugin answer benchmark. A single summary binds both reports to the
candidate release.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


QUALIFICATION_FORMAT = "krw-ontology-release-qualification/v1"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(command: list[str], *, log_handle: Any) -> dict[str, Any]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {
        "command": [str(item) for item in command],
        "returncode": completed.returncode,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "passed": completed.returncode == 0,
    }


def _report_gate(path: Path) -> bool:
    try:
        payload = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("gate", {}).get("passed") is True


def qualify(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.expanduser().resolve()
    release_root = args.release_root.expanduser().resolve()
    manifest_path = release_root / "manifest.json"
    global_spine = release_root / "indexes" / "global_spine.sqlite"
    sidecar = release_root / "indexes" / "router_sidecar.sqlite"
    coherence = release_root / "indexes" / "router_coherence.sqlite"
    for required in (manifest_path, global_spine, sidecar, coherence):
        if not required.is_file():
            raise FileNotFoundError(f"candidate artifact missing: {required}")
    manifest = _read_json(manifest_path)
    release_id = str(manifest.get("release_id") or release_root.name)
    verify_root = release_root / "verify"
    verify_root.mkdir(parents=True, exist_ok=True)
    router_report = verify_root / "router-bootstrap-v4.json"
    mcp_report = verify_root / "mcp-candidate-v1.json"
    agent_report = verify_root / "agent-sdk-answer-v1.json"
    qualification_report = verify_root / "qualification-v1.json"
    qualification_log = verify_root / "qualification-v1.log"

    router_command = [
        sys.executable,
        str(repo_root / "scripts" / "benchmark_router_sidecar.py"),
        "--global-spine",
        str(global_spine),
        "--sidecar",
        str(sidecar),
        "--queries",
        str(repo_root / "benchmarks" / "router_gold_v2.json"),
        "--planned-queries",
        str(repo_root / "benchmarks" / "router_planned_gold_v2.json"),
        "--limit",
        "20",
        "--cold-iterations",
        str(args.router_cold_iterations),
        "--iterations",
        str(args.router_iterations),
        "--budget",
        str(repo_root / "benchmarks" / "router_bootstrap_budget_v2.json"),
        "--output",
        str(router_report),
    ]
    agent_command = [
        sys.executable,
        str(repo_root / "scripts" / "benchmark_agent_sdk_plugin.py"),
        "--release-root",
        str(release_root),
        "--plugin-root",
        str(repo_root / "plugins" / "krw-ontology"),
        "--repo-root",
        str(repo_root),
        "--gold",
        str(repo_root / "benchmarks" / "agent_sdk_answer_gold_v1.json"),
        "--output",
        str(agent_report),
        "--timeout-seconds",
        str(args.agent_timeout_seconds),
    ]
    if args.agent_case_limit:
        agent_command.extend(["--case-limit", str(args.agent_case_limit)])
    if args.agent_baseline:
        agent_command.extend(["--baseline", str(args.agent_baseline.expanduser().resolve())])
    mcp_command = [
        sys.executable,
        str(repo_root / "scripts" / "benchmark_mcp_candidate.py"),
        "--release-root",
        str(release_root),
        "--planned-gold",
        str(repo_root / "benchmarks" / "router_planned_gold_v2.json"),
        "--output",
        str(mcp_report),
    ]

    started_at = datetime.now(timezone.utc).isoformat()
    with qualification_log.open("a", encoding="utf-8") as log_handle:
        router_result = _run(router_command, log_handle=log_handle)
        if router_result["passed"]:
            mcp_result = _run(mcp_command, log_handle=log_handle)
        else:
            mcp_result = {
                "command": mcp_command,
                "returncode": None,
                "duration_ms": 0.0,
                "passed": False,
                "skipped": True,
                "reason": "router_gate_failed",
            }
        if router_result["passed"] and mcp_result["passed"] and args.include_agent_sdk:
            agent_result = _run(agent_command, log_handle=log_handle)
        elif not args.include_agent_sdk:
            agent_result = {
                "command": agent_command,
                "returncode": None,
                "duration_ms": 0.0,
                "passed": None,
                "skipped": True,
            }
        else:
            agent_result = {
                "command": agent_command,
                "returncode": None,
                "duration_ms": 0.0,
                "passed": False,
                "skipped": True,
                "reason": "router_or_mcp_gate_failed",
            }

    router_gate = router_result["passed"] and _report_gate(router_report)
    mcp_gate = mcp_result["passed"] and _report_gate(mcp_report)
    agent_gate = (
        True
        if not args.include_agent_sdk
        else agent_result["passed"] and _report_gate(agent_report)
    )
    passed = bool(router_gate and mcp_gate and agent_gate)
    summary = {
        "format": QUALIFICATION_FORMAT,
        "release_id": release_id,
        "release_root": str(release_root),
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "build_or_poll_performed": False,
            "promotion_performed": False,
            "agent_sdk_required": args.include_agent_sdk,
        },
        "router": {
            **router_result,
            "report": str(router_report),
            "report_gate_passed": router_gate,
        },
        "mcp": {
            **mcp_result,
            "report": str(mcp_report),
            "report_gate_passed": mcp_gate,
        },
        "agent_sdk": {
            **agent_result,
            "report": str(agent_report),
            "report_gate_passed": agent_gate,
        },
        "gate": {"passed": passed},
        "log": str(qualification_log),
    }
    _atomic_write_json(qualification_report, summary)
    print(
        json.dumps(
            {
                "release_id": release_id,
                "qualification_report": str(qualification_report),
                "router_report": str(router_report),
                "mcp_report": str(mcp_report),
                "agent_report": str(agent_report),
                "gate": summary["gate"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--release-root", type=Path, required=True)
    result.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    result.add_argument("--router-iterations", type=int, default=7)
    result.add_argument("--router-cold-iterations", type=int, default=3)
    result.add_argument("--agent-timeout-seconds", type=float, default=180.0)
    result.add_argument("--agent-case-limit", type=int, default=4)
    result.add_argument("--agent-baseline", type=Path)
    result.add_argument(
        "--include-agent-sdk",
        action="store_true",
        help="Also run the external Agent SDK/provider answer gate. Disabled by default.",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    if args.router_iterations <= 0 or args.router_cold_iterations <= 0:
        raise SystemExit("router iterations must be positive")
    if args.agent_timeout_seconds <= 0 or args.agent_case_limit <= 0:
        raise SystemExit("agent timeout and case limit must be positive")
    try:
        raise SystemExit(qualify(args))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"FAILED candidate qualification: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
