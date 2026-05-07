"""Pipeline configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

import yaml


_DEFAULT_MODEL = "claude-sonnet-4-20250514"


@dataclass
class PipelineConfig:
    model: str = _DEFAULT_MODEL
    stage_models: dict[str, str] = field(default_factory=dict)
    ai_concurrency: int = 10
    stage_concurrency: dict[str, int] = field(default_factory=lambda: {
        "extract_evidence_quotes": 10,
        "extract_research_claims": 10,
        "extract_assumption_candidates": 10,
    })
    max_turns: int = 10
    call_timeout_seconds: int = 600
    max_retries: int = 3
    retry_base_delay_seconds: int = 5
    sec_user_agent: str = "krw-ontology/0.1 contact@example.com"
    batch_sizes: dict[str, int] = field(default_factory=lambda: {
        "quote_extraction": 10,
        "claim_extraction": 12,
    })
    max_context_tokens: int = 180000
    fail_on_section_quality: bool = False
    span_pruning: str = "conservative"

    @classmethod
    def load(cls) -> PipelineConfig:
        """Load config from file or environment, with defaults."""
        _load_dotenv()
        config = cls()

        # Environment variable overrides
        if env_model := os.environ.get("KRW_MODEL"):
            config.model = env_model
        if env_concurrency := os.environ.get("KRW_AI_CONCURRENCY"):
            config.ai_concurrency = _parse_positive_int(env_concurrency, config.ai_concurrency)
        if env_max_turns := os.environ.get("KRW_MAX_TURNS"):
            config.max_turns = _parse_positive_int(env_max_turns, config.max_turns)
        if env_timeout := os.environ.get("KRW_CALL_TIMEOUT_SECONDS"):
            config.call_timeout_seconds = _parse_positive_int(env_timeout, config.call_timeout_seconds)
        if env_fail_on_section_quality := os.environ.get("KRW_FAIL_ON_SECTION_QUALITY"):
            config.fail_on_section_quality = _parse_bool(
                env_fail_on_section_quality,
                config.fail_on_section_quality,
            )
        if env_span_pruning := os.environ.get("KRW_SPAN_PRUNING"):
            config.span_pruning = _parse_span_pruning_mode(env_span_pruning, config.span_pruning)
        _apply_stage_env(config)
        _apply_batch_size_env(config)

        # File-based config
        config_path = _find_config_file()
        if config_path is not None:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            if "model" in data:
                config.model = data["model"]
            if "stage_models" in data:
                config.stage_models.update(data["stage_models"] or {})
            if "ai_concurrency" in data:
                config.ai_concurrency = int(data["ai_concurrency"])
            if "stage_concurrency" in data:
                config.stage_concurrency.update(data["stage_concurrency"] or {})
            if "max_turns" in data:
                config.max_turns = int(data["max_turns"])
            if "call_timeout_seconds" in data:
                config.call_timeout_seconds = int(data["call_timeout_seconds"])
            if "max_retries" in data:
                config.max_retries = data["max_retries"]
            if "retry_base_delay_seconds" in data:
                config.retry_base_delay_seconds = data["retry_base_delay_seconds"]
            if "sec_user_agent" in data:
                config.sec_user_agent = data["sec_user_agent"]
            if "batch_sizes" in data:
                config.batch_sizes.update(data["batch_sizes"])
            if "max_context_tokens" in data:
                config.max_context_tokens = data["max_context_tokens"]
            if "fail_on_section_quality" in data:
                config.fail_on_section_quality = _coerce_bool(
                    data["fail_on_section_quality"],
                    config.fail_on_section_quality,
                )
            if "span_pruning" in data:
                config.span_pruning = _parse_span_pruning_mode(
                    str(data["span_pruning"]),
                    config.span_pruning,
                )

        # Env var always wins over file
        if env_model := os.environ.get("KRW_MODEL"):
            config.model = env_model
        if env_concurrency := os.environ.get("KRW_AI_CONCURRENCY"):
            config.ai_concurrency = _parse_positive_int(env_concurrency, config.ai_concurrency)
        if env_max_turns := os.environ.get("KRW_MAX_TURNS"):
            config.max_turns = _parse_positive_int(env_max_turns, config.max_turns)
        if env_timeout := os.environ.get("KRW_CALL_TIMEOUT_SECONDS"):
            config.call_timeout_seconds = _parse_positive_int(env_timeout, config.call_timeout_seconds)
        if env_fail_on_section_quality := os.environ.get("KRW_FAIL_ON_SECTION_QUALITY"):
            config.fail_on_section_quality = _parse_bool(
                env_fail_on_section_quality,
                config.fail_on_section_quality,
            )
        if env_span_pruning := os.environ.get("KRW_SPAN_PRUNING"):
            config.span_pruning = _parse_span_pruning_mode(env_span_pruning, config.span_pruning)
        _apply_stage_env(config)
        _apply_batch_size_env(config)

        return config

    def model_for_stage(self, stage_name: str) -> str:
        return self.stage_models.get(stage_name, self.model)

    def concurrency_for_stage(self, stage_name: str) -> int:
        return max(1, int(self.stage_concurrency.get(stage_name, self.ai_concurrency)))

    def batch_size_for_stage(self, stage_name: str, default: int) -> int:
        key_by_stage = {
            "extract_evidence_quotes": "quote_extraction",
            "extract_research_claims": "claim_extraction",
        }
        key = key_by_stage.get(stage_name, stage_name)
        return max(1, int(self.batch_sizes.get(key, default)))


def _parse_positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


def _parse_bool(value: str, default: bool) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value, default)
    if isinstance(value, int):
        return bool(value)
    return default


def _parse_span_pruning_mode(value: str, default: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"off", "conservative"}:
        return normalized
    return default


def _load_dotenv() -> None:
    """Load project-local .env values without overriding existing environment."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _apply_stage_env(config: PipelineConfig) -> None:
    for stage_name in _AI_STAGE_NAMES:
        suffix = stage_name.upper()
        if model := os.environ.get(f"KRW_STAGE_MODEL_{suffix}"):
            config.stage_models[stage_name] = model
        if concurrency := os.environ.get(f"KRW_STAGE_CONCURRENCY_{suffix}"):
            config.stage_concurrency[stage_name] = _parse_positive_int(
                concurrency,
                config.concurrency_for_stage(stage_name),
            )


def _apply_batch_size_env(config: PipelineConfig) -> None:
    env_keys = {
        "quote_extraction": "KRW_BATCH_SIZE_QUOTE_EXTRACTION",
        "claim_extraction": "KRW_BATCH_SIZE_CLAIM_EXTRACTION",
    }
    for key, env_name in env_keys.items():
        if value := os.environ.get(env_name):
            config.batch_sizes[key] = _parse_positive_int(
                value,
                int(config.batch_sizes.get(key, 1)),
            )


_AI_STAGE_NAMES = (
    "extract_evidence_quotes",
    "extract_research_claims",
    "extract_assumption_candidates",
)


def _find_config_file() -> Path | None:
    candidates = [
        Path.home() / ".config" / "krw-ontology" / "config.yaml",
        Path(".krw-ontology.yaml"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None
