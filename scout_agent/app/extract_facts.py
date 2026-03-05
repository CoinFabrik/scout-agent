from __future__ import annotations

from argparse import Namespace

from scout_agent.app.command_config import resolve_extract_config
from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.app.errors import CommandError
from scout_agent.llm.providers import ProviderError
from scout_agent.runtime.extract.models import ExtractContext
from scout_agent.runtime.extract.pipeline import run_extract_facts_pipeline


def run_extract_facts_command(
    args: Namespace,
    *,
    output: ConsoleOutput,
) -> int:
    try:
        config = resolve_extract_config(args)
        context = ExtractContext(
            project_root=config.project_root,
            facts_path=config.facts_path,
            model_name=config.model_name,
            llm_mode=config.llm_mode,
            scout_files=config.scout_files,
            max_parallel_files=config.max_parallel_files,
            reporter=output.make_extract_progress_reporter(),
        )

        try:
            result = run_extract_facts_pipeline(context)
        finally:
            context.reporter.close()
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_extract_summary(result=result)

    return 0
