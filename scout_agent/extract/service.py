from dataclasses import dataclass
from pathlib import Path

from scout_agent.domain.facts import (
    aggregate_facts_file_path,
    compose_aggregate_facts_document,
    facts_file_path,
    write_aggregate_facts_document,
    write_facts_document,
)
from scout_agent.llm.providers import resolve_model_identifier
from scout_agent.extract.execution import execute_extract_tasks
from scout_agent.extract.reporting import ExtractProgressReporter
from scout_agent.rust_analysis.scope import discover_in_scope_files_or_raise


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int


@dataclass(frozen=True, slots=True)
class ExtractResult:
    project_root: Path
    facts_path: Path
    file_count: int
    function_count: int


def run_extract_service(
    *,
    request: ExtractRequest,
    reporter: ExtractProgressReporter,
) -> ExtractResult:
    normalized_model_name = resolve_model_identifier(request.model_name)
    discovered_files = discover_in_scope_files_or_raise(
        project_root=request.project_root,
        configured_paths=request.scout_files,
    )

    file_count = len(discovered_files)

    reporter.started(
        project_root=request.project_root,
        total_files=file_count,
        model_name=normalized_model_name,
        llm_mode=request.llm_mode,
    )

    extracted_file_results, function_count = execute_extract_tasks(
        discovered_files=discovered_files,
        project_root=request.project_root,
        reporter=reporter,
        model_name=normalized_model_name,
        llm_mode=request.llm_mode,
        max_parallel_files=request.max_parallel_files,
    )

    for document in extracted_file_results:
        write_facts_document(
            facts_file_path(request.facts_path, document.path),
            document,
        )

    write_aggregate_facts_document(
        aggregate_facts_file_path(request.facts_path),
        compose_aggregate_facts_document(extracted_file_results),
    )

    return ExtractResult(
        project_root=request.project_root,
        facts_path=request.facts_path,
        file_count=file_count,
        function_count=function_count,
    )
