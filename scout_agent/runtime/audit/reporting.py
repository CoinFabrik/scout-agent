from __future__ import annotations

from pathlib import Path
from typing import TextIO

from scout_agent.domain.audit import Finding


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
        print(
            "Starting audit for "
            f"{project_root} with {total_files} file(s) using {model_name} [{llm_mode}]",
            file=self._stdout,
            flush=True,
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        current_file: str,
    ) -> None:
        print(
            f"Auditing {index}/{total}: {current_file}",
            file=self._stdout,
            flush=True,
        )

    def finding_verified(
        self,
        *,
        total_verified_findings: int,
        finding: Finding,
    ) -> None:
        print(
            "Verified "
            f"{finding.severity} finding #{total_verified_findings}: "
            f"{finding.pattern} at {finding.location}",
            file=self._stdout,
            flush=True,
        )

    def file_completed(
        self,
        *,
        reviewed: int,
        total: int,
        current_file: str,
    ) -> None:
        print(
            f"Completed {reviewed}/{total}: {current_file}",
            file=self._stdout,
            flush=True,
        )

    def expert_spawned(
        self,
        *,
        expert_name: str,
    ) -> None:
        print(
            f"Spawning expert: {expert_name}",
            file=self._stdout,
            flush=True,
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
    ) -> None:
        actor = expert_name if expert_name is not None else "supervisor"
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
        print(
            "Tool used: " + " ".join(parts),
            file=self._stdout,
            flush=True,
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
        actor = expert_name if expert_name is not None else "supervisor"
        print(
            "Tool denied: "
            f"actor={actor} tool={tool_name} target={target} "
            f"current={current_file} reason={reason}",
            file=self._stdout,
            flush=True,
        )

    def close(self) -> None:
        return None
