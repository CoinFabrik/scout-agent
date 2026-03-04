from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any, TypedDict

try:
    from deepagents import CompiledSubAgent
except ImportError:

    class CompiledSubAgent(TypedDict):
        name: str
        description: str
        runnable: Any


try:
    from langchain.agents import create_agent
except ImportError:
    create_agent = None

from scout_agent.domain.audit import ExpertResult, ExpertTypeEnum
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.reporting import PlainAuditProgressReporter
from scout_agent.runtime.audit.tools import (
    EXPERT_READ_MAX_LINES,
    read_sanitized_code_chunk,
)

BASE_EXPERT_PROMPT = """You are a specialized smart-contract audit expert.

You receive one isolated audit task from a parent agent.
You have exactly one tool:
- `read_code_chunk(file, start_line=1, max_lines=100)`

Your job:
- evaluate only the delegated concern
- return exactly one ExpertResult as structured output
- return at most one finding

Rules:
- Do not invent code, files, or behavior.
- Do not broaden the scope beyond the delegated concern.
- Use status='VULNERABLE' only when the evidence supports a concrete issue.
- Use status='SAFE' when the delegated concern was checked and no issue was found.
- Use status='NEEDS_INFO' when the provided context is insufficient to conclude.
- If status='VULNERABLE', include exactly one finding.
- If status is 'SAFE' or 'NEEDS_INFO', do not include a finding.
- Keep findings concise and evidence specific.
"""

EXECUTION_PATH_CONSISTENCY_PROMPT = """Focus: execution path consistency.

Investigate whether equivalent or related state mutation paths enforce consistent validation.
Look for missing authorization, pause checks, threshold checks, or equivalent guards.
"""

COLLECTION_VALIDATION_PROMPT = """Focus: collection validation.

Investigate whether vector or array-like inputs require uniqueness validation or safe duplicate handling
before insertion into state or before aggregation.
"""

TIME_STATE_PROMPT = """Focus: time-dependent state.

Investigate whether time-based accrual, settlement, yield, or elapsed-time updates
must occur before modifying rate-driving or balance-driving state.
"""

SENTINEL_LOGIC_PROMPT = """Focus: sentinel logic.

Investigate whether sentinel or special-status values such as u32::MAX, None, or 0
are handled safely by readers, iterators, and processing paths.
"""


def build_expert_subagents(
    *,
    model_name: str,
    llm_mode: str,
    project_root: Path,
    allowed_paths: Collection[str],
    reporter: PlainAuditProgressReporter | None = None,
    extra_prompt: str | None = None,
) -> list[CompiledSubAgent]:
    if create_agent is None:
        raise ValueError(
            "langchain is required for expert subagent creation. "
            "Install project dependencies first."
        )

    model = build_chat_model(model_name, llm_mode)

    return [
        {
            "name": expert_type.value,
            "description": _description_for_expert(expert_type),
            "runnable": _maybe_wrap_runnable_with_logging(
                create_agent(
                    model=model,
                    system_prompt=_system_prompt_for_expert(
                        expert_type,
                        extra_prompt=extra_prompt,
                    ),
                    tools=[
                        _build_read_code_chunk_tool(
                            project_root=project_root,
                            allowed_paths=allowed_paths,
                            expert_name=expert_type.value,
                            reporter=reporter,
                        )
                    ],
                    response_format=ExpertResult,
                    name=expert_type.value,
                ),
                expert_name=expert_type.value,
                reporter=reporter,
            ),
        }
        for expert_type in ExpertTypeEnum
    ]


def _build_read_code_chunk_tool(
    *,
    project_root: Path,
    allowed_paths: Collection[str],
    expert_name: str,
    reporter: PlainAuditProgressReporter | None = None,
) -> Any:
    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file."""

        if reporter is not None:
            reporter.tool_used(
                tool_name="read_code_chunk",
                target=file,
                expert_name=expert_name,
                line_start=start_line,
                line_end=start_line + max_lines - 1,
            )
        try:
            return read_sanitized_code_chunk(
                project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
        except (ValueError, FileNotFoundError) as exc:
            if reporter is not None:
                reporter.tool_denied(
                    tool_name="read_code_chunk",
                    target=file,
                    current_file=file,
                    reason=str(exc),
                    expert_name=expert_name,
                )
            return f"Error: {exc}"

    return read_code_chunk


class _LoggingRunnable:
    def __init__(
        self,
        runnable: Any,
        *,
        expert_name: str,
        reporter: PlainAuditProgressReporter,
    ) -> None:
        self._runnable = runnable
        self._expert_name = expert_name
        self._reporter = reporter

    def _log_spawn(self) -> None:
        self._reporter.expert_spawned(expert_name=self._expert_name)

    def __call__(self, *args, **kwargs):
        self._log_spawn()
        return self._runnable(*args, **kwargs)

    def invoke(self, *args, **kwargs):
        self._log_spawn()
        return self._runnable.invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        self._log_spawn()
        return await self._runnable.ainvoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        self._log_spawn()
        return self._runnable.stream(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        self._log_spawn()
        return await self._runnable.astream(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)


def _maybe_wrap_runnable_with_logging(
    runnable: Any,
    *,
    expert_name: str,
    reporter: PlainAuditProgressReporter | None,
) -> Any:
    if reporter is None:
        return runnable
    return _LoggingRunnable(
        runnable,
        expert_name=expert_name,
        reporter=reporter,
    )


def _description_for_expert(expert_type: ExpertTypeEnum) -> str:
    if expert_type == ExpertTypeEnum.EXECUTION_PATH_CONSISTENCY:
        return "Audit state mutation paths for inconsistent validation or authorization."
    if expert_type == ExpertTypeEnum.COLLECTION_VALIDATION:
        return "Audit vector or array inputs for missing uniqueness or duplicate-safe validation."
    if expert_type == ExpertTypeEnum.TIME_STATE:
        return "Audit time-dependent state transitions and ordering."
    if expert_type == ExpertTypeEnum.SENTINEL_LOGIC:
        return "Audit sentinel and special-status value handling."
    raise ValueError(f"Unsupported expert type: {expert_type}")


def _append_extra_prompt(base_prompt: str, extra_prompt: str | None) -> str:
    if extra_prompt is None or not extra_prompt.strip():
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "Additional audit instructions:\n"
        f"{extra_prompt.strip()}\n"
    )


def _system_prompt_for_expert(
    expert_type: ExpertTypeEnum,
    *,
    extra_prompt: str | None = None,
) -> str:
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

    return _append_extra_prompt("\n\n".join(prompt_parts), extra_prompt)
