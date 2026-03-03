from __future__ import annotations

from pathlib import Path
from typing import TextIO

from scout_agent.domain.audit import Finding


class PlainAuditProgressReporter:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout
        self._closed = False

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

    def delegation_batch(
        self,
        *,
        current_file: str,
        delegation_count: int,
    ) -> None:
        print(
            f"{current_file}: delegating {delegation_count} expert checks",
            file=self._stdout,
            flush=True,
        )

    def supervisor_pass(
        self,
        *,
        current_file: str,
        pass_index: int,
        completed_checks: int,
        verified_findings: int,
        needs_info_notes: int,
    ) -> None:
        print(
            f"{current_file}: supervisor pass {pass_index} "
            f"({completed_checks} completed checks, {verified_findings} findings, "
            f"{needs_info_notes} needs-info notes)",
            file=self._stdout,
            flush=True,
        )

    def duplicate_delegations_filtered(
        self,
        *,
        current_file: str,
        requested: int,
        dropped: int,
        remaining: int,
    ) -> None:
        print(
            f"{current_file}: dropped {dropped} duplicate delegations "
            f"({remaining} new expert checks remain)",
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

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
