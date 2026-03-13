from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from scout_agent.domain.facts import FactsDocument
from scout_agent.runtime.time_utils import utc_now_iso
from scout_agent.runtime.extract.facts_extractor import extract_file_facts_with_llm
from scout_agent.runtime.extract.models import (
    ExtractFactsParallelError,
    ExtractProgressReporter,
    FileExtractionFailure,
)
from scout_agent.runtime.source.rust_parser import parse_rust_source


@dataclass(frozen=True, slots=True)
class ExtractTask:
    index: int
    relative_path: str
    analysis_text: str
    content_sha256: str


def execute_extract_tasks(
    *,
    tasks: list[ExtractTask],
    project_root: Path,
    reporter: ExtractProgressReporter,
    model_name: str,
    llm_mode: str,
    max_parallel_files: int,
) -> tuple[list[FactsDocument], int]:
    total = len(tasks)
    failures: list[FileExtractionFailure] = []
    results: dict[int, FactsDocument] = {}
    function_count = 0

    with ThreadPoolExecutor(max_workers=max_parallel_files) as executor:
        future_to_task: dict[Future[FactsDocument], ExtractTask] = {}

        for task in tasks:
            reporter.file_started(
                index=task.index,
                total=total,
                relative_path=task.relative_path,
            )
            future = executor.submit(
                _run_extract_task,
                task,
                project_root=project_root,
                model_name=model_name,
                llm_mode=llm_mode,
            )
            future_to_task[future] = task

        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                document = future.result()
                function_count += len(document.functions)
                results[task.index] = document
                reporter.file_completed(
                    index=task.index,
                    total=total,
                    relative_path=document.path,
                    function_count=len(document.functions),
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

    return ([results[i] for i in sorted(results)], function_count)


def _run_extract_task(
    task: ExtractTask,
    *,
    project_root: Path,
    model_name: str,
    llm_mode: str,
) -> FactsDocument:
    parsed_file = parse_rust_source(
        task.analysis_text.encode("utf-8"),
        relative_path=task.relative_path,
    )
    extracted_file = extract_file_facts_with_llm(
        parsed_file,
        model_name=model_name,
        llm_mode=llm_mode,
    )
    return FactsDocument(
        generated_at_utc=utc_now_iso(),
        project_root=str(project_root),
        model=model_name,
        llm_mode=llm_mode,
        path=task.relative_path,
        content_sha256=task.content_sha256,
        functions=extracted_file,
    )
