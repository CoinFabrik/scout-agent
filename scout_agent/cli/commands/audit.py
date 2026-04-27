from argparse import Namespace

from scout_agent.cli.output.console import ConsoleOutput
from scout_agent.cli.errors import CommandError
from scout_agent.config.settings import ResolvedAuditSettings, resolve_audit_settings
from scout_agent.audit.service import AuditRequest, run_audit_service
from scout_agent.llm.providers import ProviderError


def run_audit_command(args: Namespace, output: ConsoleOutput) -> int:
    try:
        settings = resolve_audit_settings(
            project_root=args.project_root,
            facts_path=args.facts_path,
            report_path=args.report_path,
            model=args.model,
            llm_mode=args.llm_mode,
            extra_prompt=args.extra_prompt,
            max_parallel_files=getattr(args, "max_parallel_files", None),
            agent_read_limit=getattr(args, "agent_read_limit", None),
            agent_grep_limit=getattr(args, "agent_grep_limit", None),
            resume=getattr(args, "resume", None),
        )
        request = _build_audit_request(settings)
        line_sink = output.make_progress_sink()
        session = output.make_audit_progress_session(line_sink=line_sink)
        result = session.run(
            lambda: run_audit_service(request=request, reporter=session.reporter)
        )
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_audit_summary(
        report_path=result.report_path,
        final_state=result.final_state,
    )
    return 0


def _build_audit_request(settings: ResolvedAuditSettings) -> AuditRequest:
    return AuditRequest(
        project_root=settings.project_root,
        facts_path=settings.facts_path,
        report_path=settings.report_path,
        model_name=settings.model_name,
        llm_mode=settings.llm_mode,
        scout_files=settings.scout_files,
        max_parallel_files=settings.max_parallel_files,
        recursion_limit=settings.recursion_limit,
        agent_read_limit=settings.agent_read_limit,
        agent_grep_limit=settings.agent_grep_limit,
        extra_prompt=settings.extra_prompt,
        thread_id=settings.thread_id,
    )
