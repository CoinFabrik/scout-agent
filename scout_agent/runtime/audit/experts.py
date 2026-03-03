from __future__ import annotations

from pathlib import Path
from typing import Collection

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from scout_agent.domain.audit import Delegation, ExpertResult, ExpertTypeEnum
from scout_agent.domain.facts import FileFacts, FunctionFacts
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.tools import expert_read_code

MAX_TOOL_ITERATIONS = 5

READ_CODE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_code",
        "description": (
            "Read up to 100 lines of sanitized source code from an in-scope file. "
            "Use this when the initial code snapshot is insufficient to evaluate "
            "the delegated concern. You may read any file that is in scope for the audit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Relative path to the file (e.g. 'src/lib.rs').",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read (1-indexed). Defaults to 1.",
                    "default": 1,
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to return (1-100). Defaults to 100.",
                    "default": 100,
                },
            },
            "required": ["file"],
        },
    },
}

BASE_EXPERT_PROMPT = """You are a specialized smart-contract audit expert.

You receive:
- one narrow delegation from the Supervisor
- the semantic facts already extracted for the target file
- a focused code snapshot prepared for this investigation
- optional cross-file search results

You have access to a `read_code` tool that lets you read up to 100 lines at a time from any in-scope file.
Use it when the initial code snapshot is insufficient to evaluate the delegated concern.
Only use the tool when necessary — prefer working with the provided snapshot when it is enough.

Your job:
- evaluate only the delegated concern
- return exactly one ExpertResult
- return at most one finding

Rules:
- Do not invent code, files, or behavior.
- Do not broaden the scope beyond the delegation.
- Use status='VULNERABLE' only when the evidence supports a concrete issue.
- Use status='SAFE' when the delegated concern was checked and no issue was found.
- Use status='NEEDS_INFO' when the provided context is insufficient to conclude.
- If status='VULNERABLE', include exactly one finding.
- If status is 'SAFE' or 'NEEDS_INFO', do not include a finding.
- Keep descriptions concise and evidence specific.
"""

EXECUTION_PATH_CONSISTENCY_PROMPT = """Focus: execution path consistency.

Investigate whether equivalent or related state mutation paths enforce consistent validation.
This includes mutation backtracking:
- identify the state mutation in question
- compare with other mutation paths if search results provide them
- check for missing authorization, pause checks, threshold checks, or equivalent guards
"""

COLLECTION_VALIDATION_PROMPT = """Focus: collection validation.

Investigate whether vector or array-like inputs require explicit uniqueness validation
before insertion into state or before mathematical aggregation.
Look for duplicate-sensitive logic such as vote counting, aggregation, accumulation,
or repeated insertion of user-controlled elements.
"""

TIME_STATE_PROMPT = """Focus: time-dependent state.

Investigate whether time-based accrual, settlement, yield, or elapsed-time updates
must occur before modifying rate-driving or balance-driving state.
Look for timestamp-driven logic and ordering hazards.
"""

SENTINEL_LOGIC_PROMPT = """Focus: sentinel logic.

Investigate whether sentinel or special-status values such as u32::MAX, None, or 0
are handled safely by readers, iterators, and processing paths.
Look for missing branches, unsafe iteration, or logic that accidentally treats a sentinel
as ordinary business data.
"""


def run_expert_analysis(
    *,
    delegation: Delegation,
    target_file_facts: FileFacts,
    code_snapshot: str,
    model_name: str,
    llm_mode: str,
    search_results: str | None = None,
    project_root: Path,
    allowed_paths: Collection[str],
) -> ExpertResult:
    model = build_chat_model(model_name, llm_mode)

    messages: list[BaseMessage] = build_expert_messages(
        delegation=delegation,
        target_file_facts=target_file_facts,
        code_snapshot=code_snapshot,
        search_results=search_results,
    )

    tool_model = model.bind_tools(
        [READ_CODE_TOOL_SCHEMA],
        tool_choice="auto",
    )

    for _ in range(MAX_TOOL_ITERATIONS):
        response = tool_model.invoke(messages)
        messages.append(response)

        if not isinstance(response, AIMessage) or not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            tool_result = _execute_read_code_tool(
                tool_call=tool_call,
                project_root=project_root,
                allowed_paths=allowed_paths,
            )
            messages.append(
                ToolMessage(
                    content=tool_result,
                    tool_call_id=tool_call["id"],
                )
            )

    structured_model = model.with_structured_output(
        ExpertResult,
        method="json_schema",
    )
    final_response = structured_model.invoke(messages)

    if isinstance(final_response, ExpertResult):
        return final_response

    return ExpertResult.model_validate(final_response)


