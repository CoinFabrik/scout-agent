from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm_modes import normalize_llm_mode

DEFAULT_SCOUT_CONFIG_FILENAME = "scout.json"


@dataclass(frozen=True, slots=True)
class ScoutConfig:
    model: str | None = None
    mode: str | None = None
    files: list[str] | None = None
    max_parallel_files: int | None = None
    recursion_limit: int | None = None
    agent_read_limit: int | None = None
    agent_grep_limit: int | None = None


def resolve_default_scout_config_path(project_root: Path) -> Path | None:
    candidate = project_root.resolve() / DEFAULT_SCOUT_CONFIG_FILENAME
    if not candidate.exists():
        return None
    if candidate.is_symlink():
        raise ValueError(f"Refusing to load symlinked config file: {candidate}")
    if not candidate.is_file():
        raise ValueError(f"Expected scout.json to be a file: {candidate}")
    return candidate


def load_scout_config(path: Path) -> ScoutConfig:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON inside {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("scout.json must contain a JSON object at the top level.")

    model = _optional_non_empty_string(payload.get("model"), field_name="model")
    mode = payload.get("mode")
    if mode is not None and not isinstance(mode, str):
        raise ValueError("'mode' must be a string when provided in scout.json")

    files = _optional_files(payload.get("files"))
    max_parallel_files = _optional_positive_int(
        payload.get("max_parallel_files"),
        field_name="max_parallel_files",
    )
    recursion_limit = _optional_positive_int(
        payload.get("recursion_limit"),
        field_name="recursion_limit",
    )
    agent_read_limit = _optional_positive_int(
        payload.get("agent_read_limit"),
        field_name="agent_read_limit",
    )
    agent_grep_limit = _optional_positive_int(
        payload.get("agent_grep_limit"),
        field_name="agent_grep_limit",
    )

    return ScoutConfig(
        model=model,
        mode=normalize_llm_mode(mode),
        files=files,
        max_parallel_files=max_parallel_files,
        recursion_limit=recursion_limit,
        agent_read_limit=agent_read_limit,
        agent_grep_limit=agent_grep_limit,
    )


def load_default_scout_config(project_root: Path) -> ScoutConfig | None:
    config_path = resolve_default_scout_config_path(project_root)
    if config_path is None:
        return None
    return load_scout_config(config_path)


def _optional_non_empty_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field_name}' must be a non-empty string when provided")
    return value.strip()


def _optional_files(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("'files' must be a list of paths when provided in scout.json")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("'files' entries must be non-empty strings")
        normalized.append(item.strip())

    return normalized


def _optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"'{field_name}' must be an integer when provided")
    if value < 0:
        raise ValueError(f"'{field_name}' must be >= 0 when provided")
    return value
