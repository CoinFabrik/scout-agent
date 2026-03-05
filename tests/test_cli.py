from __future__ import annotations

import contextlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scout_agent.app.errors import CommandError
from scout_agent.app.main import main
from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import FactsDocument
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult


def _initialized_state(root: Path) -> AuditState:
    return {
        "project_root": str(root),
        "facts_path": str(root / "FACTS.yaml"),
        "files_to_review": [],
        "files_reviewed": [],
        "verified_findings": [],
        "finding_keys": [],
    }


def _facts_document(
    root: Path, *, model: str = "anthropic:claude-sonnet-4-5"
) -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(root),
        model=model,
        llm_mode="consistent",
        scope_fingerprint="abc123",
        functions={},
    )


class FakeInitialized:
    def __init__(
        self,
        *,
        state: AuditState,
        facts_document: FactsDocument,
    ) -> None:
        self.facts_document = facts_document
        self.initial_state = state
        self.current_scope_fingerprint = "abc123"


def test_extract_facts_cli_success() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        stdout = StringIO()

        with (
            patch(
                "scout_agent.app.extract_facts.run_extract_facts_pipeline",
                return_value=ExtractFactsPipelineResult(
                    project_root=root,
                    facts_path=root / "FACTS.yaml",
                    file_count=3,
                    function_count=11,
                    scope_fingerprint="a" * 64,
                ),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = main(
                [
                    "extract-facts",
                    str(root),
                    "--model",
                    "anthropic:claude-sonnet-4-5",
                ]
            )

    assert exit_code == 0
    assert "FACTS written to:" in stdout.getvalue()
    assert "Files analyzed: 3" in stdout.getvalue()


def test_extract_facts_cli_uses_scout_json_defaults(tmp_path: Path) -> None:
    (tmp_path / "scout.json").write_text(
        "{\n"
        '  "model": "anthropic:claude-sonnet-4-5",\n'
        '  "mode": "creative",\n'
        '  "files": ["contracts"]\n'
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "contracts").mkdir()

    captured: dict[str, object] = {}

    def fake_run_extract_facts_pipeline(context):
        captured["model_name"] = context.model_name
        captured["llm_mode"] = context.llm_mode
        captured["scout_files"] = context.scout_files
        captured["max_parallel_files"] = context.max_parallel_files
        return ExtractFactsPipelineResult(
            project_root=context.project_root,
            facts_path=context.facts_path,
            file_count=1,
            function_count=1,
            scope_fingerprint="a" * 64,
        )

    with patch(
        "scout_agent.app.extract_facts.run_extract_facts_pipeline",
        side_effect=fake_run_extract_facts_pipeline,
    ):
        exit_code = main(["extract-facts", str(tmp_path)])

    assert exit_code == 0
    assert captured["model_name"] == "anthropic:claude-sonnet-4-5"
    assert captured["llm_mode"] == "creative"
    assert captured["scout_files"] == ["contracts"]
    assert captured["max_parallel_files"] == 4


def test_audit_cli_success(tmp_path: Path) -> None:
    initialized = FakeInitialized(
        state=_initialized_state(tmp_path),
        facts_document=_facts_document(tmp_path),
    )
    report_path = (tmp_path / "REPORT.md").resolve()

    with (
        patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=initialized,
        ),
        patch(
            "scout_agent.app.audit.run_audit",
            return_value=initialized.initial_state,
        ) as mock_run_audit,
        patch(
            "scout_agent.app.audit.write_report",
            return_value=report_path,
        ) as mock_write_report,
    ):
        exit_code = main(["audit", str(tmp_path), "--report-path", "REPORT.md"])

    assert exit_code == 0
    runtime = mock_run_audit.call_args.kwargs["runtime"]
    mock_write_report.assert_called_once_with(
        report_path=runtime.report_path,
        facts_document=runtime.facts_document,
        state=initialized.initial_state,
    )


def test_audit_cli_falls_back_to_facts_document_model(tmp_path: Path) -> None:
    initialized = FakeInitialized(
        state=_initialized_state(tmp_path),
        facts_document=_facts_document(tmp_path, model="openai:gpt-5"),
    )

    with (
        patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=initialized,
        ),
        patch(
            "scout_agent.app.audit.run_audit",
            return_value=initialized.initial_state,
        ) as mock_run_audit,
        patch(
            "scout_agent.app.audit.write_report",
            return_value=(tmp_path / "REPORT.md").resolve(),
        ),
    ):
        exit_code = main(["audit", str(tmp_path)])

    assert exit_code == 0
    runtime = mock_run_audit.call_args.kwargs["runtime"]
    assert runtime.model_name == "openai:gpt-5"


