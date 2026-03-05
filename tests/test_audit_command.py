from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from scout_agent.app.audit import run_audit_command
from scout_agent.app.audit_ui import AuditUiError
from scout_agent.app.command_config import ResolvedAuditConfig
from scout_agent.app.errors import CommandError
from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import FactsDocument


def _initial_state(root: Path) -> AuditState:
    return {
        "project_root": root,
        "facts_path": root / "FACTS.yaml",
        "files_to_review": [],
        "files_reviewed": ["contracts/gateway.rs"],
        "verified_findings": [],
        "finding_keys": [],
    }


def _facts_document(root: Path) -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(root),
        model="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scope_fingerprint="abc123",
        functions={},
    )


class FakeInitialized:
    def __init__(self, *, root: Path) -> None:
        self.facts_document = _facts_document(root)
        self.initial_state = _initial_state(root)
        self.current_scope_fingerprint = "abc123"


class FakeSession:
    def __init__(self, *, reporter: object, events: list[str]) -> None:
        self.reporter = reporter
        self._events = events

    def run(self, task):
        self._events.append("session:start")
        result = task()
        self._events.append("session:end")
        return result


class FakeOutput:
    def __init__(self, *, session: FakeSession) -> None:
        self._session = session
        self.ui_modes: list[str] = []
        self.summary_calls: list[tuple[Path, AuditState]] = []

    def make_audit_progress_session(self, *, ui_mode: str):
        self.ui_modes.append(ui_mode)
        return self._session

    def print_audit_summary(
        self,
        *,
        report_path: Path,
        final_state: AuditState,
    ) -> None:
        self.summary_calls.append((report_path, final_state))


def test_run_audit_command_prints_summary_after_session_finishes(tmp_path: Path) -> None:
    initialized = FakeInitialized(root=tmp_path)
    config = ResolvedAuditConfig(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        report_path=tmp_path / "REPORT.md",
        model_name=None,
        llm_mode="consistent",
        scout_files=None,
        extra_prompt=None,
        ui_mode="tui",
    )
    events: list[str] = []
    reporter = object()
    session = FakeSession(reporter=reporter, events=events)
    output = FakeOutput(session=session)

    def fake_run_audit(*, runtime) -> AuditState:
        assert runtime.reporter is reporter
        events.append("run_audit")
        return initialized.initial_state

    def fake_write_report(
        *,
        report_path: Path,
        facts_document: FactsDocument,
        state: AuditState,
    ) -> None:
        _ = (report_path, facts_document, state)
        events.append("write_report")

    with (
        patch("scout_agent.app.audit.resolve_audit_config", return_value=config),
        patch("scout_agent.app.audit.initialize_audit", return_value=initialized),
        patch("scout_agent.app.audit.run_audit", side_effect=fake_run_audit),
        patch("scout_agent.app.audit.write_report", side_effect=fake_write_report),
    ):
        exit_code = run_audit_command(Namespace(), output=output)  # type: ignore[arg-type]

    assert exit_code == 0
    assert output.ui_modes == ["tui"]
    assert events == [
        "session:start",
        "run_audit",
        "write_report",
        "session:end",
    ]
    assert output.summary_calls == [
        (config.report_path, initialized.initial_state),
    ]


def test_run_audit_command_wraps_audit_ui_errors(tmp_path: Path) -> None:
    initialized = FakeInitialized(root=tmp_path)
    config = ResolvedAuditConfig(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        report_path=tmp_path / "REPORT.md",
        model_name=None,
        llm_mode="consistent",
        scout_files=None,
        extra_prompt=None,
        ui_mode="tui",
    )

    class FailingOutput:
        def make_audit_progress_session(self, *, ui_mode: str):
            _ = ui_mode
            raise AuditUiError("ui init failed")

        def print_audit_summary(
            self,
            *,
            report_path: Path,
            final_state: AuditState,
        ) -> None:
            raise AssertionError("summary should not be printed")

    with (
        patch("scout_agent.app.audit.resolve_audit_config", return_value=config),
        patch("scout_agent.app.audit.initialize_audit", return_value=initialized),
    ):
        with pytest.raises(CommandError, match="ui init failed"):
            run_audit_command(Namespace(), output=FailingOutput())  # type: ignore[arg-type]
