from argparse import Namespace

from scout_agent.app.settings import resolve_audit_config
from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.app.errors import CommandError
from scout_agent.domain.audit import AuditState
from scout_agent.llm.providers import ProviderError
from scout_agent.runtime.audit.engine.graph import AuditContext, run_audit
from scout_agent.runtime.audit.io.report_writer import write_report
from scout_agent.runtime.audit.run import initialize_audit


def run_audit_command(args: Namespace, output: ConsoleOutput) -> int:
    try:
        config = resolve_audit_config(args)
        initialized = initialize_audit(
            project_root=config.project_root,
            facts_path=config.facts_path,
            scout_files=config.scout_files,
        )
        model_name = config.model_name or initialized.aggregate_facts_document.model
        line_sink = output.make_progress_sink()
        session = output.make_audit_progress_session(line_sink=line_sink)
        context = AuditContext(
            project_root=config.project_root,
            facts_path=config.facts_path,
            report_path=config.report_path,
            aggregate_facts_document=initialized.aggregate_facts_document,
            model_name=model_name,
            llm_mode=config.llm_mode,
            max_parallel_files=config.max_parallel_files,
            recursion_limit=config.recursion_limit,
            agent_read_limit=config.agent_read_limit,
            extra_prompt=config.extra_prompt,
            initial_state=initialized.initial_state,
            reporter=session.reporter,
        )
        final_state = session.run(lambda: _run_audit_task(context))
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_audit_summary(
        report_path=context.report_path,
        final_state=final_state,
    )
    return 0


def _run_audit_task(context: AuditContext) -> AuditState:
    final_state = run_audit(runtime=context)
    write_report(
        report_path=context.report_path,
        aggregate_facts_document=context.aggregate_facts_document,
        state=final_state,
    )
    return final_state
