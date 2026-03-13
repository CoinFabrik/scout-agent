from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from scout_agent.domain.audit import Finding
from scout_agent.domain.facts import (
    AggregateFactsDocument,
    FunctionSummary,
    present_summary_fields,
)
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SUPERVISOR_SYSTEM_PROMPT_FILE = "audit/supervisor/system.md"
_SUPERVISOR_USER_PROMPT_FILE = "audit/supervisor/user.md"
_EXECUTION_PATH_CONSISTENCY_SYSTEM_PROMPT_FILE = (
    "audit/execution_path_consistency/system.md"
)
_EXECUTION_PATH_CONSISTENCY_USER_PROMPT_FILE = (
    "audit/execution_path_consistency/user.md"
)
_FINAL_DEDUP_SYSTEM_PROMPT_FILE = "audit/final_dedup/system.md"
_FINAL_DEDUP_USER_PROMPT_FILE = "audit/final_dedup/user.md"
_EXPERT_SYSTEM_PROMPT_FILES = {
    "collection_validation": "audit/experts/collection_validation/system.md",
    "time_state": "audit/experts/time_state/system.md",
    "sentinel_logic": "audit/experts/sentinel_logic/system.md",
}


@cache
def _load_prompt_asset(
    file_name: str,
    *,
    preserve_trailing_newline: bool = False,
) -> str:
    prompt_path = _PROMPTS_DIR / file_name
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not preserve_trailing_newline:
        prompt_text = prompt_text.removesuffix("\n")
    if not prompt_text.strip():
        raise ValueError(f"Audit prompt is empty: {prompt_path}")
    return prompt_text


def get_supervisor_few_shots() -> list[Any]:
    return [*_supervisor_example_1(), *_supervisor_example_2()]


def get_expert_few_shots(expert_name: str) -> list[Any]:
    if expert_name == "sentinel_logic":
        return _sentinel_logic_example()
    if expert_name == "collection_validation":
        return _collection_validation_example()
    if expert_name == "time_state":
        return _time_state_example()
    return []


def build_expert_system_prompt(
    *,
    expert_name: str,
    extra_prompt: str | None = None,
) -> str:
    prompt_file = _EXPERT_SYSTEM_PROMPT_FILES.get(expert_name)
    if prompt_file is None:
        raise ValueError(f"Unknown expert prompt: {expert_name}")
    system_prompt = append_extra_prompt(
        _load_prompt_asset(prompt_file, preserve_trailing_newline=True),
        extra_prompt,
    )
    return f"{system_prompt}{_render_example_session(get_expert_few_shots(expert_name))}"


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

    full_system_prompt = (
        f"{_load_prompt_asset(_SUPERVISOR_SYSTEM_PROMPT_FILE, preserve_trailing_newline=True)}\n\n"
        f"{facts_block}"
    )
    return append_extra_prompt(full_system_prompt, extra_prompt)


def build_parent_audit_prompt(
    *,
    current_file: str,
    extra_prompt: str | None = None,
) -> str:
    prompt = _load_prompt_asset(
        _SUPERVISOR_USER_PROMPT_FILE,
        preserve_trailing_newline=True,
    ).format(current_file=current_file)
    return append_extra_prompt(prompt, extra_prompt)


