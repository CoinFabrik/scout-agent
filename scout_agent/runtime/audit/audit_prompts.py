from __future__ import annotations

from typing import Any
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from scout_agent.domain.facts import (
    FunctionSummary,
    file_path_from_function_key,
    present_summary_fields,
)
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt

PARENT_SYSTEM_PROMPT = """You are the supervisor for a Soroban smart-contract audit.

Your sole responsibility is to read and understand the file, then decide which specialist subagents if any are needed to audit it.

**CRITICAL RULES:**
1. **ROUTER ONLY:** You have ZERO authority to produce findings yourself. You do not audit. You only delegate.
2. **NO GENERAL FINDINGS:** You MUST NOT report general vulnerabilities like Reentrancy, Division by Zero, or Overflow. These are OUT OF SCOPE.
3. **ONLY SPECIALISTS:** You MUST ONLY use the three specialist subagents. Do NOT use any general `task` or `explore` tools.
4. **EVIDENCE-BASED DELEGATION:** Only call a subagent if you identify a CLEAR PATTERN matching its focus area.
5. **FORBIDDEN TOOLS:** The `task` tool is strictly FORBIDDEN. You must only use the specialist subagents provided.

## Available Specialist Subagents
- `collection_validation` — Trigger: Input Vec/Map used in loops or for calculations without explicit duplicate/uniqueness checks.
- `time_state` — Trigger: State updates that depend on ledger time/sequence where the update order is suspicious.
- `sentinel_logic` — Trigger: Use of special values (0, u32::MAX) to represent states without consistent handling across all functions.

## Your Process
1. Read the file thoroughly using the provided tools.
2. Identify if any of the specific "Triggers" above are present.
3. If a trigger is found, call the relevant specialist.
4. When calling a specialist, you MUST provide a "Specialist Brief" with:
   - **Target:** The specific function or line numbers.
   - **Pattern:** Describe the exact suspicious code pattern you found.
   - **Question:** The specific doubt you want the specialist to verify.
5. If no specialists return findings, your final response must be empty.
"""


def get_supervisor_few_shots() -> list[Any]:
    return [
        HumanMessage(
            content="Audit the current file: src/token.rs\n\nInstructions: [standard instructions]"
        ),
        AIMessage(
            content="I have analyzed src/token.rs. I observed that the `transfer` function does not check if the `amount` is zero. However, general arithmetic bugs are out of scope. I found no patterns matching the three specialist triggers. I will not delegate any tasks."
        ),
    ]


def get_expert_few_shots(expert_name: str) -> list[Any]:
    if expert_name == "sentinel_logic":
        return [
            HumanMessage(
                content="Specialist Brief: Check function `claim_reward` at line 120 for sentinel value handling of `reward_id`."
            ),
            AIMessage(
                content="I checked the handling of `reward_id`. It is checked against `u32::MAX` at line 122, which is the correct sentinel value. The logic is safe. I also saw a reentrancy bug at line 125, but I will ignore it as it is out of my scope.",
            ),
        ]
    elif expert_name == "collection_validation":
        return [
            HumanMessage(
                content="Specialist Brief: Check `batch_transfer` at line 50 for duplicate address validation in the `recipients` vector."
            ),
            AIMessage(
                content="I analyzed the loop at line 55. It iterates over `recipients` without checking for duplicates. This is a finding. I also noticed a missing access control check, but I will ignore it as it is out of my scope.",
            ),
        ]
    elif expert_name == "time_state":
        return [
            HumanMessage(
                content="Specialist Brief: Verify if `update_pool` at line 300 correctly updates the `last_reward_timestamp` before modifying the `total_staked`."
            ),
            AIMessage(
                content="I checked the order of operations. The `last_reward_timestamp` is updated *after* the `total_staked` is modified. This is a finding. I also saw a division by zero risk, but I will ignore it as it is out of my scope.",
            ),
        ]
    return []


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
        "- Delegate to the three specialist subagents only when deeper review is needed.\n"
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
