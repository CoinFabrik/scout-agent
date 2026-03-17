from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock

from scout_agent.domain.audit import Finding
from scout_agent.runtime.progress import LineProgressSink


def format_audit_started_line(
    *,
    project_root: Path,
    total_files: int,
    model_name: str,
    llm_mode: str,
) -> str:
    return (
        "Starting audit for "
        f"{project_root} with {total_files} file(s) using {model_name} [{llm_mode}]"
    )


def format_audit_file_started_line(
    *,
    index: int,
    total: int,
    current_file: str,
) -> str:
    return f"Auditing {index}/{total}: {current_file}"


def format_audit_finding_verified_line(
    *,
    total_verified_findings: int,
    finding: Finding,
) -> str:
    return (
        "Verified "
        f"{finding.severity} finding #{total_verified_findings}: "
        f"{finding.pattern} at {finding.location}"
    )


def format_audit_file_completed_line(
    *,
    reviewed: int,
    total: int,
    current_file: str,
) -> str:
    return f"Completed {reviewed}/{total}: {current_file}"


def format_audit_file_failed_line(
    *,
    current_file: str,
    error_type: str,
    message: str,
) -> str:
    return f"Failed: {current_file}: {error_type}: {message}"


def format_execution_path_consistency_started_line() -> str:
    return "Starting repo-wide execution_path_consistency audit"


def format_execution_path_consistency_completed_line() -> str:
    return "Completed repo-wide execution_path_consistency audit"


def format_audit_expert_spawned_line(
    *,
    expert_name: str,
) -> str:
    return f"Spawning expert: {expert_name}"


def format_audit_tool_used_line(
    *,
    tool_name: str,
    target: str,
    actor_name: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
    pattern: str | None = None,
) -> str:
    actor = _resolve_actor_name(actor_name)
    parts = [
        f"actor={actor}",
        f"tool={tool_name}",
        f"target={target}",
    ]
    if pattern is not None:
        parts.append(f"pattern={pattern!r}")
    if line_start is not None and line_end is not None:
        parts.append(f"lines={line_start}-{line_end}")
    if offset is not None:
        parts.append(f"offset={offset}")
    if limit is not None:
        parts.append(f"limit={limit}")
    return "Tool used: " + " ".join(parts)


def format_audit_tool_denied_line(
    *,
    tool_name: str,
    target: str,
    current_file: str,
    reason: str,
    actor_name: str | None = None,
    pattern: str | None = None,
) -> str:
    actor = _resolve_actor_name(actor_name)
    parts = [
        f"actor={actor}",
        f"tool={tool_name}",
        f"target={target}",
    ]
    if pattern is not None:
        parts.append(f"pattern={pattern!r}")
    parts.append(f"current={current_file}")
    parts.append(f"reason={reason}")
    return "Tool denied: " + " ".join(parts)


@dataclass(frozen=True, slots=True)
class AuditStatusSnapshot:
    current_file: str = ""
    reviewed: int = 0
    total: int = 0
    verified_findings: int = 0


class AuditProgressReporter:
    def __init__(self, sink: LineProgressSink) -> None:
        self._sink = sink
        self._status = AuditStatusSnapshot()
        self._closed = False
        self._lock = Lock()

    @property
    def status_snapshot(self) -> AuditStatusSnapshot:
        with self._lock:
            return self._status

    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None:
        self._emit(
            format_audit_started_line(
                project_root=project_root,
                total_files=total_files,
                model_name=model_name,
                llm_mode=llm_mode,
            ),
            total=total_files,
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        current_file: str,
    ) -> None:
        _ = index
        self._emit(
            format_audit_file_started_line(
                index=index,
                total=total,
                current_file=current_file,
            ),
            current_file=current_file,
            total=total,
        )

    def finding_verified(
        self,
        *,
        total_verified_findings: int,
        finding: Finding,
    ) -> None:
        self._emit(
            format_audit_finding_verified_line(
                total_verified_findings=total_verified_findings,
                finding=finding,
            ),
            verified_findings=total_verified_findings,
        )

    def execution_path_consistency_started(self) -> None:
        self._emit(format_execution_path_consistency_started_line())

    def execution_path_consistency_completed(self) -> None:
        self._emit(format_execution_path_consistency_completed_line())

    def file_completed(
        self,
        *,
        reviewed: int,
        total: int,
        current_file: str,
    ) -> None:
        self._emit(
            format_audit_file_completed_line(
                reviewed=reviewed,
                total=total,
                current_file=current_file,
            ),
            current_file=current_file,
            reviewed=reviewed,
            total=total,
        )

    def file_failed(
        self,
        *,
        current_file: str,
        error_type: str,
        message: str,
    ) -> None:
        self._emit(
            format_audit_file_failed_line(
                current_file=current_file,
                error_type=error_type,
                message=message,
            ),
            current_file=current_file,
        )

    def expert_spawned(
        self,
        *,
        expert_name: str,
    ) -> None:
        self._emit(format_audit_expert_spawned_line(expert_name=expert_name))

    def tool_used(
        self,
        *,
        tool_name: str,
        target: str,
        actor_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
        pattern: str | None = None,
    ) -> None:
        self._emit(
            format_audit_tool_used_line(
                tool_name=tool_name,
                target=target,
                actor_name=actor_name,
                line_start=line_start,
                line_end=line_end,
                offset=offset,
                limit=limit,
                pattern=pattern,
            )
        )

    def tool_denied(
        self,
        *,
        tool_name: str,
        target: str,
        current_file: str,
        reason: str,
        actor_name: str | None = None,
        pattern: str | None = None,
    ) -> None:
        self._emit(
            format_audit_tool_denied_line(
                tool_name=tool_name,
                target=target,
                current_file=current_file,
                reason=reason,
                actor_name=actor_name,
                pattern=pattern,
            )
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._sink.close()

    def _emit(self, line: str, **status_updates: int | str) -> None:
        with self._lock:
            if self._closed:
                return
            if status_updates:
                self._status = replace(self._status, **status_updates)
        self._sink.emit(line)


def _resolve_actor_name(actor_name: str | None) -> str:
    return actor_name if actor_name is not None else "supervisor"
