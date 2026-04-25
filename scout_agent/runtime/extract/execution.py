from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from scout_agent.domain.facts import FactsDocument
from scout_agent.runtime.extract.facts_extractor import extract_file_facts_with_llm
from scout_agent.runtime.extract.reporting import ExtractProgressReporter
from scout_agent.runtime.source.discovery import DiscoveredRustFile
from scout_agent.runtime.source.rust_parser import parse_rust_source
from scout_agent.time_utils import utc_now_iso


@dataclass(frozen=True, slots=True)
class FileExtractionFailure:
    relative_path: str
    error_type: str
    message: str


class ExtractFactsParallelError(ValueError):
    def __init__(self, failures: list[FileExtractionFailure]) -> None:
        self.failures = failures
        super().__init__(self._render_message(failures))

    @staticmethod
    def _render_message(failures: list[FileExtractionFailure]) -> str:
        lines = [f"extract-facts failed for {len(failures)} file(s):"]
        for failure in failures:
            lines.append(
                f"- {failure.relative_path}: {failure.error_type}: {failure.message}"
            )
        return "\n".join(lines)


def execute_extract_tasks(
    *,
    discovered_files: list[DiscoveredRustFile],
    project_root: Path,
    reporter: ExtractProgressReporter,
    model_name: str,
    llm_mode: str,
    max_parallel_files: int,
) -> tuple[list[FactsDocument], int]:
    total = len(discovered_files)
    failures: list[FileExtractionFailure] = []
    results: dict[int, FactsDocument] = {}
    function_count = 0

    with ThreadPoolExecutor(max_workers=max_parallel_files) as executor:
        future_to_file: dict[Future[FactsDocument], tuple[int, DiscoveredRustFile]] = {}

        for index, discovered_file in enumerate(discovered_files, start=1):
            reporter.file_started(
                index=index,
                total=total,
                relative_path=discovered_file.relative_path,
            )
            future = executor.submit(
                _run_extract_task,
                discovered_file,
                project_root=project_root,
                model_name=model_name,
                llm_mode=llm_mode,
            )
            future_to_file[future] = (index, discovered_file)

        for future in as_completed(future_to_file):
            index, discovered_file = future_to_file[future]
            try:
                document = future.result()
                function_count += len(document.functions)
                results[index] = document
                reporter.file_completed(
                    index=index,
                    total=total,
                    relative_path=document.path,
                    function_count=len(document.functions),
                )
            except Exception as exc:
                reporter.file_failed(
                    index=index,
                    total=total,
                    relative_path=discovered_file.relative_path,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                failures.append(
                    FileExtractionFailure(
                        relative_path=discovered_file.relative_path,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                for f in future_to_file:
                    f.cancel()

    if failures:
        raise ExtractFactsParallelError(failures)

    return ([results[i] for i in sorted(results)], function_count)


def _run_extract_task(
    discovered_file: DiscoveredRustFile,
    *,
    project_root: Path,
    model_name: str,
    llm_mode: str,
) -> FactsDocument:
    parsed_file = parse_rust_source(
        discovered_file.analysis_text.encode("utf-8"),
        relative_path=discovered_file.relative_path,
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
        path=discovered_file.relative_path,
        content_sha256=discovered_file.content_sha256,
        functions=extracted_file,
    )
