from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler

from scout_agent.audit.io.reporting import AuditProgressReporter


@dataclass(frozen=True, slots=True)
class _ToolRunContext:
    expert_name: str | None
    tool_name: str
    target: str
    line_start: int | None
    line_end: int | None
    offset: int | None
    limit: int | None
    pattern: str | None
    current_file: str


class RuntimeProgressHandler(BaseCallbackHandler):
    """Forward runtime chain and tool events to the progress reporter."""

    def __init__(
        self,
        *,
        reporter: AuditProgressReporter,
        expert_names: set[str],
        current_file: str,
        primary_actor_name: str = "supervisor",
    ) -> None:
        self._reporter = reporter
        self._expert_names = expert_names
        self._current_file = current_file
        self._primary_actor_name = primary_actor_name
        self._active_experts: set[str] = set()
        self._run_to_expert: dict[UUID, str] = {}
        self._tool_runs: dict[UUID, _ToolRunContext] = {}

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (inputs, parent_run_id, tags, kwargs)
        expert_name = _resolve_expert_chain_name(
            expert_names=self._expert_names,
            metadata=metadata,
            serialized=serialized,
        )
        if expert_name not in self._expert_names:
            return
        if parent_run_id is not None and parent_run_id in self._run_to_expert:
            return

        self._run_to_expert[run_id] = expert_name
        if expert_name in self._active_experts:
            return

        self._active_experts.add(expert_name)
        self._reporter.expert_spawned(expert_name=expert_name)

    def on_chain_end(
        self,
        outputs: Any,
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
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (input_str, tags, kwargs)
        expert_name = _resolve_tool_expert_name(
            expert_names=self._expert_names,
            metadata=metadata,
            serialized=serialized,
            parent_run_id=parent_run_id,
            run_to_expert=self._run_to_expert,
        )
        tool_name = (
            serialized.get("name", "unknown_tool")
            if isinstance(serialized, dict)
            else "unknown_tool"
        )
        if expert_name == "unknown_subagent":
            expert_name = None

        safe_inputs = inputs if isinstance(inputs, dict) else {}
        target = _resolve_tool_target(safe_inputs, fallback=self._current_file)
        line_start, line_end = _resolve_line_range(safe_inputs)
        offset = _coerce_optional_int(safe_inputs.get("offset"))
        limit = _coerce_optional_int(safe_inputs.get("limit"))
        
        pattern = safe_inputs.get("pattern")
        if not isinstance(pattern, str):
            pattern = None

        current_file = _ensure_leading_slash(self._current_file)

        self._tool_runs[run_id] = _ToolRunContext(
            expert_name=expert_name,
            tool_name=tool_name,
            target=target,
            line_start=line_start,
            line_end=line_end,
            offset=offset,
            limit=limit,
            pattern=pattern,
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

        output_text = _extract_tool_output_text(output)
        if output_text is not None and (
            output_text.startswith("Error:") or output_text.startswith("Error[")
        ):
            self._report_tool_denied(
                context=context,
                reason=output_text.removeprefix("Error:").strip(),
            )
            return

        self._reporter.tool_used(
            tool_name=context.tool_name,
            target=context.target,
            actor_name=(
                context.expert_name
                if context.expert_name is not None
                else self._primary_actor_name
            ),
            line_start=context.line_start,
            line_end=context.line_end,
            offset=context.offset,
            limit=context.limit,
            pattern=context.pattern,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (parent_run_id, kwargs)
        context = self._tool_runs.pop(run_id, None)
        if context is None:
            return

        self._report_tool_denied(
            context=context,
            reason=_tool_error_reason(error),
        )

    def _finish_expert_run(self, run_id: UUID) -> None:
        expert_name = self._run_to_expert.pop(run_id, None)
        if expert_name is None:
            return

        if expert_name in self._run_to_expert.values():
            return

        self._active_experts.discard(expert_name)

    def _report_tool_denied(
        self,
        *,
        context: _ToolRunContext,
        reason: str,
    ) -> None:
        self._reporter.tool_denied(
            tool_name=context.tool_name,
            target=context.target,
            current_file=context.current_file,
            reason=reason,
            actor_name=(
                context.expert_name
                if context.expert_name is not None
                else self._primary_actor_name
            ),
            pattern=context.pattern,
        )


def _resolve_tool_target(inputs: dict[str, Any], *, fallback: str) -> str:
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


def _resolve_expert_name(
    *,
    expert_names: Set[str],
    metadata: Mapping[str, Any] | None,
    serialized: Mapping[str, Any] | None,
    parent_run_id: UUID | None = None,
    run_to_expert: Mapping[UUID, str] | None = None,
) -> str:
    for candidate in (
        _candidate_from_metadata(metadata),
        _candidate_from_serialized_name(serialized),
        _candidate_from_serialized_id(serialized),
    ):
        if candidate is not None and candidate in expert_names:
            return candidate

    if parent_run_id is not None and run_to_expert is not None:
        parent_candidate = run_to_expert.get(parent_run_id)
        if parent_candidate is not None and parent_candidate in expert_names:
            return parent_candidate

    return "unknown_subagent"


def _resolve_expert_chain_name(
    *,
    expert_names: Set[str],
    metadata: Mapping[str, Any] | None,
    serialized: Mapping[str, Any] | None,
) -> str:
    for candidate in (
        _candidate_from_metadata(metadata),
        _candidate_from_serialized_name(serialized),
    ):
        if candidate is not None and candidate in expert_names:
            return candidate

    return "unknown_subagent"


def _resolve_tool_expert_name(
    *,
    expert_names: Set[str],
    metadata: Mapping[str, Any] | None,
    serialized: Mapping[str, Any] | None,
    parent_run_id: UUID | None = None,
    run_to_expert: Mapping[UUID, str] | None = None,
) -> str:
    return _resolve_expert_name(
        expert_names=expert_names,
        metadata=metadata,
        serialized=serialized,
        parent_run_id=parent_run_id,
        run_to_expert=run_to_expert,
    )


def _candidate_from_metadata(metadata: Mapping[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    return _coerce_non_empty_string(metadata.get("lc_agent_name"))


def _candidate_from_serialized_name(
    serialized: Mapping[str, Any] | None,
) -> str | None:
    if serialized is None:
        return None
    return _coerce_non_empty_string(serialized.get("name"))


def _candidate_from_serialized_id(serialized: Mapping[str, Any] | None) -> str | None:
    if serialized is None:
        return None

    raw_id = serialized.get("id")
    if not isinstance(raw_id, list) or not raw_id:
        return None

    trailing = raw_id[-1]
    return _coerce_non_empty_string(trailing)


def _coerce_non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _is_expert_tool(tool_name: str) -> bool:
    return tool_name in {"read_file", "grep"}


def _tool_error_reason(error: BaseException) -> str:
    message = str(error).strip()
    if message:
        return message
    return type(error).__name__


def _extract_tool_output_text(output: Any) -> str | None:
    if isinstance(output, str):
        return output

    if isinstance(output, dict):
        for key in ("content", "output", "text"):
            value = output.get(key)
            text = _coerce_tool_output_text(value)
            if text is not None:
                return text
        return None

    return _coerce_tool_output_text(getattr(output, "content", None))


def _coerce_tool_output_text(value: object) -> str | None:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        joined = "\n".join(part for part in parts if part)
        return joined or None

    return None
