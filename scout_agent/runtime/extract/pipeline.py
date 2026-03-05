from __future__ import annotations
from scout_agent.runtime.extract.models import ExtractContext

from scout_agent.domain.facts import FactsDocument, write_facts_document
from scout_agent.llm.providers import resolve_model_identifier
from scout_agent.runtime.time_utils import utc_now_iso
from scout_agent.runtime.extract.execution import (
    build_tasks,
    execute_extract_tasks,
)
from scout_agent.runtime.extract.models import (
    ExtractFactsParallelError,
    ExtractFactsPipelineResult,
)
from scout_agent.runtime.extract.facts_extractor import extract_file_facts_with_llm
from scout_agent.runtime.source.discovery import (
    compute_scope_fingerprint,
    discover_rust_files,
)

__all__ = [
    "ExtractFactsParallelError",
    "ExtractFactsPipelineResult",
    "run_extract_facts_pipeline",
]


def run_extract_facts_pipeline(context: ExtractContext) -> ExtractFactsPipelineResult:
    # Same as before, this could be reworked, moved to CLI parsing, etc..
    resolved_model_name = resolve_model_identifier(context.model_name)

    discovered_files = discover_rust_files(
        context.project_root,
        configured_paths=context.scout_files,
    )

    if not discovered_files:
        raise ValueError(
            "No in-scope production Rust source files were discovered under "
            f"{context.project_root}"
        )

    tasks = build_tasks(discovered_files)
    scope_fingerprint = compute_scope_fingerprint(discovered_files)

    context.reporter.started(
        project_root=context.project_root,
        total_files=len(discovered_files),
        model_name=resolved_model_name,
        llm_mode=context.llm_mode,
    )

    extracted_file_results, function_count = execute_extract_tasks(
        tasks=tasks,
        reporter=context.reporter,
        model_name=resolved_model_name,
        llm_mode=context.llm_mode,
        max_parallel_files=context.max_parallel_files,
        extract_file_facts=extract_file_facts_with_llm,
    )

    functions = {}
    for extracted_file in extracted_file_results:
        functions.update(extracted_file)

    document = FactsDocument(
        generated_at_utc=utc_now_iso(),
        project_root=str(context.project_root),
        model=resolved_model_name,
        llm_mode=context.llm_mode,
        scope_fingerprint=scope_fingerprint,
        functions=functions,
    )
    write_facts_document(context.facts_path, document)

    return ExtractFactsPipelineResult(
        project_root=context.project_root,
        facts_path=context.facts_path,
        file_count=len(discovered_files),
        function_count=function_count,
        scope_fingerprint=scope_fingerprint,
    )
