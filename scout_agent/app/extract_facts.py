from scout_agent.runtime.extract.reporting import ExtractProgressReporter
from scout_agent.app.settings import resolve_extract_settings
from argparse import Namespace
from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.app.errors import CommandError
from scout_agent.llm.providers import ProviderError
from scout_agent.runtime.extract.models import ExtractContext
from scout_agent.runtime.extract.pipeline import run_extract_facts_pipeline


def run_extract_facts_command(args: Namespace, output: ConsoleOutput) -> int:
    try:
        settings = resolve_extract_settings(args)
        context = ExtractContext(
            settings=settings,
            reporter=ExtractProgressReporter(output._stdout),
        )

        try:
            result = run_extract_facts_pipeline(context)
        finally:
            context.reporter.close()
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_extract_summary(result=result)

    return 0
