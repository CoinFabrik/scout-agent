from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Set
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from scout_agent.domain.audit import ExpertResult
from scout_agent.runtime.audit.dump import (
    AuditDumpWriter,
    build_preview_text,
    extract_message_text,
)
from scout_agent.runtime.audit.reporting import AuditProgressReporter


@dataclass(frozen=True, slots=True)
class _ToolRunContext:
    expert_name: str | None
    tool_name: str
    target: str
    line_start: int | None
    line_end: int | None
    max_lines: int | None
    offset: int | None
    limit: int | None
    current_file: str
    actor_run_id: str | None
    tool_call_id: str


class RuntimeProgressHandler(BaseCallbackHandler):
    """Forward runtime chain and tool events to the progress reporter."""

    def __init__(
        self,
        *,
        reporter: AuditProgressReporter,
        expert_names: set[str],
        current_file: str,
        dump_writer: AuditDumpWriter | None = None,
    ) -> None:
        self._reporter = reporter
        self._expert_names = expert_names
        self._current_file = current_file
        self._dump_writer = dump_writer
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
        expert_name = _resolve_expert_name(
            expert_names=self._expert_names,
            metadata=metadata,
            serialized=serialized,
        )
        if expert_name not in self._expert_names:
            return

        self._run_to_expert[run_id] = expert_name
        if expert_name in self._active_experts:
            return

        self._active_experts.add(expert_name)
        self._reporter.expert_spawned(expert_name=expert_name)
        if self._dump_writer is not None:
            self._dump_writer.expert_started(
                relative_path=self._current_file,
                expert_name=expert_name,
                actor_run_id=str(run_id),
            )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        _ = (parent_run_id, tags, kwargs)
        expert_name = self._run_to_expert.get(run_id)
        if expert_name is not None and self._dump_writer is not None:
            result = _extract_expert_result(outputs)
            if result is not None:
                final_message_text = _extract_final_message_text(outputs)
                self._dump_writer.expert_result(
                    relative_path=self._current_file,
                    expert_name=expert_name,
                    status=result.status,
                    finding=result.finding,
                    actor_run_id=str(run_id),
                    final_message_text=final_message_text,
                    final_message_truncated=False if final_message_text else None,
                )
            self._dump_writer.expert_completed(
                relative_path=self._current_file,
                expert_name=expert_name,
                actor_run_id=str(run_id),
            )
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
        expert_name = _resolve_expert_name(
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
        if expert_name == "unknown_subagent" and not _is_expert_tool(tool_name):
            expert_name = None

        safe_inputs = inputs if isinstance(inputs, dict) else {}
        target = _resolve_tool_target(
            safe_inputs,
            fallback=self._current_file,
        )
        line_start, line_end = _resolve_line_range(safe_inputs)
        max_lines = _coerce_optional_int(safe_inputs.get("max_lines"))
        offset = _coerce_optional_int(safe_inputs.get("offset"))
        limit = _coerce_optional_int(safe_inputs.get("limit"))
        current_file = _ensure_leading_slash(self._current_file)
        actor_run_id = str(parent_run_id) if parent_run_id is not None else None

        self._tool_runs[run_id] = _ToolRunContext(
            expert_name=expert_name,
            tool_name=tool_name,
            target=target,
            line_start=line_start,
            line_end=line_end,
            max_lines=max_lines,
            offset=offset,
            limit=limit,
            current_file=current_file,
            actor_run_id=actor_run_id,
            tool_call_id=str(run_id),
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
            self._report_tool_denied(
                context=context,
                reason=output.removeprefix("Error:").strip(),
            )
            return

        preview_text, preview_truncated = build_preview_text(output)
        self._reporter.tool_used(
            tool_name=context.tool_name,
            target=context.target,
            expert_name=context.expert_name,
            line_start=context.line_start,
            line_end=context.line_end,
            offset=context.offset,
            limit=context.limit,
        )
        if self._dump_writer is not None:
            self._report_tool_used_to_dump(
                context=context,
                preview_text=preview_text,
                preview_truncated=preview_truncated,
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
            expert_name=context.expert_name,
        )
        if self._dump_writer is not None:
            self._report_tool_denied_to_dump(
                context=context,
                reason=reason,
            )

    def _report_tool_used_to_dump(
        self,
        *,
        context: _ToolRunContext,
        preview_text: str | None,
        preview_truncated: bool | None,
    ) -> None:
        if context.expert_name is None:
            self._dump_writer.supervisor_tool_used(
                relative_path=self._current_file,
                tool_name=context.tool_name,
                target=context.target,
                line_start=context.line_start,
                line_end=context.line_end,
                max_lines=context.max_lines,
                offset=context.offset,
                limit=context.limit,
                preview_text=preview_text,
                preview_truncated=preview_truncated,
                tool_call_id=context.tool_call_id,
            )
            return

        self._dump_writer.expert_tool_used(
            relative_path=self._current_file,
            expert_name=context.expert_name,
            tool_name=context.tool_name,
            target=context.target,
            line_start=context.line_start,
            line_end=context.line_end,
            max_lines=context.max_lines,
            offset=context.offset,
            limit=context.limit,
            preview_text=preview_text,
            preview_truncated=preview_truncated,
            actor_run_id=context.actor_run_id,
            tool_call_id=context.tool_call_id,
        )

    def _report_tool_denied_to_dump(
        self,
        *,
        context: _ToolRunContext,
        reason: str,
    ) -> None:
        if context.expert_name is None:
            self._dump_writer.supervisor_tool_denied(
                relative_path=self._current_file,
                tool_name=context.tool_name,
                target=context.target,
                current_file=context.current_file,
                reason=reason,
                tool_call_id=context.tool_call_id,
            )
            return

        self._dump_writer.expert_tool_denied(
            relative_path=self._current_file,
            expert_name=context.expert_name,
            tool_name=context.tool_name,
            target=context.target,
            current_file=context.current_file,
            reason=reason,
            actor_run_id=context.actor_run_id,
            tool_call_id=context.tool_call_id,
        )


def _resolve_tool_target(inputs: dict[str, Any], *, fallback: str) -> str:
    primary_target = inputs.get("file")
    if isinstance(primary_target, str) and primary_target.strip():
        return primary_target.strip()

    secondary_target = inputs.get("file_path")
    if isinstance(secondary_target, str) and secondary_target.strip():
        return secondary_target.strip()

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
        if candidate in expert_names:
            return candidate

    if parent_run_id is not None and run_to_expert is not None:
        parent_candidate = run_to_expert.get(parent_run_id)
        if parent_candidate in expert_names:
            return parent_candidate

    return "unknown_subagent"


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
    return tool_name == "read_code_chunk"


def _tool_error_reason(error: BaseException) -> str:
    message = str(error).strip()
    if message:
        return message
    return type(error).__name__


def _extract_expert_result(outputs: dict[str, Any]) -> ExpertResult | None:
    candidates: list[object] = []
    structured = outputs.get("structured_response")
    if structured is not None:
        candidates.append(structured)

    output_value = outputs.get("output")
    if output_value is not None:
        candidates.append(output_value)

    candidates.append(outputs)

    for candidate in candidates:
        if isinstance(candidate, ExpertResult):
            return candidate
        if isinstance(candidate, dict):
            try:
                return ExpertResult.model_validate(candidate)
            except ValueError:
                continue
        if isinstance(candidate, str):
            try:
                return ExpertResult.model_validate_json(candidate)
            except ValueError:
                continue
    return None


def _extract_final_message_text(outputs: dict[str, Any]) -> str | None:
    messages = outputs.get("messages")
    if isinstance(messages, list) and messages:
        extracted = extract_message_text(messages[-1])
        if extracted:
            return extracted

    output_value = outputs.get("output")
    if isinstance(output_value, str):
        extracted = extract_message_text(output_value)
        if extracted:
            return extracted
    if hasattr(output_value, "content"):
        extracted = extract_message_text(output_value)
        if extracted:
            return extracted

    return None
