from __future__ import annotations

from pathlib import Path
from queue import Empty

import pytest

from scout_agent.app.audit_ui import (
    AuditStatusSnapshot,
    TextualAuditProgressReporter,
    TextualAuditProgressSession,
)
from scout_agent.domain.audit import Finding
from scout_agent.runtime.audit.reporting import (
    format_audit_file_completed_line,
    format_audit_file_started_line,
    format_audit_finding_verified_line,
    format_audit_started_line,
)


def test_textual_audit_progress_reporter_updates_status_and_log_lines() -> None:
    reporter = TextualAuditProgressReporter()
    finding = Finding(
        pattern="Unchecked admin transfer",
        severity="HIGH",
        location="contracts/a.rs:17",
        description="x",
        evidence="contracts/a.rs:17-22",
    )

    reporter.started(
        project_root=Path("/tmp/project"),
        total_files=2,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )
    reporter.file_started(index=1, total=2, current_file="contracts/a.rs")
    reporter.finding_verified(total_verified_findings=1, finding=finding)
    reporter.file_completed(reviewed=1, total=2, current_file="contracts/a.rs")
    reporter.close()

    events = _drain_events(reporter)

    assert [event.line for event in events if event.line is not None] == [
        format_audit_started_line(
            project_root=Path("/tmp/project"),
            total_files=2,
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
        ),
        format_audit_file_started_line(
            index=1,
            total=2,
            current_file="contracts/a.rs",
        ),
        format_audit_finding_verified_line(
            total_verified_findings=1,
            finding=finding,
        ),
        format_audit_file_completed_line(
            reviewed=1,
            total=2,
            current_file="contracts/a.rs",
        ),
    ]
    assert reporter.status_snapshot == AuditStatusSnapshot(
        current_file="contracts/a.rs",
        reviewed=1,
        total=2,
        verified_findings=1,
    )
    assert [event.close for event in events].count(True) == 1


def test_textual_audit_progress_session_reraises_worker_exception_after_shutdown(
    monkeypatch,
) -> None:
    session = TextualAuditProgressSession()
    seen_close_events: list[bool] = []

    class FakeApp:
        def run(self) -> None:
            while True:
                event = session.reporter.event_queue.get(timeout=1)
                seen_close_events.append(event.close)
                if event.close:
                    return

    monkeypatch.setattr(session, "_create_app", lambda: FakeApp())

    def task() -> None:
        session.reporter.file_started(index=1, total=1, current_file="contracts/a.rs")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        session.run(task)

    assert seen_close_events.count(True) == 1


def _drain_events(reporter: TextualAuditProgressReporter) -> list[object]:
    events: list[object] = []
    while True:
        try:
            events.append(reporter.event_queue.get_nowait())
        except Empty:
            return events
