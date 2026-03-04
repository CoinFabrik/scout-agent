from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Mapping

from .llm_modes import DEFAULT_LLM_MODE, normalize_llm_mode

DEFAULT_FACTS_FILENAME: Final[str] = "FACTS.yaml"
DEFAULT_REPORT_FILENAME: Final[str] = "REPORT.md"
MODEL_ENV_VAR: Final[str] = "SCOUT_MODEL"
DEFAULT_MAX_PARALLEL_FILES: Final[int] = 4
MAX_MAX_PARALLEL_FILES: Final[int] = 100


def resolve_project_root(raw_path: str) -> Path:
    project_root = Path(raw_path).expanduser().resolve()

    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")
    if not project_root.is_dir():
        raise ValueError(f"Project root must be a directory: {project_root}")

    return project_root


def resolve_facts_path(project_root: Path, override: str | None) -> Path:
    return _resolve_output_path(
        project_root=project_root,
        override=override,
        default_filename=DEFAULT_FACTS_FILENAME,
    )


def resolve_report_path(project_root: Path, override: str | None) -> Path:
    return _resolve_output_path(
        project_root=project_root,
        override=override,
        default_filename=DEFAULT_REPORT_FILENAME,
    )


def resolve_extra_prompt_text(project_root: Path, override: str | None) -> str | None:
    if override is None or not override.strip():
        return None

    raw = Path(override).expanduser()
    prompt_path = raw.resolve() if raw.is_absolute() else (project_root / raw).resolve()

    if not prompt_path.exists():
        raise FileNotFoundError(f"Extra prompt file does not exist: {prompt_path}")
    if prompt_path.is_symlink():
        raise ValueError(f"Refusing to load symlinked extra prompt file: {prompt_path}")
    if not prompt_path.is_file():
        raise ValueError(f"Extra prompt path must be a file: {prompt_path}")
    if prompt_path.suffix.lower() != ".txt":
        raise ValueError(f"--extra-prompt must point to a .txt file: {prompt_path}")

    prompt_text = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt_text:
        raise ValueError(f"Extra prompt file is empty: {prompt_path}")

    return prompt_text


def resolve_model_name(
    cli_value: str | None,
    *,
    fallback: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    if cli_value is not None and cli_value.strip():
        return cli_value.strip()

    resolved_env = os.environ if env is None else env
    env_value = resolved_env.get(MODEL_ENV_VAR)
    if env_value and env_value.strip():
        return env_value.strip()

    if fallback is not None and fallback.strip():
        return fallback.strip()

    raise ValueError(
        "Model is required. Pass --model, set SCOUT_MODEL, or provide a model in scout.json."
    )


def resolve_llm_mode(mode: str | None, *, fallback: str | None = None) -> str:
    return normalize_llm_mode(mode) or normalize_llm_mode(fallback) or DEFAULT_LLM_MODE


def resolve_max_parallel_files(
    cli_value: int | None,
    *,
    fallback: int | None = None,
) -> int:
    candidate = cli_value if cli_value is not None else fallback
    if candidate is None:
        candidate = DEFAULT_MAX_PARALLEL_FILES

    if candidate < 1 or candidate > MAX_MAX_PARALLEL_FILES:
        raise ValueError(
            f"max_parallel_files must be between 1 and {MAX_MAX_PARALLEL_FILES}; got {candidate}"
        )

    return candidate


def _resolve_output_path(
    *,
    project_root: Path,
    override: str | None,
    default_filename: str,
) -> Path:
    if override is None or not override.strip():
        return project_root / default_filename

    raw = Path(override).expanduser()
    if raw.is_absolute():
        return raw.resolve()

    return (project_root / raw).resolve()
