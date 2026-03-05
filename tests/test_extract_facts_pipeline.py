from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from scout_agent.domain.facts import FunctionSummary, load_facts_document
from scout_agent.runtime.extract.models import ExtractContext
from scout_agent.runtime.extract.facts_extractor import (
    ExtractedFunctionSummary,
    FileFactsExtractionResponse,
)
from scout_agent.runtime.extract.pipeline import (
    ExtractFactsParallelError,
    run_extract_facts_pipeline,
)


class FakeExtractReporter:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def started(self, **kwargs) -> None:
        self.events.append(("started", kwargs["total_files"]))

    def file_started(self, **kwargs) -> None:
        self.events.append(("file_started", kwargs["index"], kwargs["relative_path"]))

    def file_completed(self, **kwargs) -> None:
        self.events.append(
            (
                "file_completed",
                kwargs["index"],
                kwargs["relative_path"],
                kwargs["function_count"],
            )
        )

    def file_failed(self, **kwargs) -> None:
        self.events.append(
            (
                "file_failed",
                kwargs["index"],
                kwargs["relative_path"],
                kwargs["error_type"],
                kwargs["message"],
            )
        )

    def close(self) -> None:
        self.events.append(("close",))


def _context(root: Path, reporter: FakeExtractReporter) -> ExtractContext:
    return ExtractContext(
        project_root=root,
        facts_path=root / "FACTS.yaml",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scout_files=None,
        max_parallel_files=2,
        reporter=reporter,
    )


def _make_summaries(parsed_file) -> dict[str, FunctionSummary]:
    summaries: dict[str, FunctionSummary] = {}
    for function in parsed_file.functions:
        key = f"{parsed_file.relative_path}::{function.name}"
        if function.impl_target:
            key = (
                f"{parsed_file.relative_path}::{function.impl_target}::{function.name}"
            )
        summaries[key] = FunctionSummary()
    return summaries


def _response(function_key: str) -> FileFactsExtractionResponse:
    return FileFactsExtractionResponse(
        functions=[
            ExtractedFunctionSummary(
                function_key=function_key,
                summary=FunctionSummary(),
            )
        ]
    )


def test_extract_facts_pipeline_emits_reporter_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    (contracts / "b.rs").write_text("pub fn beta() {}\n", encoding="utf-8")
    (contracts / "c.rs").write_text("pub fn gamma() {}\n", encoding="utf-8")

    def fake_extract(parsed_file, *, model_name, llm_mode):  # noqa: ARG001
        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.12)
        elif parsed_file.relative_path.endswith("b.rs"):
            time.sleep(0.02)
        else:
            time.sleep(0.01)
        return _make_summaries(parsed_file)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    reporter = FakeExtractReporter()
    result = run_extract_facts_pipeline(_context(tmp_path, reporter))

    assert result.file_count == 3
    assert result.function_count == 3
    assert reporter.events[0] == ("started", 3)
    assert [event for event in reporter.events if event[0] == "file_started"] == [
        ("file_started", 1, "contracts/a.rs"),
        ("file_started", 2, "contracts/b.rs"),
        ("file_started", 3, "contracts/c.rs"),
    ]


def test_extract_facts_pipeline_writes_flat_function_map_in_file_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    (contracts / "b.rs").write_text("pub fn beta() {}\n", encoding="utf-8")
    (contracts / "c.rs").write_text("pub fn gamma() {}\n", encoding="utf-8")

    def fake_extract(parsed_file, *, model_name, llm_mode):  # noqa: ARG001
        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.12)
        elif parsed_file.relative_path.endswith("b.rs"):
            time.sleep(0.01)
        else:
            time.sleep(0.03)
        return _make_summaries(parsed_file)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    result = run_extract_facts_pipeline(_context(tmp_path, FakeExtractReporter()))

    assert result.file_count == 3
    loaded = load_facts_document(tmp_path / "FACTS.yaml")
    assert loaded.schema_version == "2"
    assert list(loaded.functions) == [
        "contracts/a.rs::alpha",
        "contracts/b.rs::beta",
        "contracts/c.rs::gamma",
    ]


