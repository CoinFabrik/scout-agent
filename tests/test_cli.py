from __future__ import annotations

import contextlib
import os
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scout_agent.app.console_reporting import (
    PlainExtractProgressReporter,
)
from scout_agent.app.context import ExtractContext
from scout_agent.app.main import main
from scout_agent.configuration.settings import MAX_MAX_PARALLEL_FILES
from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import FactsDocument
from scout_agent.runtime.audit.graph import AuditContext
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult


def _initialized_state(root: Path) -> AuditState:
    return {
        "project_root": str(root),
        "facts_path": str(root / "FACTS.yaml"),
        "facts_index": {},
        "files_to_review": [],
        "current_file": None,
        "last_supervisor_decision": None,
        "pending_delegations": [],
        "completed_delegation_keys": [],
        "needs_info_notes": [],
        "finding_keys": [],
        "files_reviewed": [],
        "verified_findings": [],
        "expert_batch_items": [],
    }


def _facts_document(root: Path, *, model: str = "anthropic:claude-sonnet-4-5") -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(root),
        model=model,
        llm_mode="consistent",
        scope_fingerprint="abc123",
        files=[],
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


def _state_dir(root: Path) -> Path:
    return root / ("." + "scout-ai")


def test_extract_facts_cli_success() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        stdout = StringIO()

        with patch(
            "scout_agent.app.extract_facts.run_extract_facts_pipeline",
            return_value=ExtractFactsPipelineResult(
                project_root=root,
                facts_path=root / "FACTS.yaml",
                file_count=3,
                function_count=11,
                scope_fingerprint="a" * 64,
            ),
        ):
            with contextlib.redirect_stdout(stdout):
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
        '{\n'
        '  "model": "anthropic:claude-sonnet-4-5",\n'
        '  "mode": "creative",\n'
        '  "files": ["contracts"]\n'
        '}\n',
        encoding="utf-8",
    )
    (tmp_path / "contracts").mkdir()

    captured: dict[str, object] = {}

    def fake_run_extract_facts_pipeline(
        *,
        project_root,
        facts_path,
        model_name,
        llm_mode,
        scout_files,
        reporter,
        max_parallel_files,
    ):
        captured["model_name"] = model_name
        captured["llm_mode"] = llm_mode
        captured["scout_files"] = scout_files
        captured["max_parallel_files"] = max_parallel_files
        return ExtractFactsPipelineResult(
            project_root=project_root,
            facts_path=facts_path,
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


def test_extract_facts_cli_resolves_max_parallel_files(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_extract_facts_pipeline(
        *,
        project_root,
        facts_path,
        model_name,
        llm_mode,
        scout_files,
        reporter,
        max_parallel_files,
    ):
        captured["max_parallel_files"] = max_parallel_files
        return ExtractFactsPipelineResult(
            project_root=project_root,
            facts_path=facts_path,
            file_count=1,
            function_count=1,
            scope_fingerprint="a" * 64,
        )

    with patch(
        "scout_agent.app.extract_facts.run_extract_facts_pipeline",
        side_effect=fake_run_extract_facts_pipeline,
    ):
        exit_code = main(
            [
                "extract-facts",
                str(tmp_path),
                "--model",
                "anthropic:claude-sonnet-4-5",
                "--max-parallel-files",
                "6",
            ]
        )

    assert exit_code == 0
    assert captured["max_parallel_files"] == 6


def test_extract_facts_cli_falls_back_to_scout_json_max_parallel_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "scout.json").write_text(
        '{\n'
        '  "model": "anthropic:claude-sonnet-4-5",\n'
        '  "max_parallel_files": 5\n'
        '}\n',
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_run_extract_facts_pipeline(
        *,
        project_root,
        facts_path,
        model_name,
        llm_mode,
        scout_files,
        reporter,
        max_parallel_files,
    ):
        captured["max_parallel_files"] = max_parallel_files
        return ExtractFactsPipelineResult(
            project_root=project_root,
            facts_path=facts_path,
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
    assert captured["max_parallel_files"] == 5


def test_extract_facts_cli_rejects_invalid_max_parallel_files() -> None:
    stderr = StringIO()
    invalid_value = MAX_MAX_PARALLEL_FILES + 1

    with contextlib.redirect_stderr(stderr):
        exit_code = main(
            [
                "extract-facts",
                ".",
                "--model",
                "anthropic:claude-sonnet-4-5",
                "--max-parallel-files",
                str(invalid_value),
            ]
        )

    assert exit_code == 1
    assert (
        f"max_parallel_files must be between 1 and {MAX_MAX_PARALLEL_FILES}"
        in stderr.getvalue()
    )


def test_extract_facts_cli_rejects_test_only_projects(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "integration.rs").write_text(
        "pub fn ignored() {}\n",
        encoding="utf-8",
    )

    stderr = StringIO()

    with contextlib.redirect_stderr(stderr):
        exit_code = main(
            [
                "extract-facts",
                str(tmp_path),
                "--model",
                "anthropic:claude-sonnet-4-5",
            ]
        )

    assert exit_code == 1
    assert "No in-scope production Rust source files were discovered" in stderr.getvalue()


def test_extract_facts_command_uses_output_object_for_summary(tmp_path: Path) -> None:
    fake_output = Mock()
    fake_output.make_extract_progress_reporter.return_value = Mock()

    result = ExtractFactsPipelineResult(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        file_count=2,
        function_count=5,
        scope_fingerprint="a" * 64,
    )

    with patch(
        "scout_agent.app.extract_facts.run_extract_facts_pipeline",
        return_value=result,
    ):
        with patch("scout_agent.app.main.build_console_output", return_value=fake_output):
            exit_code = main(
                [
                    "extract-facts",
                    str(tmp_path),
                    "--model",
                    "anthropic:claude-sonnet-4-5",
                ]
            )

    assert exit_code == 0
    fake_output.print_extract_summary.assert_called_once()
    kwargs = fake_output.print_extract_summary.call_args.kwargs
    assert kwargs == {"result": result}


def test_extract_facts_cli_does_not_create_scout_state_directory(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_extract_facts_pipeline(**kwargs):
        captured.update(kwargs)
        return ExtractFactsPipelineResult(
            project_root=tmp_path,
            facts_path=tmp_path / "FACTS.yaml",
            file_count=1,
            function_count=1,
            scope_fingerprint="a" * 64,
        )

    exit_code = 0
    with patch(
        "scout_agent.app.extract_facts.run_extract_facts_pipeline",
        side_effect=fake_run_extract_facts_pipeline,
    ):
        exit_code = main(
            ["extract-facts", str(tmp_path), "--model", "anthropic:claude-sonnet-4-5"]
        )

    assert exit_code == 0
    assert set(captured) == {
        "facts_path",
        "llm_mode",
        "max_parallel_files",
        "model_name",
        "project_root",
        "reporter",
        "scout_files",
    }
    assert not _state_dir(tmp_path).exists()


def test_audit_cli_success() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        initialized_state = _initialized_state(root)
        initialized_state["files_reviewed"] = ["contracts/gateway.rs"]
        facts_document = _facts_document(root)
        stdout = StringIO()
        captured: dict[str, object] = {}

        def fake_run_audit(*, runtime, initial_state):
            captured["runtime"] = runtime
            return initial_state

        with patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=FakeInitialized(
                state=initialized_state,
                facts_document=facts_document,
            ),
        ):
            with patch(
                "scout_agent.app.audit.run_audit",
                side_effect=fake_run_audit,
            ):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        ["audit", str(root), "--report-path", "REPORT.md"]
                    )

    assert exit_code == 0
    assert captured["runtime"].report_path == (root / "REPORT.md").resolve()
    assert "REPORT written to:" in stdout.getvalue()
    assert "Files reviewed: 1" in stdout.getvalue()


def test_audit_cli_falls_back_to_facts_document_model() -> None:
    with TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        initialized_state = _initialized_state(root)
        facts_document = _facts_document(root)
        captured: dict[str, object] = {}

        def fake_run_audit(*, runtime, initial_state):
            captured["runtime"] = runtime
            return initial_state

        with patch.dict(os.environ, {}, clear=False):
            with patch(
                "scout_agent.app.audit.initialize_audit",
                return_value=FakeInitialized(
                    state=initialized_state,
                    facts_document=facts_document,
                ),
            ):
                with patch(
                    "scout_agent.app.audit.run_audit",
                    side_effect=fake_run_audit,
                ):
                    exit_code = main(["audit", str(root)])

    assert exit_code == 0
    assert captured["runtime"].model_name == "anthropic:claude-sonnet-4-5"


def test_audit_cli_uses_scout_json_model_before_facts_model(tmp_path: Path) -> None:
    (tmp_path / "scout.json").write_text(
        '{\n'
        '  "model": "openai:gpt-5",\n'
        '  "mode": "creative"\n'
        '}\n',
        encoding="utf-8",
    )

    initialized_state = _initialized_state(tmp_path)
    facts_document = _facts_document(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_audit(*, runtime, initial_state):
        captured["runtime"] = runtime
        return initial_state

    with patch(
        "scout_agent.app.audit.initialize_audit",
        return_value=FakeInitialized(
            state=initialized_state,
            facts_document=facts_document,
        ),
    ):
        with patch(
            "scout_agent.app.audit.run_audit",
            side_effect=fake_run_audit,
        ):
            exit_code = main(["audit", str(tmp_path)])

    assert exit_code == 0
    assert captured["runtime"].model_name == "openai:gpt-5"
    assert captured["runtime"].llm_mode == "creative"


def test_audit_command_uses_output_object_for_summary(tmp_path: Path) -> None:
    initialized_state = _initialized_state(tmp_path)
    initialized_state["files_reviewed"] = ["contracts/gateway.rs"]
    facts_document = _facts_document(tmp_path)
    fake_output = Mock()
    fake_output.make_audit_progress_reporter.return_value = Mock()

    with patch("scout_agent.app.main.build_console_output", return_value=fake_output):
        with patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=FakeInitialized(
                state=initialized_state,
                facts_document=facts_document,
            ),
        ):
            with patch(
                "scout_agent.app.audit.run_audit",
                return_value=initialized_state,
            ):
                exit_code = main(["audit", str(tmp_path), "--report-path", "REPORT.md"])

    assert exit_code == 0
    fake_output.print_audit_summary.assert_called_once()
    kwargs = fake_output.print_audit_summary.call_args.kwargs
    assert kwargs["report_path"] == tmp_path / "REPORT.md"
    assert kwargs["final_state"] == initialized_state
    assert set(kwargs) == {"report_path", "final_state"}


def test_audit_cli_surfaces_invalid_supervisor_state_as_clean_error(
    tmp_path: Path,
) -> None:
    initialized_state = _initialized_state(tmp_path)
    facts_document = _facts_document(tmp_path)
    fake_output = Mock()
    fake_output.make_audit_progress_reporter.return_value = Mock()

    with patch("scout_agent.app.main.build_console_output", return_value=fake_output):
        with patch(
            "scout_agent.app.audit.initialize_audit",
            return_value=FakeInitialized(
                state=initialized_state,
                facts_document=facts_document,
            ),
        ):
            with patch(
                "scout_agent.app.audit.run_audit",
                side_effect=ValueError(
                    "Invalid SupervisorDecision for contracts/gateway.rs: "
                    "file_fully_analyzed=false with 0 delegations."
                ),
            ):
                exit_code = main(["audit", str(tmp_path)])

    assert exit_code == 1
    fake_output.print_error.assert_called_once_with(
        "Invalid SupervisorDecision for contracts/gateway.rs: "
        "file_fully_analyzed=false with 0 delegations."
    )


def test_audit_cli_does_not_create_scout_state_directory(tmp_path: Path) -> None:
    initialized_state = _initialized_state(tmp_path)
    facts_document = _facts_document(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_audit(*, runtime, initial_state):
        captured["runtime"] = runtime
        return initial_state

    with patch(
        "scout_agent.app.audit.initialize_audit",
        return_value=FakeInitialized(
            state=initialized_state,
            facts_document=facts_document,
        ),
    ):
        exit_code = 0
        with patch(
            "scout_agent.app.audit.run_audit",
            side_effect=fake_run_audit,
        ):
            exit_code = main(["audit", str(tmp_path), "--model", "anthropic:claude-sonnet-4-5"])

    assert exit_code == 0
    assert set(captured["runtime"].__dataclass_fields__) == {
        "facts_document",
        "facts_index",
        "llm_mode",
        "model_name",
        "project_root",
        "report_path",
        "reporter",
    }
    assert not _state_dir(tmp_path).exists()


def test_command_context_dataclasses_use_direct_fields() -> None:
    extract_context = ExtractContext(
        project_root=Path("/tmp/project"),
        facts_path=Path("/tmp/project/FACTS.yaml"),
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scout_files=["contracts"],
        max_parallel_files=4,
        reporter=PlainExtractProgressReporter(StringIO()),
    )
    audit_context = AuditContext(
        project_root=Path("/tmp/project"),
        report_path=Path("/tmp/project/REPORT.md"),
        facts_document=_facts_document(Path("/tmp/project")),
        facts_index={},
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=Mock(),
    )

    assert extract_context.project_root == Path("/tmp/project")
    assert extract_context.facts_path == Path("/tmp/project/FACTS.yaml")
    assert extract_context.model_name == "anthropic:claude-sonnet-4-5"
    assert extract_context.llm_mode == "consistent"
    assert extract_context.scout_files == ["contracts"]
    assert extract_context.max_parallel_files == 4
    assert isinstance(extract_context.reporter, PlainExtractProgressReporter)
    assert audit_context.project_root == Path("/tmp/project")
    assert audit_context.report_path == Path("/tmp/project/REPORT.md")
    assert audit_context.model_name == "anthropic:claude-sonnet-4-5"
    assert audit_context.llm_mode == "consistent"
    assert audit_context.facts_index == {}


def test_main_routes_errors_through_output_object(tmp_path: Path) -> None:
    fake_output = Mock()

    with patch("scout_agent.app.main.build_console_output", return_value=fake_output):
        exit_code = main(
            [
                "extract-facts",
                str(tmp_path / "missing"),
                "--model",
                "anthropic:claude-sonnet-4-5",
            ]
        )

    assert exit_code == 1
    fake_output.print_error.assert_called_once()
