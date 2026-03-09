from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from scout_agent.app.audit_ui import AuditUiMode
from scout_agent.configuration.scout_config import ScoutConfig, load_default_scout_config
from scout_agent.configuration.settings import (
    resolve_extra_prompt_text,
    resolve_facts_path,
    resolve_llm_mode,
    resolve_max_parallel_files,
    resolve_model_name,
    resolve_project_root,
    resolve_report_path,
)


@dataclass(frozen=True, slots=True)
class ResolvedExtractConfig:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int


@dataclass(frozen=True, slots=True)
class ResolvedAuditConfig:
    project_root: Path
    facts_path: Path
    report_path: Path
    model_name: str | None
    llm_mode: str
    scout_files: list[str] | None
    extra_prompt: str | None
    ui_mode: AuditUiMode
    dump_runtime: bool


def resolve_extract_config(
    args: Namespace,
    *,
    env: Mapping[str, str] | None = None,
) -> ResolvedExtractConfig:
    project_root = resolve_project_root(args.project_root)
    scout_config = load_default_scout_config(project_root)

    return ResolvedExtractConfig(
        project_root=project_root,
        facts_path=resolve_facts_path(project_root, args.facts_path),
        model_name=resolve_model_name(
            args.model,
            fallback=_scout_model_name(scout_config),
            env=env,
        ),
        llm_mode=resolve_llm_mode(
            args.llm_mode,
            fallback=_scout_mode(scout_config),
        ),
        scout_files=_scout_files(scout_config),
        max_parallel_files=resolve_max_parallel_files(
            args.max_parallel_files,
            fallback=_scout_max_parallel_files(scout_config),
        ),
    )


def resolve_audit_config(
    args: Namespace,
    *,
    facts_model: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ResolvedAuditConfig:
    project_root = resolve_project_root(args.project_root)
    scout_config = load_default_scout_config(project_root)

    return ResolvedAuditConfig(
        project_root=project_root,
        facts_path=resolve_facts_path(project_root, args.facts_path),
        report_path=resolve_report_path(project_root, args.report_path),
        model_name=_resolve_audit_model_name(
            cli_value=args.model,
            scout_config_model=_scout_model_name(scout_config),
            facts_model=facts_model,
            env=env,
        ),
        llm_mode=resolve_llm_mode(
            args.llm_mode,
            fallback=_scout_mode(scout_config),
        ),
        scout_files=_scout_files(scout_config),
        extra_prompt=resolve_extra_prompt_text(project_root, args.extra_prompt),
        ui_mode=cast(AuditUiMode, getattr(args, "ui", "tui")),
        dump_runtime=bool(getattr(args, "dump_runtime", False)),
    )


def _resolve_audit_model_name(
    *,
    cli_value: str | None,
    scout_config_model: str | None,
    facts_model: str | None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    try:
        return resolve_model_name(
            cli_value,
            fallback=scout_config_model,
            env=env,
        )
    except ValueError:
        if facts_model is not None and facts_model.strip():
            return facts_model.strip()
        return None


def _scout_model_name(scout_config: ScoutConfig | None) -> str | None:
    return scout_config.model if scout_config else None


def _scout_mode(scout_config: ScoutConfig | None) -> str | None:
    return scout_config.mode if scout_config else None


def _scout_files(scout_config: ScoutConfig | None) -> list[str] | None:
    return scout_config.files if scout_config else None


def _scout_max_parallel_files(scout_config: ScoutConfig | None) -> int | None:
    return scout_config.max_parallel_files if scout_config else None
