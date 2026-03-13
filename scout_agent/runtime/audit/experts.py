from langchain.agents.structured_output import ProviderStrategy

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import CompiledSubAgent, create_deep_agent
from langchain.agents import create_agent

from scout_agent.domain.audit import ExpertResult
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.audit_prompts import build_expert_system_prompt
from scout_agent.runtime.audit.tools import (
    EXPERT_READ_MAX_LINES,
    read_sanitized_code_chunk,
)


@dataclass(frozen=True, slots=True)
class SubagentPromptSpec:
    name: str
    description: str


SUBAGENT_MANIFEST: tuple[SubagentPromptSpec, ...] = (
    SubagentPromptSpec(
        name="collection_validation",
        description="Audit vector or array inputs for missing uniqueness or duplicate-safe validation.",
    ),
    SubagentPromptSpec(
        name="time_state",
        description="Audit time-dependent state transitions and ordering.",
    ),
    SubagentPromptSpec(
        name="sentinel_logic",
        description="Audit sentinel and special-status value handling.",
    ),
)


def build_expert_subagents(
    *,
    model_name: str,
    llm_mode: str,
    project_root: Path,
    allowed_paths: Collection[str],
    recursion_limit: int,
    extra_prompt: str | None = None,
) -> list[CompiledSubAgent]:
    model = build_chat_model(model_name, llm_mode)
    subagents: list[CompiledSubAgent] = []

    # Share tool state across all specialists for this file audit
    shared_tools = [
        _build_read_code_chunk_tool(
            project_root=project_root,
            allowed_paths=allowed_paths,
        ),
        _build_grep_tool(
            project_root=project_root,
            allowed_paths=allowed_paths,
        ),
    ]

    for spec in SUBAGENT_MANIFEST:
        full_prompt = build_expert_system_prompt(
            expert_name=spec.name,
            extra_prompt=extra_prompt,
        )

        # Escape braces for LangChain prompt template interpolation
        escaped_system_prompt = full_prompt.replace("{", "{{").replace("}", "}}")
        runnable = create_agent(
            model=model,
            system_prompt=escaped_system_prompt,
            tools=shared_tools,
            response_format=ProviderStrategy(ExpertResult, strict=True),
            name=spec.name,
        ).with_config({"recursion_limit": recursion_limit})
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
    # State to track call counts and file diversity
    call_counts: dict[str, int] = {}
    read_files: set[str] = set()
    MAX_UNIQUE_FILES = 5

    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = EXPERT_READ_MAX_LINES,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file. Defaults: start_line=1, max_lines=100."""
        requested_file = Path(file.strip()).as_posix()
        call_key = f"{requested_file}:{start_line}"

        # 1. Prevent fishing expeditions
        if requested_file not in read_files and len(read_files) >= MAX_UNIQUE_FILES:
            return (
                f"SCOPE LIMIT REACHED: You have already read {MAX_UNIQUE_FILES} unique files. "
                "You must conclude your analysis with the context you have. Specialist agents "
                "are intended for deep dives into specific logic, not repository-wide exploration."
            )

        # 2. Prevent repetition loops
        count = call_counts.get(call_key, 0)
        if count >= 2:
            return (
                f"REPETITION DETECTED: You have already read {requested_file} starting at line {start_line} multiple times. "
                "To see more code, you MUST increment your `start_line`. If you cannot find a finding "
                "after multiple reads, return 'No findings' and explain why in your thought block."
            )

        try:
            content = read_sanitized_code_chunk(
                project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
            call_counts[call_key] = count + 1
            read_files.add(requested_file)
            return content
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    return read_code_chunk


def _build_grep_tool(
    *,
    project_root: Path,
    allowed_paths: Collection[str],
) -> Any:
    # State to track call counts and repetition
    call_counts = {"total": 0}
    last_pattern: list[str | None] = [None]
    MAX_GREP_CALLS = 5

    def grep(
        pattern: str,
        path: str | None = None,
    ) -> str:
        """
        Search for a regex pattern across all in-scope files.
        Optional 'path' argument restricts the search to a specific file or directory.
        Returns matching lines with their 1-indexed line numbers.
        """
        if call_counts["total"] >= MAX_GREP_CALLS:
            return f"GREP LIMIT REACHED: You have already used grep {MAX_GREP_CALLS} times. Use read_code_chunk for deeper investigation."

        if pattern == last_pattern[0]:
            return "REPETITION DETECTED: You just searched for this pattern. Try a different pattern or use read_code_chunk on one of the results."

        import re

        results = []
        total_matches = 0
        MAX_RESULTS = 20

        # Filter allowed_paths by the requested path if provided
        search_paths = list(allowed_paths)
        if path:
            norm_path = Path(path.strip()).as_posix()
            search_paths = [
                p
                for p in allowed_paths
                if p == norm_path or p.startswith(norm_path + "/")
            ]
            if not search_paths:
                return f"Error: Path '{path}' is not in scope or does not exist."

        try:
            regex = re.compile(pattern)
            for rel_path in search_paths:
                abs_path = project_root / rel_path
                if not abs_path.is_file():
                    continue

                content = abs_path.read_text(encoding="utf-8")
                for i, line in enumerate(content.splitlines()):
                    if regex.search(line):
                        results.append(f"{rel_path}:{i+1}: {line.strip()}")
                        total_matches += 1
                        if total_matches >= MAX_RESULTS:
                            break
                if total_matches >= MAX_RESULTS:
                    results.append("... (too many results, showing first 20)")
                    break

            call_counts["total"] += 1
            last_pattern[0] = pattern

            if not results:
                return f"No matches found for pattern: {pattern}"
            return "\n".join(results)

        except Exception as exc:
            return f"Grep Error: {exc}"

    return grep
