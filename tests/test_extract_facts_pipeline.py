from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from scout_agent.domain.facts import (
    AuthorizationFact,
    FileFacts,
    FunctionFactBundle,
    FunctionFacts,
    SentinelValuesFact,
    TimeDependentStateFact,
    VectorParametersFact,
    load_facts_document,
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


def _state_dir(root: Path) -> Path:
    return root / ("." + "scout-ai")


def _make_file_facts(parsed_file, *, content_sha256: str) -> FileFacts:
    return FileFacts(
        path=parsed_file.relative_path,
        content_sha256=content_sha256,
        functions=[
            FunctionFacts(
                function_id=function.function_id,
                name=function.name,
                kind=function.kind,
                visibility=function.visibility,
                line_start=function.line_start,
                line_end=function.line_end,
                signature=function.signature,
                impl_target=function.impl_target,
                facts=FunctionFactBundle(
                    authorization=AuthorizationFact(
                        status="unknown",
                        reasoning="mock",
                        evidence=[],
                    ),
                    vector_parameters=VectorParametersFact(
                        status="unknown",
                        reasoning="mock",
                        parameters=[],
                    ),
                    time_dependent_state=TimeDependentStateFact(
                        status="unknown",
                        reasoning="mock",
                        evidence=[],
                    ),
                    sentinel_values=SentinelValuesFact(
                        status="unknown",
                        reasoning="mock",
                        values=[],
                    ),
                ),
            )
            for function in parsed_file.functions
        ],
    )


def test_extract_facts_pipeline_emits_reporter_events(tmp_path: Path, monkeypatch) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    (contracts / "b.rs").write_text("pub fn beta() {}\n", encoding="utf-8")
    (contracts / "c.rs").write_text("pub fn gamma() {}\n", encoding="utf-8")

    def fake_extract(parsed_file, *, content_sha256, model_name, llm_mode):
        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.12)
        elif parsed_file.relative_path.endswith("b.rs"):
            time.sleep(0.02)
        else:
            time.sleep(0.01)

        return _make_file_facts(parsed_file, content_sha256=content_sha256)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    reporter = FakeExtractReporter()
    result = run_extract_facts_pipeline(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=reporter,
        max_parallel_files=2,
    )

    assert result.file_count == 3
    assert reporter.events[0] == ("started", 3)
    assert [event for event in reporter.events if event[0] == "file_started"] == [
        ("file_started", 1, "contracts/a.rs"),
        ("file_started", 2, "contracts/b.rs"),
        ("file_started", 3, "contracts/c.rs"),
    ]
    assert {
        (event[1], event[2], event[3])
        for event in reporter.events
        if event[0] == "file_completed"
    } == {
        (1, "contracts/a.rs", 1),
        (2, "contracts/b.rs", 1),
        (3, "contracts/c.rs", 1),
    }


def test_extract_facts_pipeline_preserves_deterministic_file_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")
    (contracts / "b.rs").write_text("pub fn beta() {}\n", encoding="utf-8")
    (contracts / "c.rs").write_text("pub fn gamma() {}\n", encoding="utf-8")

    def fake_extract(parsed_file, *, content_sha256, model_name, llm_mode):
        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.12)
        elif parsed_file.relative_path.endswith("b.rs"):
            time.sleep(0.01)
        else:
            time.sleep(0.03)

        return _make_file_facts(parsed_file, content_sha256=content_sha256)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    reporter = FakeExtractReporter()
    result = run_extract_facts_pipeline(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=reporter,
        max_parallel_files=2,
    )

    assert result.file_count == 3
    assert result.function_count == 3

    loaded = load_facts_document(tmp_path / "FACTS.yaml")
    assert [file_facts.path for file_facts in loaded.files] == [
        "contracts/a.rs",
        "contracts/b.rs",
        "contracts/c.rs",
    ]


def test_extract_facts_pipeline_bounds_concurrency(tmp_path: Path, monkeypatch) -> None:
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

    def fake_extract(parsed_file, *, content_sha256, model_name, llm_mode):
        nonlocal active, max_seen
        with lock:
            active += 1
            max_seen = max(max_seen, active)
        time.sleep(0.05)
        with lock:
            active -= 1

        return _make_file_facts(parsed_file, content_sha256=content_sha256)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    run_extract_facts_pipeline(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=FakeExtractReporter(),
        max_parallel_files=2,
    )

    assert max_seen <= 2


def test_extract_facts_pipeline_fails_after_in_flight_tasks(
    tmp_path: Path,
    monkeypatch,
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

    def fake_extract(parsed_file, *, content_sha256, model_name, llm_mode):
        with lock:
            started_paths.append(parsed_file.relative_path)

        if parsed_file.relative_path.endswith("a.rs"):
            time.sleep(0.05)
            raise ValueError("broken inventory")

        time.sleep(0.12)
        return _make_file_facts(parsed_file, content_sha256=content_sha256)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    reporter = FakeExtractReporter()

    with pytest.raises(ExtractFactsParallelError, match="contracts/a.rs") as exc_info:
        run_extract_facts_pipeline(
            project_root=tmp_path,
            facts_path=tmp_path / "FACTS.yaml",
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            reporter=reporter,
            max_parallel_files=2,
        )

    assert sorted(started_paths) == ["contracts/a.rs", "contracts/b.rs"]
    assert [event for event in reporter.events if event[0] == "file_failed"] == [
        ("file_failed", 1, "contracts/a.rs", "ValueError", "broken inventory")
    ]
    assert "ValueError: broken inventory" in str(exc_info.value)
    assert not (tmp_path / "FACTS.yaml").exists()


def test_extract_facts_pipeline_rejects_empty_or_test_only_scopes(tmp_path: Path) -> None:
    reporter = FakeExtractReporter()

    with pytest.raises(
        ValueError,
        match="No in-scope production Rust source files were discovered",
    ):
        run_extract_facts_pipeline(
            project_root=tmp_path,
            facts_path=tmp_path / "FACTS.yaml",
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            reporter=reporter,
        )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "integration.rs").write_text("pub fn ignored() {}\n", encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="No in-scope production Rust source files were discovered",
    ):
        run_extract_facts_pipeline(
            project_root=tmp_path,
            facts_path=tmp_path / "FACTS.yaml",
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            reporter=FakeExtractReporter(),
        )

    assert reporter.events == []


def test_extract_facts_pipeline_does_not_create_scout_state_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "a.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")

    def fake_extract(parsed_file, *, content_sha256, model_name, llm_mode):
        return _make_file_facts(parsed_file, content_sha256=content_sha256)

    monkeypatch.setattr(
        "scout_agent.runtime.extract.pipeline.extract_file_facts_with_llm",
        fake_extract,
    )

    run_extract_facts_pipeline(
        project_root=tmp_path,
        facts_path=tmp_path / "FACTS.yaml",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=FakeExtractReporter(),
    )

    assert not _state_dir(tmp_path).exists()
