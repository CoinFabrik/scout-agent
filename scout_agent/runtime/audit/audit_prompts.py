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
            content="Audit the current file: src/token.rs\n\nInstructions: Audit only the current file. Delegate to specialists for deep dives. Return only concrete findings."
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "src/token.rs"},
                    "id": "call_sup_1",
                }
            ],
        ),
        ToolMessage(
            tool_call_id="call_sup_1",
            content="pub fn transfer(e: Env, from: Address, to: Address, amount: i128) {\n    from.require_auth();\n    let balance = get_balance(&e, &from);\n    if amount > balance { panic!(\"insufficient balance\"); }\n    set_balance(&e, &from, balance - amount);\n    set_balance(&e, &to, get_balance(&e, &to) + amount);\n}",
        ),
        AIMessage(
            content="I have analyzed src/token.rs. The `transfer` function lacks a check for zero `amount`, which is a general arithmetic concern and out of scope. I also see a potential reentrancy risk if `set_balance` emitted events before state changes, but that is also out of scope. I found no patterns matching the three specialist triggers. I will not delegate any tasks.",
        ),
        HumanMessage(
            content="Audit the current file: src/vault.rs\n\nInstructions: Audit only the current file. Delegate to specialists for deep dives. Return only concrete findings."
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"file_path": "src/vault.rs"},
                    "id": "call_sup_2",
                }
            ],
        ),
        ToolMessage(
            tool_call_id="call_sup_2",
            content="pub fn deposit(e: Env, user: Address, amount: Vec<i128>) {\n    let mut total = 0;\n    for a in amount.iter() {\n        total += a;\n    }\n    let mut balance = balances.get(user).unwrap_or(0);\n    balances.set(user, balance + total);\n}",
        ),
        AIMessage(
            content="In src/vault.rs, the `deposit` function takes a `Vec<i128>` and sums the elements without checking if the same element (or index) is processed multiple times, which might be fine for values but suspicious for collections. This matches the `collection_validation` trigger.",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "subagent_type": "collection_validation",
                        "description": "Specialist Brief:\n- Target: `deposit` function in src/vault.rs\n- Pattern: Input `Vec` used in a loop for balance calculation.\n- Question: Verify if duplicate elements in the vector could cause inflated balance updates.",
                    },
                    "id": "call_sup_3",
                }
            ],
        ),
        ToolMessage(
            tool_call_id="call_sup_3",
            content='{"status": "VULNERABLE", "finding": {"pattern": "Unvalidated collection input", "severity": "MEDIUM", "location": "src/vault.rs:3", "description": "...", "evidence": "..."}}',
        ),
        AIMessage(
            content="",
        ),
    ]


def get_expert_few_shots(expert_name: str) -> list[Any]:
    if expert_name == "sentinel_logic":
        return [
            HumanMessage(
                content="Specialist Brief:\n- Target: `claim_reward` in src/rewards.rs\n- Pattern: `reward_id` handling.\n- Question: Check if sentinel values for uninitialized states are handled."
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "grep",
                        "args": {"pattern": "struct.*Reward", "path": "src/storage.rs"},
                        "id": "call_exp_0",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_0",
                content="src/storage.rs:45: struct RewardState {\nsrc/storage.rs:46:     reward_id: u32,",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_code_chunk",
                        "args": {"file": "src/rewards.rs", "start_line": 1},
                        "id": "call_exp_1",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_1",
                content="   1: pub fn claim_reward(e: Env, reward_id: u32) {\n   2:     if reward_id == 0 { panic!(\"invalid id\"); }\n   3:     let reward = rewards.get(reward_id).unwrap();\n   4: }",
            ),
            AIMessage(
                content="I grepped for the reward structure and found its definition in storage.rs. Then I checked the handling of `reward_id` in rewards.rs. It is checked against `0` at line 2. The sentinel handling for `reward_id` is correct. The logic is safe.",
            ),
        ]
    elif expert_name == "collection_validation":
        return [
            HumanMessage(
                content="Specialist Brief:\n- Target: `deposit` in src/vault.rs\n- Pattern: `amount` Vec summation.\n- Question: Verify if duplicate elements cause issues."
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "grep",
                        "args": {"pattern": "Vec<.*>", "path": "src/vault.rs"},
                        "id": "call_exp_col_0",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_col_0",
                content="src/vault.rs:1: pub fn deposit(e: Env, user: Address, amount: Vec<i128>) {",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_code_chunk",
                        "args": {"file": "src/vault.rs", "start_line": 1},
                        "id": "call_exp_2",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_2",
                content="   1: pub fn deposit(e: Env, user: Address, amount: Vec<i128>) {\n   2:     let mut total = 0;\n   3:     for a in amount.iter() {\n   4:         total += a;\n   5:     }\n   6: }",
            ),
            AIMessage(
                content="I analyzed the loop at line 3. It iterates over `amount` elements. While duplicates would increase the `total`, this is a vector of values, not addresses or unique identifiers. The collection usage is safe for this pattern.",
            ),
        ]
    elif expert_name == "time_state":
        return [
            HumanMessage(
                content="Specialist Brief:\n- Target: `update_pool` in src/rewards.rs\n- Pattern: State update order.\n- Question: Verify if update order relative to time is safe."
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "grep",
                        "args": {"pattern": "fn update_pool", "path": "src/rewards.rs"},
                        "id": "call_exp_time_0",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_time_0",
                content="src/rewards.rs:100: pub fn update_pool(e: Env) {",
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_code_chunk",
                        "args": {"file": "src/rewards.rs", "start_line": 100},
                        "id": "call_exp_3",
                    }
                ],
            ),
            ToolMessage(
                tool_call_id="call_exp_3",
                content=" 100: pub fn update_pool(e: Env) {\n 101:     let mut state = get_state(&e);\n 102:     state.total_rewards += 100;\n 103:     state.last_update = e.ledger().timestamp();\n 104:     set_state(&e, state);\n 105: }",
            ),
            AIMessage(
                content="I checked the order of operations. The `last_update` is set at line 103, *after* rewards are added at line 102. The update order is safe here as it is atomic state change.",
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
