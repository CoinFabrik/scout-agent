from dataclasses import dataclass
from pathlib import Path

from scout_agent.config.scout_config import load_default_scout_config
from scout_agent.config.resolvers import (
    resolve_agent_read_limit,
    resolve_agent_grep_limit,
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
class ResolvedExtractSettings:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int


@dataclass(frozen=True, slots=True)
class _CommonSettings:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int


@dataclass(frozen=True, slots=True)
class ResolvedAuditSettings:
    project_root: Path
    facts_path: Path
    report_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int
    recursion_limit: int
    agent_read_limit: int
    agent_grep_limit: int
    extra_prompt: str | None
    thread_id: str | None


def resolve_extract_settings(
    *,
    project_root: str,
    facts_path: str | None,
    model: str | None,
    llm_mode: str | None,
    max_parallel_files: int | None,
) -> ResolvedExtractSettings:
    common = _resolve_common(
        project_root=project_root,
        facts_path=facts_path,
        model=model,
        llm_mode=llm_mode,
        max_parallel_files=max_parallel_files,
    )
    return _to_resolved_extract_settings(common)


def _to_resolved_extract_settings(common: _CommonSettings) -> ResolvedExtractSettings:
    return ResolvedExtractSettings(
        project_root=common.project_root,
        facts_path=common.facts_path,
        model_name=common.model_name,
        llm_mode=common.llm_mode,
        scout_files=common.scout_files,
        max_parallel_files=common.max_parallel_files,
    )


def resolve_audit_settings(
    *,
    project_root: str,
    facts_path: str | None,
    report_path: str | None,
    model: str | None,
    llm_mode: str | None,
    extra_prompt: str | None,
    max_parallel_files: int | None,
    agent_read_limit: int | None,
    agent_grep_limit: int | None,
    resume: str | None,
) -> ResolvedAuditSettings:
    common = _resolve_common(
        project_root=project_root,
        facts_path=facts_path,
        model=model,
        llm_mode=llm_mode,
        max_parallel_files=max_parallel_files,
    )
    resolved_project_root = common.project_root
    scout_config = load_default_scout_config(resolved_project_root)

    return ResolvedAuditSettings(
        project_root=common.project_root,
        facts_path=common.facts_path,
        report_path=resolve_report_path(resolved_project_root, report_path),
        model_name=common.model_name,
        llm_mode=common.llm_mode,
        scout_files=common.scout_files,
        max_parallel_files=common.max_parallel_files,
        recursion_limit=DEFAULT_AUDIT_RECURSION_LIMIT,
        agent_read_limit=resolve_agent_read_limit(
            agent_read_limit,
            fallback=scout_config.agent_read_limit if scout_config else None,
        ),
        agent_grep_limit=resolve_agent_grep_limit(
            agent_grep_limit,
            fallback=scout_config.agent_grep_limit if scout_config else None,
        ),
        extra_prompt=resolve_extra_prompt_text(resolved_project_root, extra_prompt),
        thread_id=resume,
    )


def _resolve_common(
    *,
    project_root: str,
    facts_path: str | None,
    model: str | None,
    llm_mode: str | None,
    max_parallel_files: int | None,
) -> _CommonSettings:
    resolved_project_root = resolve_project_root(project_root)
    scout_config = load_default_scout_config(resolved_project_root)

    return _CommonSettings(
        project_root=resolved_project_root,
        facts_path=resolve_facts_path(resolved_project_root, facts_path),
        model_name=resolve_model_name(
            model,
            fallback=scout_config.model if scout_config else None,
        ),
        llm_mode=resolve_llm_mode(
            llm_mode,
            fallback=scout_config.mode if scout_config else None,
        ),
        scout_files=scout_config.files if scout_config else None,
        max_parallel_files=resolve_max_parallel_files(
            max_parallel_files,
            fallback=scout_config.max_parallel_files if scout_config else None,
        ),
    )
