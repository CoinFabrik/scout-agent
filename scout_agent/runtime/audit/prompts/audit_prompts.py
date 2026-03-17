from __future__ import annotations

from pathlib import Path

from scout_agent.domain.facts import (
    AggregateFactsDocument,
    FunctionSummary,
    present_summary_fields,
)
from scout_agent.runtime.audit.prompts.prompt_utils import append_extra_prompt
from scout_agent.runtime.prompt_loader import (
    load_optional_prompt_asset,
    load_prompt_asset,
)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SUPERVISOR_SYSTEM_PROMPT_FILE = "audit/supervisor/system.md"
_SUPERVISOR_USER_PROMPT_FILE = "audit/supervisor/user.md"
_SUPERVISOR_FEW_SHOTS_FILE = "audit/supervisor/few_shots.md"
_EXECUTION_PATH_CONSISTENCY_SYSTEM_PROMPT_FILE = (
    "audit/execution_path_consistency/system.md"
)
_EXECUTION_PATH_CONSISTENCY_USER_PROMPT_FILE = (
    "audit/execution_path_consistency/user.md"
)
_EXPERT_SYSTEM_PROMPT_FILES = {
    "collection_validation": "audit/experts/collection_validation/system.md",
    "time_state": "audit/experts/time_state/system.md",
    "sentinel_logic": "audit/experts/sentinel_logic/system.md",
}
_EXPERT_FEW_SHOT_FILES = {
    "collection_validation": "audit/experts/collection_validation/few_shots.md",
    "time_state": "audit/experts/time_state/few_shots.md",
    "sentinel_logic": "audit/experts/sentinel_logic/few_shots.md",
}


def build_expert_system_prompt(
    *,
    expert_name: str,
    extra_prompt: str | None = None,
) -> str:
    prompt_file = _EXPERT_SYSTEM_PROMPT_FILES.get(expert_name)
    if prompt_file is None:
        raise ValueError(f"Unknown expert prompt: {expert_name}")

    sections = [
        load_prompt_asset(
            _PROMPTS_DIR,
            prompt_file,
            preserve_trailing_newline=True,
            empty_error_label="Audit prompt",
        ).rstrip(),
    ]
    few_shots = load_optional_prompt_asset(
        _PROMPTS_DIR,
        _EXPERT_FEW_SHOT_FILES.get(expert_name, ""),
        preserve_trailing_newline=True,
        empty_error_label="Audit prompt",
    ).rstrip()
    if few_shots:
        sections.append(few_shots)

    return append_extra_prompt("\n\n".join(sections), extra_prompt)


def build_parent_system_prompt(
    *,
    current_file: str,
    current_file_facts: dict[str, FunctionSummary],
    extra_prompt: str | None = None,
) -> str:
    current_file_fact_text = _format_current_file_facts(current_file_facts)

    facts_block = (
        "## Context Facts\n\n"
        f"Current file: {current_file}\n\n"
        "Current file fact summaries:\n"
        f"{current_file_fact_text}"
    )

    sections = [
        load_prompt_asset(
            _PROMPTS_DIR,
            _SUPERVISOR_SYSTEM_PROMPT_FILE,
            preserve_trailing_newline=True,
            empty_error_label="Audit prompt",
        ).rstrip(),
        facts_block,
    ]
    few_shots = load_optional_prompt_asset(
        _PROMPTS_DIR,
        _SUPERVISOR_FEW_SHOTS_FILE,
        preserve_trailing_newline=True,
        empty_error_label="Audit prompt",
    ).rstrip()
    if few_shots:
        sections.append(few_shots)

    return append_extra_prompt("\n\n".join(sections), extra_prompt)


def build_parent_audit_prompt(
    *,
    current_file: str,
    extra_prompt: str | None = None,
) -> str:
    prompt = load_prompt_asset(
        _PROMPTS_DIR,
        _SUPERVISOR_USER_PROMPT_FILE,
        preserve_trailing_newline=True,
        empty_error_label="Audit prompt",
    ).format(current_file=current_file)
    return append_extra_prompt(prompt, extra_prompt)


def build_execution_path_consistency_system_prompt(
    *,
    aggregate_facts_document: AggregateFactsDocument,
    extra_prompt: str | None = None,
) -> str:
    file_list = _format_scope_file_list(sorted(aggregate_facts_document.files))
    system_prompt = (
        f"{load_prompt_asset(_PROMPTS_DIR, _EXECUTION_PATH_CONSISTENCY_SYSTEM_PROMPT_FILE, preserve_trailing_newline=True, empty_error_label='Audit prompt')}\n\n"
        "## Scope\n\n"
        f"Project root: {aggregate_facts_document.project_root}\n\n"
        "In-scope production Rust files:\n"
        f"{file_list}"
    )
    return append_extra_prompt(system_prompt, extra_prompt)


def build_execution_path_consistency_audit_prompt(
    *,
    extra_prompt: str | None = None,
) -> str:
    prompt = load_prompt_asset(
        _PROMPTS_DIR,
        _EXECUTION_PATH_CONSISTENCY_USER_PROMPT_FILE,
        preserve_trailing_newline=True,
        empty_error_label="Audit prompt",
    )
    return append_extra_prompt(prompt, extra_prompt)


def _format_current_file_facts(
    current_file_facts: dict[str, FunctionSummary],
) -> str:
    if not current_file_facts:
        return "No extracted function facts for this file."

    return "\n".join(
        _format_fact_line(function_key, summary)
        for function_key, summary in current_file_facts.items()
    )


def _format_fact_line(function_key: str, summary: FunctionSummary) -> str:
    fields = present_summary_fields(summary)
    if not fields:
        return f"- {function_key}: observed with no extracted categories."

    rendered_fields = " ".join(
        f"{field_name}={field_value}" for field_name, field_value in fields
    )
    return f"- {function_key}: {rendered_fields}"


def _format_scope_file_list(relative_paths: list[str]) -> str:
    if not relative_paths:
        return "- No in-scope files."
    return "\n".join(f"- {relative_path}" for relative_path in relative_paths)
