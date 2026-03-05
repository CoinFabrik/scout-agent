from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import CompiledSubAgent
from langchain.agents import create_agent

from scout_agent.domain.audit import ExpertResult, ExpertTypeEnum
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt
from scout_agent.runtime.audit.tools import (
    EXPERT_READ_MAX_LINES,
    read_sanitized_code_chunk,
)

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


@dataclass(frozen=True, slots=True)
class SubagentPromptSpec:
    name: str
    description: str
    system_prompt: str


SUBAGENT_MANIFEST: tuple[SubagentPromptSpec, ...] = (
    SubagentPromptSpec(
        name=ExpertTypeEnum.EXECUTION_PATH_CONSISTENCY.value,
        description="Audit state mutation paths for inconsistent validation or authorization.",
        system_prompt=EXECUTION_PATH_CONSISTENCY_PROMPT,
    ),
    SubagentPromptSpec(
        name=ExpertTypeEnum.COLLECTION_VALIDATION.value,
        description="Audit vector or array inputs for missing uniqueness or duplicate-safe validation.",
        system_prompt=COLLECTION_VALIDATION_PROMPT,
    ),
    SubagentPromptSpec(
        name=ExpertTypeEnum.TIME_STATE.value,
        description="Audit time-dependent state transitions and ordering.",
        system_prompt=TIME_STATE_PROMPT,
    ),
    SubagentPromptSpec(
        name=ExpertTypeEnum.SENTINEL_LOGIC.value,
        description="Audit sentinel and special-status value handling.",
        system_prompt=SENTINEL_LOGIC_PROMPT,
    ),
)


def build_expert_subagents(
    *,
    model_name: str,
    llm_mode: str,
    project_root: Path,
    allowed_paths: Collection[str],
    extra_prompt: str | None = None,
) -> list[CompiledSubAgent]:
    model = build_chat_model(model_name, llm_mode)
    subagents: list[CompiledSubAgent] = []

    for spec in SUBAGENT_MANIFEST:
        system_prompt = append_extra_prompt(spec.system_prompt, extra_prompt)
        runnable = create_agent(
            model=model,
            system_prompt=system_prompt,
            tools=[
                _build_read_code_chunk_tool(
                    project_root=project_root,
                    allowed_paths=allowed_paths,
                )
            ],
            response_format=ExpertResult,
            name=spec.name,
        )
        subagents.append(
            {
                "name": spec.name,
                "description": spec.description,
                "runnable": runnable,
            }
        )

    return subagents


def _build_read_code_chunk_tool(
    *,
    project_root: Path,
    allowed_paths: Collection[str],
) -> Any:
    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file."""
        try:
            return read_sanitized_code_chunk(
                project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    return read_code_chunk
