from argparse import Namespace

from scout_agent.config.settings import ResolvedExtractSettings, resolve_extract_settings
from scout_agent.cli.output.console import ConsoleOutput
from scout_agent.cli.errors import CommandError
from scout_agent.extract.service import ExtractRequest, run_extract_service
from scout_agent.llm.providers import ProviderError


def run_extract_facts_command(args: Namespace, output: ConsoleOutput) -> int:
    try:
        settings = resolve_extract_settings(
            project_root=args.project_root,
            facts_path=args.facts_path,
            model=args.model,
            llm_mode=args.llm_mode,
            max_parallel_files=getattr(args, "max_parallel_files", None),
        )
        request = _build_extract_request(settings)
        line_sink = output.make_progress_sink()
        reporter = output.make_extract_progress_reporter(line_sink=line_sink)
        result = run_extract_service(request=request, reporter=reporter)
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_extract_summary(result)
    return 0


def _build_extract_request(settings: ResolvedExtractSettings) -> ExtractRequest:
    return ExtractRequest(
        project_root=settings.project_root,
        facts_path=settings.facts_path,
        model_name=settings.model_name,
        llm_mode=settings.llm_mode,
        scout_files=settings.scout_files,
        max_parallel_files=settings.max_parallel_files,
    )
