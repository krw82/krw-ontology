"""Persistent CLI defaults for local operator workflows."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from krw_ontology.config.paths import resolve_ontology_root


CLI_CONFIG_ENV = "KRW_ONTOLOGY_CLI_CONFIG"
DEFAULT_CLI_CONFIG_PATH = Path.home() / ".config" / "krw-ontology" / "config.json"
CONFIG_KEYS = {"running-root", "publish-root", "publish-index-path"}


@dataclass
class CliConfig:
    running_root: str | None = None
    publish_root: str | None = None
    publish_index_path: str | None = None

    @classmethod
    def from_dict(cls, payload: dict) -> "CliConfig":
        return cls(
            running_root=payload.get("running_root"),
            publish_root=payload.get("publish_root"),
            publish_index_path=payload.get("publish_index_path"),
        )

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


def cli_config_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    raw_path = os.environ.get(CLI_CONFIG_ENV)
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return DEFAULT_CLI_CONFIG_PATH.expanduser().resolve()


def load_cli_config(path: Path | str | None = None) -> CliConfig:
    config_path = cli_config_path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CliConfig()
    return CliConfig.from_dict(payload)


def save_cli_config(config: CliConfig, path: Path | str | None = None) -> Path:
    config_path = cli_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(config_path)
    return config_path


def config_attr(key: str) -> str:
    if key not in CONFIG_KEYS:
        raise KeyError(key)
    return key.replace("-", "_")


def set_config_value(key: str, value: str, path: Path | str | None = None) -> CliConfig:
    config = load_cli_config(path)
    attr = config_attr(key)
    resolved = Path(value).expanduser().resolve()
    setattr(config, attr, str(resolved))
    save_cli_config(config, path)
    return config


def unset_config_value(key: str, path: Path | str | None = None) -> CliConfig:
    config = load_cli_config(path)
    attr = config_attr(key)
    setattr(config, attr, None)
    save_cli_config(config, path)
    return config


def resolve_running_root(root: Path | str | None = None, *, fallback_to_cwd: bool = False) -> Path:
    if root is not None:
        return resolve_ontology_root(root, fallback_to_cwd=fallback_to_cwd)
    config = load_cli_config()
    if config.running_root:
        return Path(config.running_root).expanduser().resolve()
    return resolve_ontology_root(None, fallback_to_cwd=fallback_to_cwd)


def resolve_publish_root(root: Path | str | None = None) -> Path | None:
    if root is not None:
        return Path(root).expanduser().resolve()
    config = load_cli_config()
    if config.publish_root:
        return Path(config.publish_root).expanduser().resolve()
    return None


def resolve_publish_index_path(path: Path | str | None = None) -> Path | None:
    if path is not None:
        return Path(path).expanduser().resolve()
    config = load_cli_config()
    if config.publish_index_path:
        return Path(config.publish_index_path).expanduser().resolve()
    return None
