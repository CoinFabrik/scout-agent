from __future__ import annotations

import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from langgraph.graph.state import RunnableConfig

from scout_agent.audit.graph.builder import build_audit_graph
from scout_agent.audit.graph.context import AuditContext
from scout_agent.audit.initialization import initialize_audit
from scout_agent.audit.io.report_writer import write_report
from scout_agent.audit.io.reporting import AuditProgressReporter
from scout_agent.domain.audit import AuditState
from scout_agent.llm.providers import resolve_model_identifier


@dataclass(frozen=True, slots=True)
class AuditRequest:
    project_root: Path
    facts_path: Path
    report_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int
    recursion_limit: int
    agent_read_limit: int
    agent_grep_limit: int
    extra_prompt: str | None
    thread_id: str | None


@dataclass(frozen=True, slots=True)
class AuditResult:
    report_path: Path
    final_state: AuditState


def run_audit_service(
    *,
    request: AuditRequest,
    reporter: AuditProgressReporter,
) -> AuditResult:
    initialized = initialize_audit(
        project_root=request.project_root,
        facts_path=request.facts_path,
        scout_files=request.scout_files,
    )
    normalized_model_name = resolve_model_identifier(request.model_name)
    runtime = AuditContext(
        project_root=request.project_root,
        facts_path=request.facts_path,
        report_path=request.report_path,
        model_name=normalized_model_name,
        llm_mode=request.llm_mode,
        max_parallel_files=request.max_parallel_files,
        recursion_limit=request.recursion_limit,
        agent_read_limit=request.agent_read_limit,
        agent_grep_limit=request.agent_grep_limit,
        extra_prompt=request.extra_prompt,
        thread_id=request.thread_id,
        aggregate_facts_document=initialized.aggregate_facts_document,
        initial_state=initialized.initial_state,
        reporter=reporter,
    )

    final_state = _run_audit_graph(runtime=runtime)
    write_report(
        report_path=runtime.report_path,
        aggregate_facts_document=runtime.aggregate_facts_document,
        state=final_state,
    )
    return AuditResult(report_path=runtime.report_path, final_state=final_state)


def _run_audit_graph(*, runtime: AuditContext) -> AuditState:
    """Run the audit graph, resuming a checkpoint when a thread ID is provided."""
    runtime.reporter.started(
        project_root=runtime.project_root,
        total_files=len(runtime.initial_state["files_to_review"]),
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )
    thread_id = runtime.thread_id or _generate_thread_id(runtime.project_root)
    graph = build_audit_graph(runtime, outer_thread_id=thread_id)
    runtime.reporter.audit_thread_started(thread_id=thread_id)

    run_config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "max_concurrency": runtime.max_parallel_files,
        "recursion_limit": runtime.recursion_limit,
    }

    is_resume = False
    existing_state: AuditState | None = None
    has_pending_next = False
    if thread_id is not None:
        existing = graph.get_state(run_config)
        if existing is not None and existing.values:
            is_resume = True
            existing_state = cast(AuditState, existing.values)
            has_pending_next = bool(existing.next)
            prev_reviewed = set(existing_state.get("files_reviewed") or [])
            all_files = runtime.initial_state["files_to_review"]
            total_files = len(all_files)
            for idx, path in enumerate(all_files, start=1):
                if path in prev_reviewed:
                    runtime.reporter.file_skipped(
                        index=idx,
                        total=total_files,
                        current_file=path,
                    )
            if existing_state.get("execution_path_consistency_completed"):
                runtime.reporter.execution_path_consistency_skipped()

    if is_resume:
        resume_input = None if has_pending_next else _build_resume_input(existing_state)
        result = (
            existing_state
            if resume_input is None
            and not has_pending_next
            and existing_state is not None
            else cast(AuditState, graph.invoke(resume_input, config=run_config))
        )
    else:
        result = cast(
            AuditState, graph.invoke(runtime.initial_state, config=run_config)
        )

    return result


def _generate_thread_id(project_root: Path) -> str:
    project_hash = sha256(str(project_root.resolve()).encode()).hexdigest()[:12]
    run_hash = uuid.uuid4().hex[:8]
    return f"audit_{project_hash}_{run_hash}"


def _build_resume_input(state: AuditState | None) -> dict[str, Any] | None:
    if state is None:
        return None

    files_to_review = list(state.get("files_to_review") or [])
    epc_completed = bool(state.get("execution_path_consistency_completed"))
    if not files_to_review and epc_completed:
        return None

    return {
        "files_to_review": files_to_review,
        "execution_path_consistency_completed": epc_completed,
        "retry_generations": dict(state.get("retry_generations") or {}),
    }
