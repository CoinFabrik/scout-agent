from __future__ import annotations
from scout_agent.runtime.audit.reporting import PlainAuditProgressReporter
from scout_agent.runtime.extract.reporting import PlainExtractProgressReporter

import sys
from pathlib import Path
from typing import TextIO

from scout_agent.domain.audit import AuditState
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult


class ConsoleOutput:
    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._stdout = stdout or sys.stdout
        self._stderr = stderr or sys.stderr

    def make_extract_progress_reporter(self) -> PlainExtractProgressReporter:
        return PlainExtractProgressReporter(self._stdout)

    def make_audit_progress_reporter(self) -> PlainAuditProgressReporter:
        return PlainAuditProgressReporter(self._stdout)

    def print_extract_summary(
        self,
        *,
        result: ExtractFactsPipelineResult,
    ) -> None:
        print(f"FACTS written to: {result.facts_path}", file=self._stdout)
        print(f"Files analyzed: {result.file_count}", file=self._stdout)
        print(f"Functions analyzed: {result.function_count}", file=self._stdout)
        print(f"Scope fingerprint: {result.scope_fingerprint}", file=self._stdout)

    def print_audit_summary(
        self,
        *,
        report_path: Path,
        final_state: AuditState,
    ) -> None:
        print(f"REPORT written to: {report_path.resolve()}", file=self._stdout)
        print(
            f"Files reviewed: {len(final_state['files_reviewed'])}",
            file=self._stdout,
        )
        print(
            f"Verified findings: {len(final_state['verified_findings'])}",
            file=self._stdout,
        )

    def print_error(self, message: str) -> None:
        print(f"Error: {message}", file=self._stderr)
