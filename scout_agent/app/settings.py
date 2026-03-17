from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from scout_agent.configuration.scout_config import load_default_scout_config
from scout_agent.configuration.settings import (
    resolve_agent_read_limit,
    resolve_extra_prompt_text,
    resolve_facts_path,
    resolve_llm_mode,
    resolve_max_parallel_files,
    resolve_model_name,
    resolve_project_root,
    resolve_report_path,
)

DEFAULT_AUDIT_RECURSION_LIMIT = 3000


@dataclass(frozen=True, slots=True)
class ExtractSettings:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int


@dataclass(frozen=True, slots=True)
class AuditSettings(ExtractSettings):
    report_path: Path
    extra_prompt: str | None
    recursion_limit: int
    agent_read_limit: int


def resolve_extract_settings(args: Namespace) -> ExtractSettings:
    return ExtractSettings(**_resolve_common(args))


def resolve_audit_config(args: Namespace) -> AuditSettings:
    common = _resolve_common(args)
    project_root = common["project_root"]
    scout_config = load_default_scout_config(project_root)

    return AuditSettings(
        **common,
        report_path=resolve_report_path(project_root, args.report_path),
        extra_prompt=resolve_extra_prompt_text(project_root, args.extra_prompt),
        recursion_limit=DEFAULT_AUDIT_RECURSION_LIMIT,
        agent_read_limit=resolve_agent_read_limit(
            getattr(args, "agent_read_limit", None),
            fallback=scout_config.agent_read_limit if scout_config else None,
        ),
    )


def _resolve_common(args: Namespace) -> dict:
    project_root = resolve_project_root(args.project_root)
    scout_config = load_default_scout_config(project_root)

    return dict(
        project_root=project_root,
        facts_path=resolve_facts_path(project_root, args.facts_path),
        model_name=resolve_model_name(
            args.model, fallback=scout_config.model if scout_config else None
        ),
        llm_mode=resolve_llm_mode(
            args.llm_mode, fallback=scout_config.mode if scout_config else None
        ),
        scout_files=scout_config.files if scout_config else None,
        max_parallel_files=resolve_max_parallel_files(
            getattr(args, "max_parallel_files", None),
            fallback=scout_config.max_parallel_files if scout_config else None,
        ),
    )
