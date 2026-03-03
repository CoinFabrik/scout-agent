from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scout_agent.domain.facts import FileFacts
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
        content_sha256: str,
        model_name: str,
        llm_mode: str,
    ) -> FileFacts: ...


@dataclass(frozen=True, slots=True)
class ExtractTask:
    index: int
    absolute_path: Path
    relative_path: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ExtractTaskSuccess:
    relative_path: str
    file_facts: FileFacts
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
) -> tuple[list[FileFacts], int]:
    effective_workers = min(max_parallel_files, len(tasks))
    total_tasks = len(tasks)
    function_count = 0
    results_by_index: dict[int, ExtractTaskSuccess] = {}
    failures: list[FileExtractionFailure] = []
    next_task_index = 0
    saw_failure = False

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        pending: dict[Future[ExtractTaskSuccess], ExtractTask] = {}
        remaining_tasks = list(tasks)

        next_task_index = _fill_pending_tasks(
            executor=executor,
            pending=pending,
            remaining_tasks=remaining_tasks,
            next_task_index=next_task_index,
            effective_workers=effective_workers,
            total_tasks=total_tasks,
            reporter=reporter,
            model_name=model_name,
            llm_mode=llm_mode,
            extract_file_facts=extract_file_facts,
            allow_submit=True,
        )

        while pending:
            done, _ = wait(set(pending.keys()), return_when=FIRST_COMPLETED)

            for future in done:
                task = pending.pop(future)
                try:
                    success = future.result()
                except Exception as exc:
                    reporter.file_failed(
                        index=task.index,
                        total=total_tasks,
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
                    saw_failure = True
                    continue

                results_by_index[task.index] = success
                function_count += success.function_count
                reporter.file_completed(
                    index=task.index,
                    total=total_tasks,
                    relative_path=success.relative_path,
                    function_count=success.function_count,
                )

            next_task_index = _fill_pending_tasks(
                executor=executor,
                pending=pending,
                remaining_tasks=remaining_tasks,
                next_task_index=next_task_index,
                effective_workers=effective_workers,
                total_tasks=total_tasks,
                reporter=reporter,
                model_name=model_name,
                llm_mode=llm_mode,
                extract_file_facts=extract_file_facts,
                allow_submit=not saw_failure,
            )

    if failures:
        raise ExtractFactsParallelError(failures)

    return (
        [results_by_index[index].file_facts for index in sorted(results_by_index)],
        function_count,
    )


def _fill_pending_tasks(
    *,
    executor: ThreadPoolExecutor,
    pending: dict[Future[ExtractTaskSuccess], ExtractTask],
    remaining_tasks: list[ExtractTask],
    next_task_index: int,
    effective_workers: int,
    total_tasks: int,
    reporter: Any,
    model_name: str,
    llm_mode: str,
    extract_file_facts: FileFactsExtractor,
    allow_submit: bool,
) -> int:
    if not allow_submit:
        return next_task_index

    while next_task_index < len(remaining_tasks) and len(pending) < effective_workers:
        task = remaining_tasks[next_task_index]
        _submit_task(
            executor=executor,
            pending=pending,
            task=task,
            total=total_tasks,
            reporter=reporter,
            model_name=model_name,
            llm_mode=llm_mode,
            extract_file_facts=extract_file_facts,
        )
        next_task_index += 1

    return next_task_index


def _submit_task(
    *,
    executor: ThreadPoolExecutor,
    pending: dict[Future[ExtractTaskSuccess], ExtractTask],
    task: ExtractTask,
    total: int,
    reporter: Any,
    model_name: str,
    llm_mode: str,
    extract_file_facts: FileFactsExtractor,
) -> None:
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
    pending[future] = task


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
        content_sha256=task.content_sha256,
        model_name=model_name,
        llm_mode=llm_mode,
    )
    return ExtractTaskSuccess(
        relative_path=task.relative_path,
        file_facts=extracted_file,
        function_count=len(extracted_file.functions),
    )
