from __future__ import annotations

from io import StringIO
from pathlib import Path

from scout_agent.app.audit_ui import (
    PlainAuditProgressSession,
    TextualAuditProgressSession,
)
from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.domain.audit import AuditState, Finding
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult


def _audit_state() -> AuditState:
    return {
        "project_root": Path("/tmp/project"),
        "facts_path": Path("/tmp/project/FACTS.yaml"),
        "files_to_review": [],
        "files_reviewed": ["contracts/gateway.rs"],
        "verified_findings": [
            Finding(
                pattern="Duplicate vector elements",
                severity="HIGH",
                location="contracts/gateway.rs:22",
                description="Vector elements are aggregated without uniqueness checks.",
                evidence="contracts/gateway.rs:22-31",
            )
        ],
        "finding_keys": [],
    }


class FakeStdout(StringIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_console_output_constructs_plain_console_output() -> None:
    output = ConsoleOutput(stdout=StringIO(), stderr=StringIO())

    assert isinstance(output, ConsoleOutput)


def test_console_output_print_extract_summary() -> None:
    stdout = StringIO()
    output = ConsoleOutput(stdout=stdout, stderr=StringIO())

    output.print_extract_summary(
        result=ExtractFactsPipelineResult(
            project_root=Path("/tmp/project"),
            facts_path=Path("/tmp/project/FACTS.yaml"),
            file_count=3,
            function_count=11,
            scope_fingerprint="a" * 64,
        ),
    )

    assert stdout.getvalue().splitlines() == [
        "FACTS written to: /tmp/project/FACTS.yaml",
        "Files analyzed: 3",
        "Functions analyzed: 11",
        f"Scope fingerprint: {'a' * 64}",
    ]


def test_console_output_print_audit_summary() -> None:
    stdout = StringIO()
    output = ConsoleOutput(stdout=stdout, stderr=StringIO())

    output.print_audit_summary(
        report_path=Path("/tmp/project/REPORT.md"),
        final_state=_audit_state(),
    )

    assert stdout.getvalue().splitlines() == [
        f"REPORT written to: {Path('/tmp/project/REPORT.md').resolve()}",
        "Files reviewed: 1",
        "Verified findings: 1",
    ]


def test_console_output_print_error() -> None:
    stderr = StringIO()
    output = ConsoleOutput(stdout=StringIO(), stderr=stderr)

    output.print_error("boom")

    assert stderr.getvalue() == "Error: boom\n"


def test_extract_progress_reporter_prints_started_completed_and_failed_lines() -> None:
    stdout = StringIO()
    output = ConsoleOutput(stdout=stdout, stderr=StringIO())
    reporter = output.make_extract_progress_reporter()

    reporter.started(
        project_root=Path("/tmp/project"),
        total_files=2,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )
    reporter.file_started(index=1, total=2, relative_path="contracts/a.rs")
    reporter.file_completed(
        index=1,
        total=2,
        relative_path="contracts/a.rs",
        function_count=3,
    )
    reporter.file_failed(
        index=2,
        total=2,
        relative_path="contracts/b.rs",
        error_type="ValueError",
        message="broken inventory",
    )
    reporter.close()

    assert stdout.getvalue().splitlines() == [
        "Starting extract-facts for /tmp/project with 2 file(s) using anthropic:claude-sonnet-4-5 [consistent]",
        "Extracting 1/2: contracts/a.rs",
        "Completed 1/2: contracts/a.rs (3 function(s))",
        "Failed 2/2: contracts/b.rs (ValueError: broken inventory)",
    ]


def test_audit_progress_reporter_prints_progress_lines() -> None:
    stdout = StringIO()
    output = ConsoleOutput(stdout=stdout, stderr=StringIO())
    reporter = output.make_audit_progress_reporter()

    reporter.started(
        project_root=Path("/tmp/project"),
        total_files=2,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )
    reporter.file_started(index=1, total=2, current_file="contracts/a.rs")
    reporter.finding_verified(
        total_verified_findings=1,
        finding=Finding(
            pattern="Unchecked admin transfer",
            severity="HIGH",
            location="contracts/a.rs:17",
            description="x",
            evidence="contracts/a.rs:17-22",
        ),
    )
    reporter.file_completed(reviewed=1, total=2, current_file="contracts/a.rs")
    reporter.close()

    assert stdout.getvalue().splitlines() == [
        "Starting audit for /tmp/project with 2 file(s) using anthropic:claude-sonnet-4-5 [consistent]",
        "Auditing 1/2: contracts/a.rs",
        "Verified HIGH finding #1: Unchecked admin transfer at contracts/a.rs:17",
        "Completed 1/2: contracts/a.rs",
    ]


def test_audit_progress_reporter_prints_expert_and_tool_logs() -> None:
    stdout = StringIO()
    output = ConsoleOutput(stdout=stdout, stderr=StringIO())
    reporter = output.make_audit_progress_reporter()

    reporter.expert_spawned(expert_name="time_state")
    reporter.tool_used(
        tool_name="read_code_chunk",
        target="contracts/a.rs",
        expert_name="time_state",
        line_start=12,
        line_end=40,
    )
    reporter.tool_used(
        tool_name="read",
        target="/contracts/a.rs",
        offset=0,
        limit=2000,
        line_start=1,
        line_end=75,
    )
    reporter.tool_denied(
        tool_name="read",
        target="/contracts/b.rs",
        current_file="/contracts/a.rs",
        reason="outside-current-file-scope",
    )
    reporter.close()

    assert stdout.getvalue().splitlines() == [
        "Spawning expert: time_state",
        "Tool used: actor=time_state tool=read_code_chunk target=contracts/a.rs lines=12-40",
        "Tool used: actor=supervisor tool=read target=/contracts/a.rs lines=1-75 offset=0 limit=2000",
        "Tool denied: actor=supervisor tool=read target=/contracts/b.rs current=/contracts/a.rs reason=outside-current-file-scope",
    ]


def test_console_output_uses_textual_session_for_tty_tui() -> None:
    output = ConsoleOutput(stdout=FakeStdout(is_tty=True), stderr=StringIO())

    session = output.make_audit_progress_session(ui_mode="tui")

    assert isinstance(session, TextualAuditProgressSession)


def test_console_output_falls_back_to_plain_session_for_non_tty_tui() -> None:
    output = ConsoleOutput(stdout=FakeStdout(is_tty=False), stderr=StringIO())

    session = output.make_audit_progress_session(ui_mode="tui")

    assert isinstance(session, PlainAuditProgressSession)


def test_console_output_uses_plain_session_for_plain_mode() -> None:
    output = ConsoleOutput(stdout=FakeStdout(is_tty=True), stderr=StringIO())

    session = output.make_audit_progress_session(ui_mode="plain")

    assert isinstance(session, PlainAuditProgressSession)
