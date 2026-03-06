from __future__ import annotations

from scout_agent.domain.facts import (
    FunctionSummary,
    file_path_from_function_key,
    present_summary_fields,
)
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt

PARENT_SYSTEM_PROMPT = """You are the supervisor for a Soroban smart-contract audit.

Your sole responsibility is to read and understand the file, then decide which specialist subagents if any are needed to audit it.

You do not produce findings. You do not audit. You only delegate.

## Available Specialist Subagents
- `execution_path_consistency` — conflicting control flows, unreachable branches, or reentrancy across call paths
- `collection_validation` — map/vec access patterns, missing key guards, or unbounded iteration risks
- `time_state` — ledger timestamp or sequence-number dependencies that affect state transitions
- `sentinel_logic` — flag/sentinel value misuse, off-by-one conditions, or boundary invariant violations

## Your Process
1. Read the file thoroughly.
2. Identify which concern areas are actually present in the code.
3. Delegate only to the subagents relevant to what you found.
4. If no specialist is needed, return nothing.

## Rules
- Do not produce audit findings, notes, or summaries.
- Do not use the built-in `general-purpose` subagent.
- Delegate only when the file contains code that genuinely warrants specialist review.
- Do not delegate speculatively or as a default step — if a concern area is absent from the file, skip that subagent entirely.
"""


def build_parent_system_prompt(
    *,
    current_file: str,
    current_file_facts: dict[str, FunctionSummary],
    all_facts: dict[str, FunctionSummary],
    extra_prompt: str | None = None,
) -> str:
    current_file_fact_text = _format_current_file_facts(current_file_facts)
    other_inventory = _format_cross_file_inventory(
        current_file=current_file,
        all_facts=all_facts,
    )

    facts_block = (
        "## Context Facts\n\n"
        f"Current file: {current_file}\n\n"
        "Current file fact summaries:\n"
        f"{current_file_fact_text}\n\n"
        "Cross-file fact inventory:\n"
        f"{other_inventory}"
    )

    full_system_prompt = f"{PARENT_SYSTEM_PROMPT}\n\n{facts_block}"
    return append_extra_prompt(full_system_prompt, extra_prompt)


def build_parent_audit_prompt(
    *,
    current_file: str,
    extra_prompt: str | None = None,
) -> str:
    prompt = (
        f"Audit the current file: {current_file}\n\n"
        "Instructions:\n"
        "- Audit only the current file.\n"
        "- Use built-in file tools only for the current file when needed.\n"
        "- Delegate to the four specialist subagents only when deeper review is needed.\n"
        "- If you call a specialist, include the current file path, relevant facts, and the exact concern.\n"
        "- Return only deduped concrete findings in the structured response.\n"
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


def _format_cross_file_inventory(
    *,
    current_file: str,
    all_facts: dict[str, FunctionSummary],
) -> str:
    lines = [
        _format_fact_line(function_key, summary)
        for function_key, summary in all_facts.items()
        if file_path_from_function_key(function_key) != current_file
    ]
    return "\n".join(lines) if lines else "- None."


def _format_fact_line(function_key: str, summary: FunctionSummary) -> str:
    fields = present_summary_fields(summary)
    if not fields:
        return f"- {function_key}: observed with no extracted categories."

    rendered_fields = " ".join(
        f"{field_name}={field_value}" for field_name, field_value in fields
    )
    return f"- {function_key}: {rendered_fields}"
