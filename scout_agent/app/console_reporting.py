from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from scout_agent.app.audit_ui import (
    AuditProgressSession,
    AuditUiMode,
    PlainAuditProgressSession,
    TextualAuditProgressSession,
)
from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import aggregate_facts_file_path
from scout_agent.runtime.audit.reporting import (
    AuditProgressReporter,
    PlainAuditProgressSink,
)
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult
from scout_agent.runtime.extract.reporting import PlainExtractProgressReporter


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

    def make_audit_progress_session(
        self,
        *,
        ui_mode: AuditUiMode,
    ) -> AuditProgressSession:
        if ui_mode == "plain":
            return self._make_plain_audit_session()
        if not _is_tty(self._stdout):
            return self._make_plain_audit_session()
        return TextualAuditProgressSession()

    def print_extract_summary(
        self,
        *,
        result: ExtractFactsPipelineResult,
    ) -> None:
        print(f"FACTS written to: {result.facts_root}", file=self._stdout)
        print(
            f"Aggregate FACTS: {aggregate_facts_file_path(result.facts_root)}",
            file=self._stdout,
        )
        print(f"Files analyzed: {result.file_count}", file=self._stdout)
        print(f"Functions analyzed: {result.function_count}", file=self._stdout)

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
        print(
            "execution_path_consistency completed: "
            f"{final_state['execution_path_consistency_completed']}",
            file=self._stdout,
        )
        print(
            "Final dedup: "
            f"{final_state['final_dedup_status']} "
            f"(removed {final_state['final_dedup_removed_count']})",
            file=self._stdout,
        )

    def print_error(self, message: str) -> None:
        print(f"Error: {message}", file=self._stderr)

    def _make_plain_audit_session(self) -> PlainAuditProgressSession:
        return PlainAuditProgressSession(
            reporter=AuditProgressReporter(PlainAuditProgressSink(self._stdout))
        )


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False
