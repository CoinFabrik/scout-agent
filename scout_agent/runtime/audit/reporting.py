from __future__ import annotations

from pathlib import Path
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


def format_audit_expert_spawned_line(
    *,
    expert_name: str,
) -> str:
    return f"Spawning expert: {expert_name}"


def format_audit_tool_used_line(
    *,
    tool_name: str,
    target: str,
    expert_name: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    actor = _resolve_actor_name(expert_name)
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
    expert_name: str | None = None,
) -> str:
    actor = _resolve_actor_name(expert_name)
    return (
        "Tool denied: "
        f"actor={actor} tool={tool_name} target={target} "
        f"current={current_file} reason={reason}"
    )


class AuditProgressReporter(Protocol):
    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None: ...

    def file_started(
        self,
        *,
        index: int,
        total: int,
        current_file: str,
    ) -> None: ...

    def finding_verified(
        self,
        *,
        total_verified_findings: int,
        finding: Finding,
    ) -> None: ...

    def file_completed(
        self,
        *,
        reviewed: int,
        total: int,
        current_file: str,
    ) -> None: ...

    def expert_spawned(
        self,
        *,
        expert_name: str,
    ) -> None: ...

    def tool_used(
        self,
        *,
        tool_name: str,
        target: str,
        expert_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> None: ...

    def tool_denied(
        self,
        *,
        tool_name: str,
        target: str,
        current_file: str,
        reason: str,
        expert_name: str | None = None,
    ) -> None: ...

    def close(self) -> None: ...


class PlainAuditProgressReporter:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout

    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None:
        self._print_line(
            format_audit_started_line(
                project_root=project_root,
                total_files=total_files,
                model_name=model_name,
                llm_mode=llm_mode,
            )
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        current_file: str,
    ) -> None:
        self._print_line(
            format_audit_file_started_line(
                index=index,
                total=total,
                current_file=current_file,
            )
        )

    def finding_verified(
        self,
        *,
        total_verified_findings: int,
        finding: Finding,
    ) -> None:
        self._print_line(
            format_audit_finding_verified_line(
                total_verified_findings=total_verified_findings,
                finding=finding,
            )
        )

    def file_completed(
        self,
        *,
        reviewed: int,
        total: int,
        current_file: str,
    ) -> None:
        self._print_line(
            format_audit_file_completed_line(
                reviewed=reviewed,
                total=total,
                current_file=current_file,
            )
        )

    def expert_spawned(
        self,
        *,
        expert_name: str,
    ) -> None:
        self._print_line(
            format_audit_expert_spawned_line(expert_name=expert_name)
        )

    def tool_used(
        self,
        *,
        tool_name: str,
        target: str,
        expert_name: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        offset: int | None = None,
        limit: int | None = None,
        query: str | None = None,
    ) -> None:
        self._print_line(
            format_audit_tool_used_line(
                tool_name=tool_name,
                target=target,
                expert_name=expert_name,
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
        expert_name: str | None = None,
    ) -> None:
        self._print_line(
            format_audit_tool_denied_line(
                tool_name=tool_name,
                target=target,
                current_file=current_file,
                reason=reason,
                expert_name=expert_name,
            )
        )

    def close(self) -> None:
        return None

    def _print_line(self, line: str) -> None:
        print(line, file=self._stdout, flush=True)


def _resolve_actor_name(expert_name: str | None) -> str:
    return expert_name if expert_name is not None else "supervisor"
