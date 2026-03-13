from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from queue import Queue
from threading import Lock
from typing import Protocol, TextIO

from scout_agent.domain.audit import Finding


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


def format_execution_path_consistency_started_line() -> str:
    return "Starting repo-wide execution_path_consistency audit"


def format_execution_path_consistency_completed_line() -> str:
    return "Completed repo-wide execution_path_consistency audit"


def format_final_dedup_started_line(*, total_findings: int) -> str:
    return f"Starting final LLM dedup for {total_findings} finding(s)"


def format_final_dedup_completed_line(
    *,
    remaining_findings: int,
    removed_count: int,
) -> str:
    return (
        "Completed final LLM dedup: "
        f"{remaining_findings} finding(s) kept, {removed_count} duplicate(s) removed"
    )


def format_final_dedup_skipped_line(*, reason: str) -> str:
    return f"Skipped final LLM dedup: {reason}"


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
) -> str:
    actor = _resolve_actor_name(actor_name)
    parts = [
        f"actor={actor}",
        f"tool={tool_name}",
        f"target={target}",
    ]
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
) -> str:
    actor = _resolve_actor_name(actor_name)
    return (
        "Tool denied: "
        f"actor={actor} tool={tool_name} target={target} "
        f"current={current_file} reason={reason}"
    )


@dataclass(frozen=True, slots=True)
class AuditStatusSnapshot:
    current_file: str = ""
    reviewed: int = 0
    total: int = 0
    verified_findings: int = 0


@dataclass(frozen=True, slots=True)
class AuditProgressEvent:
    line: str | None
    status: AuditStatusSnapshot
    close: bool = False


class AuditProgressSink(Protocol):
    def emit(self, *, line: str, status: AuditStatusSnapshot) -> None: ...

    def close(self, *, status: AuditStatusSnapshot) -> None: ...


class PlainAuditProgressSink:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout
        self._lock = Lock()

    def emit(self, *, line: str, status: AuditStatusSnapshot) -> None:
        _ = status
        with self._lock:
            print(line, file=self._stdout, flush=True)

    def close(self, *, status: AuditStatusSnapshot) -> None:
        _ = status


class QueueAuditProgressSink:
    def __init__(
        self,
        event_queue: Queue[AuditProgressEvent] | None = None,
    ) -> None:
        self._event_queue = event_queue or Queue()

    @property
    def event_queue(self) -> Queue[AuditProgressEvent]:
        return self._event_queue

    def emit(self, *, line: str, status: AuditStatusSnapshot) -> None:
        self._event_queue.put(AuditProgressEvent(line=line, status=status))

    def close(self, *, status: AuditStatusSnapshot) -> None:
        self._event_queue.put(
            AuditProgressEvent(line=None, status=status, close=True)
        )


class AuditProgressReporter:
    def __init__(self, sink: AuditProgressSink) -> None:
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

    def final_dedup_started(self, *, total_findings: int) -> None:
        self._emit(format_final_dedup_started_line(total_findings=total_findings))

    def final_dedup_completed(
        self,
        *,
        remaining_findings: int,
        removed_count: int,
    ) -> None:
        self._emit(
            format_final_dedup_completed_line(
                remaining_findings=remaining_findings,
                removed_count=removed_count,
            ),
            verified_findings=remaining_findings,
        )

    def final_dedup_skipped(self, *, reason: str) -> None:
        self._emit(format_final_dedup_skipped_line(reason=reason))

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
    ) -> None:
        self._emit(
            format_audit_tool_denied_line(
                tool_name=tool_name,
                target=target,
                current_file=current_file,
                reason=reason,
                actor_name=actor_name,
            )
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            status = self._status
        self._sink.close(status=status)

    def _emit(self, line: str, **status_updates: int | str) -> None:
        with self._lock:
            if self._closed:
                return
            if status_updates:
                self._status = replace(self._status, **status_updates)
            status = self._status
        self._sink.emit(line=line, status=status)


def _resolve_actor_name(actor_name: str | None) -> str:
    return actor_name if actor_name is not None else "supervisor"
