from __future__ import annotations
from argparse import Namespace
from scout_agent.runtime.extract.models import ExtractContext
from scout_agent.configuration.scout_config import load_default_scout_config
from scout_agent.configuration.settings import (
    resolve_facts_path,
    resolve_llm_mode,
    resolve_max_parallel_files,
    resolve_model_name,
    resolve_project_root,
)
from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.runtime.extract.pipeline import run_extract_facts_pipeline


def run_extract_facts_command(
    args: Namespace,
    *,
    output: ConsoleOutput,
) -> int:
    project_root = resolve_project_root(args.project_root)
    facts_path = resolve_facts_path(project_root, args.facts_path)
    scout_config = load_default_scout_config(project_root)

    model_name = resolve_model_name(
        args.model,
        fallback=scout_config.model if scout_config else None,
    )
    llm_mode = resolve_llm_mode(
        args.llm_mode,
        fallback=scout_config.mode if scout_config else None,
    )
    max_parallel_files = resolve_max_parallel_files(
        args.max_parallel_files,
        fallback=scout_config.max_parallel_files if scout_config else None,
    )

    context = ExtractContext(
        project_root=project_root,
        facts_path=facts_path,
        model_name=model_name,
        llm_mode=llm_mode,
        scout_files=scout_config.files if scout_config else None,
        max_parallel_files=max_parallel_files,
        reporter=output.make_extract_progress_reporter(),
    )

    try:
        result = run_extract_facts_pipeline(context)
    finally:
        context.reporter.close()

    output.print_extract_summary(result=result)

    return 0
