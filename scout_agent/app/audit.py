from __future__ import annotations

from argparse import Namespace

from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.configuration.scout_config import load_default_scout_config
from scout_agent.configuration.settings import (
    resolve_facts_path,
    resolve_llm_mode,
    resolve_model_name,
    resolve_project_root,
    resolve_report_path,
)
from scout_agent.runtime.audit.graph import (
    AuditContext,
    run_audit,
)
from scout_agent.runtime.audit.run import initialize_audit


def run_audit_command(
    args: Namespace,
    *,
    output: ConsoleOutput,
) -> int:
    project_root = resolve_project_root(args.project_root)
    scout_config = load_default_scout_config(project_root)
    facts_path = resolve_facts_path(project_root, args.facts_path)
    report_path = resolve_report_path(project_root, args.report_path)
    scout_files = scout_config.files if scout_config else None
    llm_mode = resolve_llm_mode(
        args.llm_mode,
        fallback=scout_config.mode if scout_config else None,
    )
    initialized = initialize_audit(
        project_root=project_root,
        facts_path=facts_path,
        scout_files=scout_files,
    )
    model_name = _resolve_audit_model_name(
        cli_value=args.model,
        scout_config_model=scout_config.model if scout_config else None,
        facts_model=initialized.facts_document.model,
    )
    context = AuditContext(
        project_root=project_root,
        report_path=report_path,
        facts_document=initialized.facts_document,
        facts_index=initialized.initial_state["facts_index"],
        model_name=model_name,
        llm_mode=llm_mode,
        reporter=output.make_audit_progress_reporter(),
    )

    try:
        final_state = run_audit(
            runtime=context, initial_state=initialized.initial_state
        )
    finally:
        context.reporter.close()

    output.print_audit_summary(
        report_path=context.report_path,
        final_state=final_state,
    )
    return 0


def _resolve_audit_model_name(
    *,
    cli_value: str | None,
    scout_config_model: str | None,
    facts_model: str,
) -> str:
    try:
        return resolve_model_name(cli_value, fallback=scout_config_model)
    except ValueError:
        return facts_model
