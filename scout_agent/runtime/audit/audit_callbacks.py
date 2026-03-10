from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from scout_agent.runtime.audit.reporting import PlainAuditProgressReporter


@dataclass(frozen=True, slots=True)
class _ToolRunContext:
    expert_name: str | None
    tool_name: str
    target: str
    line_start: int | None
    line_end: int | None
    offset: int | None
    limit: int | None
    query: str | None
    current_file: str


class RuntimeProgressHandler(BaseCallbackHandler):
    """Forward runtime chain and tool events to the progress reporter."""

    def __init__(
        self,
        *,
        reporter: PlainAuditProgressReporter,
        expert_names: set[str],
        current_file: str,
    ) -> None:
        self._reporter = reporter
        self._expert_names = expert_names
        self._current_file = current_file
        self._active_experts: set[str] = set()
        self._run_to_expert: dict[UUID, str] = {}
        self._tool_runs: dict[UUID, _ToolRunContext] = {}

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        expert_name = (metadata or {}).get("lc_agent_name", "unknown_subagent")
        if expert_name not in self._expert_names:
            return

        self._run_to_expert[run_id] = expert_name
        if expert_name in self._active_experts:
            return

        self._active_experts.add(expert_name)
        self._reporter.expert_spawned(expert_name=expert_name)

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (outputs, parent_run_id, tags, kwargs)
        self._finish_expert_run(run_id)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (error, parent_run_id, tags, kwargs)
        self._finish_expert_run(run_id)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        expert_name = (metadata or {}).get("lc_agent_name", "unknown_subagent")
        tool_name = serialized.get("name", "unknown_tool")

        safe_inputs = inputs if isinstance(inputs, dict) else {}
        target = _resolve_tool_target(
            safe_inputs,
            fallback=self._current_file,
        )
        line_start, line_end = _resolve_line_range(safe_inputs)
        offset = _coerce_optional_int(safe_inputs.get("offset"))
        limit = _coerce_optional_int(safe_inputs.get("limit"))
        query = _resolve_tool_query(tool_name, safe_inputs)
        current_file = _ensure_leading_slash(self._current_file)

        self._tool_runs[run_id] = _ToolRunContext(
            expert_name=expert_name,
            tool_name=tool_name,
            target=target,
            line_start=line_start,
            line_end=line_end,
            offset=offset,
            limit=limit,
            query=query,
            current_file=current_file,
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (parent_run_id, kwargs)
        context = self._tool_runs.pop(run_id, None)
        if context is None:
            return

        if isinstance(output, str) and output.startswith("Error:"):
            reason = output.removeprefix("Error:").strip()
            self._reporter.tool_denied(
                tool_name=context.tool_name,
                target=context.target,
                current_file=context.current_file,
                reason=reason,
                expert_name=context.expert_name,
            )
            return

        self._reporter.tool_used(
            tool_name=context.tool_name,
            target=context.target,
            expert_name=context.expert_name,
            line_start=context.line_start,
            line_end=context.line_end,
            offset=context.offset,
            limit=context.limit,
            query=context.query,
        )

    def _finish_expert_run(self, run_id: UUID) -> None:
        expert_name = self._run_to_expert.pop(run_id, None)
        if expert_name is None:
            return

        if expert_name in self._run_to_expert.values():
            return

        self._active_experts.discard(expert_name)


def _resolve_tool_query(tool_name: str, inputs: dict[str, Any]) -> str | None:
    if tool_name == "grep":
        return f'"{inputs.get("pattern", "")}"'
    if tool_name == "task":
        subagent = inputs.get("subagent_type", "unknown")
        description = inputs.get("description", "")
        # Get first 50 chars, replace newlines with spaces for log clarity
        brief = description.replace("\n", " ").strip()
        if len(brief) > 50:
            brief = brief[:47] + "..."
        return f"{subagent} brief={brief}"
    return None


def _resolve_tool_target(inputs: dict[str, Any], *, fallback: str) -> str:
    # Prioritize path for grep/filesystem tools, then file-specific keys
    for key in ["path", "file", "file_path"]:
        target = inputs.get(key)
        if isinstance(target, str) and target.strip():
            return target.strip()

    return fallback


def _resolve_line_range(inputs: dict[str, Any]) -> tuple[int | None, int | None]:
    start_line = _coerce_optional_int(inputs.get("start_line"))
    max_lines = _coerce_optional_int(inputs.get("max_lines"))
    if start_line is None or max_lines is None:
        return None, None
    if start_line < 1 or max_lines < 1:
        return None, None

    return start_line, (start_line + max_lines - 1)


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _ensure_leading_slash(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        return "/"
    if cleaned.startswith("/"):
        return cleaned
    return f"/{cleaned}"
