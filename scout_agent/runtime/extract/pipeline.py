from scout_agent.runtime.extract.models import ExtractContext
from scout_agent.domain.facts import (
    aggregate_facts_file_path,
    compose_aggregate_facts_document,
    facts_file_path,
    write_aggregate_facts_document,
    write_facts_document,
)
from scout_agent.llm.providers import resolve_model_identifier
from scout_agent.runtime.extract.execution import ExtractTask, execute_extract_tasks
from scout_agent.runtime.extract.models import ExtractFactsPipelineResult
from scout_agent.runtime.source.discovery import discover_rust_files


def run_extract_facts_pipeline(context: ExtractContext) -> ExtractFactsPipelineResult:
    settings = context.settings
    resolved_model_name = resolve_model_identifier(settings.model_name)

    discovered_files = discover_rust_files(
        settings.project_root,
        configured_paths=settings.scout_files,
    )

    if not discovered_files:
        raise ValueError(
            "No in-scope production Rust source files were discovered under "
            f"{settings.project_root}"
        )

    tasks = [
        ExtractTask(
            index=index,
            relative_path=discovered_file.relative_path,
            analysis_text=discovered_file.analysis_text,
            content_sha256=discovered_file.content_sha256,
        )
        for index, discovered_file in enumerate(discovered_files, start=1)
    ]

    context.reporter.started(
        project_root=settings.project_root,
        total_files=len(discovered_files),
        model_name=resolved_model_name,
        llm_mode=settings.llm_mode,
    )

    extracted_file_results, function_count = execute_extract_tasks(
        tasks=tasks,
        project_root=settings.project_root,
        reporter=context.reporter,
        model_name=resolved_model_name,
        llm_mode=settings.llm_mode,
        max_parallel_files=settings.max_parallel_files,
    )

    for document in extracted_file_results:
        write_facts_document(
            facts_file_path(settings.facts_path, document.path),
            document,
        )
    write_aggregate_facts_document(
        aggregate_facts_file_path(settings.facts_path),
        compose_aggregate_facts_document(extracted_file_results),
    )

    return ExtractFactsPipelineResult(
        project_root=settings.project_root,
        facts_root=settings.facts_path,
        file_count=len(discovered_files),
        function_count=function_count,
    )