def build_execution_path_consistency_system_prompt(
    *,
    aggregate_facts_document: AggregateFactsDocument,
    extra_prompt: str | None = None,
) -> str:
    file_list = _format_scope_file_list(sorted(aggregate_facts_document.files))
    system_prompt = (
        f"{_load_prompt_asset(_EXECUTION_PATH_CONSISTENCY_SYSTEM_PROMPT_FILE, preserve_trailing_newline=True)}\n\n"
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
    prompt = _load_prompt_asset(
        _EXECUTION_PATH_CONSISTENCY_USER_PROMPT_FILE,
        preserve_trailing_newline=True,
    )
    return append_extra_prompt(prompt, extra_prompt)


def build_final_dedup_system_prompt(
    *,
    extra_prompt: str | None = None,
) -> str:
    prompt = _load_prompt_asset(
        _FINAL_DEDUP_SYSTEM_PROMPT_FILE,
        preserve_trailing_newline=True,
    )
    return append_extra_prompt(prompt, extra_prompt)


def build_final_dedup_user_prompt(
    *,
    findings: list[Finding],
    extra_prompt: str | None = None,
) -> str:
    prompt = _load_prompt_asset(
        _FINAL_DEDUP_USER_PROMPT_FILE,
        preserve_trailing_newline=True,
    ).format(findings_block=_format_findings_for_prompt(findings))
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


def _format_findings_for_prompt(findings: list[Finding]) -> str:
    sections: list[str] = []
    for index, finding in enumerate(findings, start=1):
        sections.extend(
            [
                f"Finding {index}",
                f"pattern: {finding.pattern}",
                f"severity: {finding.severity}",
                f"location: {finding.location}",
                f"description: {finding.description}",
                f"evidence: {finding.evidence}",
                "",
            ]
        )
    return "\n".join(sections).strip()


def _render_example_session(messages: list[Any]) -> str:
    rendered = "\n\n## Example Session\n"
    for message in messages:
        if isinstance(message, HumanMessage):
            rendered += f"\n### USER:\n{message.content}\n"
            continue
        if isinstance(message, AIMessage):
            rendered += "\n### ASSISTANT:\n"
            if message.content:
                rendered += f"{message.content}\n"
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    rendered += (
                        f"Tool Call: {tool_call['name']}({tool_call['args']})\n"
                    )
            continue
        if isinstance(message, ToolMessage):
            rendered += f"\n### TOOL OUTPUT:\n{message.content}\n"
    return rendered


def _supervisor_example_1() -> list[Any]:
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
            content='pub fn transfer(e: Env, from: Address, to: Address, amount: i128) {\n    from.require_auth();\n    let balance = get_balance(&e, &from);\n    if amount > balance { panic!("insufficient balance"); }\n    set_balance(&e, &from, balance - amount);\n    set_balance(&e, &to, get_balance(&e, &to) + amount);\n}',
        ),
        AIMessage(
            content='{"findings":[]}',
        ),
    ]


def _supervisor_example_2() -> list[Any]:
    return [
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
            content='{"status":"VULNERABLE","finding":{"pattern":"Unvalidated collection input","severity":"MEDIUM","location":"src/vault.rs:3","description":"deposit sums every element from the input vector without any uniqueness validation, so duplicate identifiers in a collection-style input would be counted multiple times.","evidence":"src/vault.rs line 3 iterates over every element in amount and line 4 adds each value into total, with no duplicate or uniqueness check before the accumulation."}}',
        ),
        AIMessage(
            content='{"findings":[{"pattern":"Unvalidated collection input","severity":"MEDIUM","location":"src/vault.rs:3","description":"deposit sums every element from the input vector without any uniqueness validation, so duplicate identifiers in a collection-style input would be counted multiple times.","evidence":"src/vault.rs line 3 iterates over every element in amount and line 4 adds each value into total, with no duplicate or uniqueness check before the accumulation."}]}',
        ),
    ]


def _sentinel_logic_example() -> list[Any]:
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
            content="src/storage.rs:45: const UNINITIALIZED_REWARD_ID: u32 = u32::MAX;\nsrc/storage.rs:46: struct RewardState {\nsrc/storage.rs:47:     reward_id: u32,",
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
            content='   1: pub fn claim_reward(e: Env, reward_id: u32) {\n   2:     let reward = rewards.get(reward_id).unwrap();\n   3:     payout_reward(e, reward);\n   4: }',
        ),
        AIMessage(
            content='{"status":"VULNERABLE","finding":{"pattern":"Missing sentinel guard","severity":"MEDIUM","location":"src/rewards.rs:2","description":"claim_reward reads a reward entry without checking whether reward_id is the uninitialized sentinel value.","evidence":"storage.rs defines UNINITIALIZED_REWARD_ID as u32::MAX, but rewards.rs line 2 calls rewards.get(reward_id) directly with no sentinel check before the lookup."}}',
        ),
    ]


def _collection_validation_example() -> list[Any]:
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
            content='{"status":"SAFE"}',
        ),
    ]


def _time_state_example() -> list[Any]:
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
            content=" 100: pub fn update_pool(e: Env) {\n 101:     let mut state = get_state(&e);\n 102:     apply_pending_rewards(&e, &mut state);\n 103:     state.last_update = e.ledger().timestamp();\n 104:     set_state(&e, state);\n 105: }",
        ),
        AIMessage(
            content='{"status":"NEEDS_INFO"}',
        ),
    ]