def test_extract_facts_pipeline_retries_retryable_mismatch_and_writes_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    invoke_count = 0

    class FakeStructuredModel:
        def invoke(self, _messages):
            nonlocal invoke_count
            invoke_count += 1
            if invoke_count == 1:
                return _response("contracts/ a.rs::alpha")
            return _response("contracts/a.rs::alpha")

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeStructuredModel()

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    reporter = FakeExtractReporter()
    result = run_extract_facts_pipeline(_context(tmp_path, reporter))

    assert result.file_count == 1
    assert result.function_count == 1
    assert invoke_count == 2
    assert [event for event in reporter.events if event[0] == "file_completed"] == [
        ("file_completed", 1, "contracts/a.rs", 1)
    ]
    loaded = load_facts_document(tmp_path / "FACTS.yaml")
    assert list(loaded.functions) == ["contracts/a.rs::alpha"]


def test_extract_facts_pipeline_fails_after_retry_budget_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    invoke_count = 0

    class FakeStructuredModel:
        def invoke(self, _messages):
            nonlocal invoke_count
            invoke_count += 1
            return _response("contracts/ a.rs::alpha")

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeStructuredModel()

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    reporter = FakeExtractReporter()

    with pytest.raises(ExtractFactsParallelError, match=r"contracts/a\.rs") as exc_info:
        run_extract_facts_pipeline(_context(tmp_path, reporter))

    assert invoke_count == 3
    assert [event for event in reporter.events if event[0] == "file_failed"] == [
        (
            "file_failed",
            1,
            "contracts/a.rs",
            "RetryableExtractionError",
            "Extraction response does not match parsed function inventory for contracts/a.rs: missing=['contracts/a.rs::alpha']; unexpected=['contracts/ a.rs::alpha']",
        )
    ]
    assert "RetryableExtractionError" in str(exc_info.value)
    assert not (tmp_path / "FACTS.yaml").exists()


def test_extract_facts_pipeline_bounds_concurrency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for index in range(5):
        (contracts / f"{index}.rs").write_text(
            f"pub fn f{index}() {{}}\n",
            encoding="utf-8",
        )

    active = 0
    max_seen = 0
    lock = threading.Lock()

    def fake_extract(parsed_file, *, model_name, llm_mode):  # noqa: ARG001
        nonlocal active, max_seen
        with lock:
            active += 1
            max_seen = max(max_seen, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _make_summaries(parsed_file)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    run_extract_facts_pipeline(_context(tmp_path, FakeExtractReporter()))

    assert max_seen <= 2


def test_extract_facts_pipeline_fails_after_in_flight_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    for name in ("a", "b", "c", "d"):
        (contracts / f"{name}.rs").write_text(
            f"pub fn {name}() {{}}\n",
            encoding="utf-8",
        )

    started_paths: list[str] = []
    lock = threading.Lock()

    def fake_extract(parsed_file, *, model_name, llm_mode):  # noqa: ARG001
        with lock:
            started_paths.append(parsed_file.relative_path)

        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.05)
            raise ValueError("broken inventory")

        time.sleep(0.12)
        return _make_summaries(parsed_file)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    reporter = FakeExtractReporter()

    with pytest.raises(ExtractFactsParallelError, match=r"contracts/a\.rs") as exc_info:
        run_extract_facts_pipeline(_context(tmp_path, reporter))

    assert "contracts/a.rs" in started_paths
    assert "contracts/b.rs" in started_paths
    assert "contracts/d.rs" not in started_paths
    assert len(started_paths) <= 3
    failed_events = [event for event in reporter.events if event[0] == "file_failed"]
    assert (
        "file_failed",
        1,
        "contracts/a.rs",
        "ValueError",
        "broken inventory",
    ) in failed_events
    for event in failed_events:
        if event[2] == "contracts/a.rs":
            continue
        assert event[3] == "CancelledError"
    assert "ValueError: broken inventory" in str(exc_info.value)
    assert not (tmp_path / "FACTS.yaml").exists()


def test_extract_facts_pipeline_rejects_empty_or_test_only_scopes(
    tmp_path: Path,
) -> None:
    reporter = FakeExtractReporter()

    with pytest.raises(
        ValueError,
        match="No in-scope production Rust source files were discovered",
    ):
        run_extract_facts_pipeline(_context(tmp_path, reporter))

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "integration.rs").write_text("pub fn ignored() {}\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="No in-scope production Rust source files were discovered",
    ):
        run_extract_facts_pipeline(_context(tmp_path, FakeExtractReporter()))

    assert reporter.events == []


def test_extract_facts_pipeline_does_not_create_scout_state_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        lambda parsed_file, *, model_name, llm_mode: _make_summaries(
            parsed_file
        ),  # noqa: ARG005
    )

    run_extract_facts_pipeline(_context(tmp_path, FakeExtractReporter()))

    assert not (tmp_path / ".scout-ai").exists()
