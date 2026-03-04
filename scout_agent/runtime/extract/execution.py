from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scout_agent.domain.facts import FunctionSummary
from scout_agent.runtime.extract.models import (
    ExtractFactsParallelError,
    FileExtractionFailure,
)
from scout_agent.runtime.source.discovery import DiscoveredRustFile
from scout_agent.runtime.source.rust_parser import ParsedRustFile, parse_rust_source
from scout_agent.runtime.source.source_filter import build_analysis_source


class FileFactsExtractor(Protocol):
    def __call__(
        self,
        parsed_file: ParsedRustFile,
        *,
        model_name: str,
        llm_mode: str,
    ) -> dict[str, FunctionSummary]: ...


@dataclass(frozen=True, slots=True)
class ExtractTask:
    index: int
    absolute_path: Path
    relative_path: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractTaskSuccess:
    relative_path: str
    function_summaries: dict[str, FunctionSummary]
    function_count: int


def build_tasks(discovered_files: list[DiscoveredRustFile]) -> list[ExtractTask]:
    return [
        ExtractTask(
            index=index,
            absolute_path=discovered_file.absolute_path,
            relative_path=discovered_file.relative_path,
            content_sha256=discovered_file.content_sha256,
        )
        for index, discovered_file in enumerate(discovered_files, start=1)
    ]


def execute_extract_tasks(
    *,
    tasks: list[ExtractTask],
    reporter: Any,
    model_name: str,
    llm_mode: str,
    max_parallel_files: int,
    extract_file_facts: FileFactsExtractor,
) -> tuple[list[dict[str, FunctionSummary]], int]:
    total = len(tasks)
    failures: list[FileExtractionFailure] = []
    results: dict[int, ExtractTaskSuccess] = {}
    function_count = 0

    with ThreadPoolExecutor(max_workers=max_parallel_files) as executor:
        future_to_task: dict[Future[ExtractTaskSuccess], ExtractTask] = {}

        for task in tasks:
            reporter.file_started(
                index=task.index,
                total=total,
                relative_path=task.relative_path,
            )
            future = executor.submit(
                _run_extract_task,
                task,
                model_name=model_name,
                llm_mode=llm_mode,
                extract_file_facts=extract_file_facts,
            )
            future_to_task[future] = task

        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                success = future.result()
                results[task.index] = success
                function_count += success.function_count
                reporter.file_completed(
                    index=task.index,
                    total=total,
                    relative_path=success.relative_path,
                    function_count=success.function_count,
                )
            except Exception as exc:
                reporter.file_failed(
                    index=task.index,
                    total=total,
                    relative_path=task.relative_path,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                failures.append(
                    FileExtractionFailure(
                        relative_path=task.relative_path,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                for f in future_to_task:
                    f.cancel()

    if failures:
        raise ExtractFactsParallelError(failures)

    return (
        [results[i].function_summaries for i in sorted(results)],
        function_count,
    )


def _run_extract_task(
    task: ExtractTask,
    *,
    model_name: str,
    llm_mode: str,
    extract_file_facts: FileFactsExtractor,
) -> ExtractTaskSuccess:
    analysis_source = build_analysis_source(
        path=task.absolute_path,
        relative_path=task.relative_path,
    )
    parsed_file = parse_rust_source(
        analysis_source.analysis_text.encode("utf-8"),
        relative_path=task.relative_path,
    )
    extracted_file = extract_file_facts(
        parsed_file,
        model_name=model_name,
        llm_mode=llm_mode,
    )
    return ExtractTaskSuccess(
        relative_path=task.relative_path,
        function_summaries=extracted_file,
        function_count=len(extracted_file),
    )