def test_audit_cli_passes_extra_prompt_from_txt(tmp_path: Path) -> None:
    prompt_text = "Prioritize balance accounting invariants."
    (tmp_path / "extra.txt").write_text(prompt_text, encoding="utf-8")

    initialized = FakeInitialized(
        state=_initialized_state(tmp_path),
        facts_document=_facts_document(tmp_path),
    )

    with (
        patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=initialized,
        ),
        patch(
            "scout_agent.app.audit.run_audit",
            return_value=initialized.initial_state,
        ) as mock_run_audit,
        patch(
            "scout_agent.app.audit.write_report",
            return_value=(tmp_path / "REPORT.md").resolve(),
        ),
    ):
        exit_code = main(
            [
                "audit",
                str(tmp_path),
                "--extra-prompt",
                "extra.txt",
            ]
        )

    assert exit_code == 0
    runtime = mock_run_audit.call_args.kwargs["runtime"]
    assert runtime.extra_prompt == prompt_text


def test_audit_cli_rejects_non_txt_extra_prompt(tmp_path: Path) -> None:
    (tmp_path / "extra.md").write_text("hello", encoding="utf-8")
    stderr = StringIO()

    with patch("sys.stderr", stderr):
        exit_code = main(
            [
                "audit",
                str(tmp_path),
                "--extra-prompt",
                "extra.md",
            ]
        )

    assert exit_code == 1
    assert "--extra-prompt must point to a .txt file" in stderr.getvalue()


def test_main_routes_errors_through_output_object(tmp_path: Path) -> None:
    stderr = StringIO()

    with patch(
        "sys.stderr",
        stderr,
    ):
        exit_code = main(["audit", str(tmp_path)])

    assert exit_code == 1
    assert "Error:" in stderr.getvalue()


def test_main_renders_command_error_without_traceback() -> None:
    stderr = StringIO()

    with (
        patch(
            "scout_agent.app.main.run_audit_command",
            side_effect=CommandError("bad input"),
        ),
        patch("sys.stderr", stderr),
    ):
        exit_code = main(["audit", "/tmp/project"])

    assert exit_code == 1
    assert stderr.getvalue().strip() == "Error: bad input"
    assert "Traceback" not in stderr.getvalue()


def test_main_renders_unexpected_exception_without_traceback() -> None:
    stderr = StringIO()

    with (
        patch(
            "scout_agent.app.main.run_audit_command",
            side_effect=RuntimeError("boom"),
        ),
        patch("sys.stderr", stderr),
    ):
        exit_code = main(["audit", "/tmp/project"])

    assert exit_code == 1
    assert stderr.getvalue().strip() == "Error: unexpected RuntimeError: boom"
    assert "Traceback" not in stderr.getvalue()


def test_main_returns_130_for_keyboard_interrupt() -> None:
    with patch(
        "scout_agent.app.main.run_extract_facts_command",
        side_effect=KeyboardInterrupt,
    ):
        exit_code = main(
            [
                "extract-facts",
                "/tmp/project",
                "--model",
                "anthropic:claude-sonnet-4-5",
            ]
        )

    assert exit_code == 130