def _execute_read_code_tool(
    *,
    tool_call: dict,
    project_root: Path,
    allowed_paths: Collection[str],
) -> str:
    if tool_call["name"] != "read_code":
        return f"Unknown tool: {tool_call['name']}"

    args = tool_call.get("args", {})
    file_path = args.get("file", "")
    start_line = args.get("start_line", 1)
    max_lines = args.get("max_lines", 100)

    try:
        return expert_read_code(
            project_root,
            file_path,
            allowed_paths=allowed_paths,
            start_line=start_line,
            max_lines=max_lines,
        )
    except (ValueError, FileNotFoundError) as exc:
        return f"Error: {exc}"


def build_expert_messages(
    *,
    delegation: Delegation,
    target_file_facts: FileFacts,
    code_snapshot: str,
    search_results: str | None = None,
) -> list[BaseMessage]:
    user_prompt = (
        f"Expert type: {delegation.expert_type.value}\n"
        f"Target file: {delegation.target_file}\n"
        f"Delegation reasoning: {delegation.reasoning}\n"
        f"Context snippet: {delegation.context_snippet}\n\n"
        "Function facts for target file:\n"
        f"{_format_file_facts(target_file_facts)}\n\n"
        "Focused code snapshot:\n"
        f"{code_snapshot}\n\n"
        "Cross-file search results:\n"
        f"{_format_search_results(search_results)}\n"
    )

    return [
        SystemMessage(content=_system_prompt_for_expert(delegation.expert_type)),
        HumanMessage(content=user_prompt),
    ]


def _system_prompt_for_expert(expert_type: ExpertTypeEnum) -> str:
    prompt_parts = [BASE_EXPERT_PROMPT]

    if expert_type == ExpertTypeEnum.EXECUTION_PATH_CONSISTENCY:
        prompt_parts.append(EXECUTION_PATH_CONSISTENCY_PROMPT)
    elif expert_type == ExpertTypeEnum.COLLECTION_VALIDATION:
        prompt_parts.append(COLLECTION_VALIDATION_PROMPT)
    elif expert_type == ExpertTypeEnum.TIME_STATE:
        prompt_parts.append(TIME_STATE_PROMPT)
    elif expert_type == ExpertTypeEnum.SENTINEL_LOGIC:
        prompt_parts.append(SENTINEL_LOGIC_PROMPT)
    else:
        raise ValueError(f"Unsupported expert type: {expert_type}")

    return "\n\n".join(prompt_parts)


def _format_file_facts(file_facts: FileFacts) -> str:
    if not file_facts.functions:
        return "- No functions discovered in this file."

    return "\n".join(
        _format_function_facts(function) for function in file_facts.functions
    )


def _format_function_facts(function: FunctionFacts) -> str:
    impl_part = f", impl_target={function.impl_target}" if function.impl_target else ""
    return (
        f"- function_id={function.function_id}, "
        f"name={function.name}, "
        f"kind={function.kind}, "
        f"visibility={function.visibility}, "
        f"lines={function.line_start}-{function.line_end}"
        f"{impl_part}, "
        f"signature={function.signature}, "
        f"authorization={function.facts.authorization.status}, "
        f"vector_parameters={function.facts.vector_parameters.status}, "
        f"time_dependent_state={function.facts.time_dependent_state.status}, "
        f"sentinel_values={function.facts.sentinel_values.status}"
    )


def _format_search_results(search_results: str | None) -> str:
    if search_results is None or not search_results.strip():
        return "No cross-file search results provided."
    return search_results.strip()
