from hashlib import sha256
from pathlib import Path
from typing import Any
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph, RunnableConfig
from langgraph.types import Send

from scout_agent.runtime.audit.engine.context import AuditContext
from scout_agent.runtime.audit.engine.runners import (
    run_file_audit,
    run_execution_path_consistency_audit,
    _deduplicate_and_relativize,
)
from scout_agent.domain.audit import AuditFailure, AuditState
from scout_agent.runtime.audit.engine.experts import build_expert_subagents
from scout_agent.runtime.audit.engine.memory import get_sqlite_saver
from scout_agent.runtime.audit.engine.tools import PolicyViolationError

_EPC_RETRY_KEY = "__execution_path_consistency__"


def _dispatch_node(state: AuditState) -> dict:
    """Pass-through node: entry point before fan-out."""
    return {}


def _fanout_router(state: AuditState) -> list[Send]:
    """Conditional edge: emit one Send per file, plus one per standalone agent.

    Files already present in ``files_reviewed`` (from a previous checkpoint)
    are silently skipped so that a resumed audit only processes the remaining
    files.
    """
    already_reviewed: set[str] = set(state.get("files_reviewed") or [])
    retry_generations = state.get("retry_generations") or {}
    sends: list[Send] = []

    for index, relative_path in enumerate(state["files_to_review"], start=1):
        if relative_path in already_reviewed:
            continue
        sends.append(
            Send(
                "audit_file",
                {
                    "index": index,
                    "relative_path": relative_path,
                    "generation": retry_generations.get(relative_path, 0),
                },
            )
        )

    if not state.get("execution_path_consistency_completed"):
        sends.append(
            Send(
                "audit_execution_path_consistency",
                {"generation": retry_generations.get(_EPC_RETRY_KEY, 0)},
            )
        )

    return sends


def _make_audit_file_node(
    runtime: AuditContext,
    allowed_paths: list[str],
    total_files: int,
    outer_thread_id: str,
):
    """Factory: returns the audit_file node function with runtime in closure."""

    def audit_file_node(task: dict[str, Any]) -> dict[str, Any]:
        index: int = task["index"]
        relative_path: str = task["relative_path"]
        generation: int = task["generation"]

        try:
            runtime.reporter.file_started(
                index=index,
                total=total_files,
                current_file=relative_path,
            )
            expert_subagents = build_expert_subagents(
                model_name=runtime.model_name,
                llm_mode=runtime.llm_mode,
                project_root=runtime.project_root,
                allowed_paths=allowed_paths,
                recursion_limit=runtime.recursion_limit,
                agent_read_limit=runtime.agent_read_limit,
                agent_grep_limit=runtime.agent_grep_limit,
                extra_prompt=runtime.extra_prompt,
            )
            response = run_file_audit(
                runtime=runtime,
                current_file=relative_path,
                expert_subagents=expert_subagents,
                outer_thread_id=outer_thread_id,
                generation=generation,
            )
            findings = _deduplicate_and_relativize(
                response.findings, runtime.project_root
            )
            for finding in findings:
                runtime.reporter.finding_verified(
                    total_verified_findings=0,
                    finding=finding,
                )
            runtime.reporter.file_completed(
                reviewed=index,
                total=total_files,
                current_file=relative_path,
            )
            return {
                "files_reviewed": [relative_path],
                "verified_findings": findings,
                "failures": [],
                "retry_generations": {},
            }
        except Exception as exc:
            runtime.reporter.file_failed(
                current_file=relative_path,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            failure: AuditFailure = {
                "index": index,
                "relative_path": relative_path,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            retry_generation = (
                {relative_path: generation + 1}
                if _is_toxic_failure(exc)
                else {}
            )
            return {
                "failures": [failure],
                "files_reviewed": [],
                "verified_findings": [],
                "retry_generations": retry_generation,
            }

    return audit_file_node


def _make_epc_node(
    runtime: AuditContext,
    allowed_paths: list[str],
    outer_thread_id: str,
):
    """Factory: returns the execution path consistency node function with runtime in closure."""

    def epc_node(task: dict[str, Any]) -> dict[str, Any]:
        generation: int = task["generation"]
        try:
            runtime.reporter.execution_path_consistency_started()
            response = run_execution_path_consistency_audit(
                runtime=runtime,
                allowed_paths=allowed_paths,
                outer_thread_id=outer_thread_id,
                generation=generation,
            )
            findings = _deduplicate_and_relativize(
                response.findings, runtime.project_root
            )
            for finding in findings:
                runtime.reporter.finding_verified(
                    total_verified_findings=0,
                    finding=finding,
                )
            runtime.reporter.execution_path_consistency_completed()
            return {
                "execution_path_consistency_completed": True,
                "verified_findings": findings,
                "files_reviewed": [],
                "failures": [],
                "retry_generations": {},
            }
        except Exception as exc:
            runtime.reporter.file_failed(
                current_file="execution_path_consistency",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            failure: AuditFailure = {
                "index": 0,
                "relative_path": "execution_path_consistency",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            retry_generation = (
                {_EPC_RETRY_KEY: generation + 1}
                if _is_toxic_failure(exc)
                else {}
            )
            return {
                "failures": [failure],
                "files_reviewed": [],
                "verified_findings": [],
                "retry_generations": retry_generation,
            }

    return epc_node


def _make_collect_node(runtime: AuditContext):
    """Factory: returns the collect node function with runtime in closure."""

    def collect_node(state: AuditState) -> dict[str, Any]:
        # Log failures but do NOT raise — let the audit complete with partial results
        for failure in state["failures"]:
            runtime.reporter.file_failed(
                current_file=failure["relative_path"],
                error_type=failure["error_type"],
                message=failure["message"],
            )

        reviewed = set(state["files_reviewed"])
        return {
            "files_to_review": [
                path for path in state["files_to_review"] if path not in reviewed
            ],
        }

    return collect_node


def build_audit_graph(
    runtime: AuditContext,
    *,
    outer_thread_id: str,
) -> CompiledStateGraph:
    """Build and compile the audit StateGraph with SQLite checkpointer."""
    files_to_review = list(runtime.initial_state["files_to_review"])
    total_files = len(files_to_review)

    builder = StateGraph(AuditState)
    builder.add_node("dispatch", _dispatch_node)
    builder.add_node(
        "audit_file",
        _make_audit_file_node(runtime, files_to_review, total_files, outer_thread_id),
    )
    builder.add_node(
        "audit_execution_path_consistency",
        _make_epc_node(runtime, files_to_review, outer_thread_id),
    )
    builder.add_node("collect", _make_collect_node(runtime))

    builder.add_conditional_edges(
        "dispatch",
        _fanout_router,
        ["audit_file", "audit_execution_path_consistency"],
    )
    builder.add_edge("audit_file", "collect")
    builder.add_edge("audit_execution_path_consistency", "collect")
    builder.add_edge(START, "dispatch")
    builder.add_edge("collect", END)

    memory_dir = runtime.project_root / ".scout-ai"
    memory_dir.mkdir(parents=True, exist_ok=True)
    checkpointer = get_sqlite_saver(memory_dir / "memory.sqlite")
    return builder.compile(checkpointer=checkpointer)


def _generate_thread_id(project_root: Path) -> str:
    project_hash = sha256(str(project_root.resolve()).encode()).hexdigest()[:12]
    run_hash = uuid.uuid4().hex[:8]
    return f"audit_{project_hash}_{run_hash}"


def run_audit(*, runtime: AuditContext) -> AuditState:
    """Run the audit using a LangGraph StateGraph with parallel fan-out.

    When *thread_id* is provided the function checks for an existing
    checkpoint.  If one is found the graph is resumed (``invoke(None, ...)``)
    which replays from the last saved state instead of starting over.
    Already-reviewed files are reported as skipped before the graph runs.
    """
    runtime.reporter.started(
        project_root=runtime.project_root,
        total_files=len(runtime.initial_state["files_to_review"]),
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )
    thread_id = runtime.thread_id or _generate_thread_id(runtime.project_root)
    graph = build_audit_graph(runtime, outer_thread_id=thread_id)
    print(f"Thread ID: {thread_id}")

    run_config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "max_concurrency": runtime.max_parallel_files,
        "recursion_limit": runtime.recursion_limit,
    }

    # --- Decide: fresh run vs. resume ---
    is_resume = False
    existing_state: AuditState | None = None
    has_pending_next = False
    if thread_id is not None:
        existing = graph.get_state(run_config)
        if existing is not None and existing.values:
            is_resume = True
            existing_state = existing.values
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
        resume_input = (
            None
            if has_pending_next
            else _build_resume_input(existing_state)
        )
        result = (
            existing_state
            if resume_input is None and not has_pending_next and existing_state is not None
            else graph.invoke(resume_input, config=run_config)
        )
    else:
        result = graph.invoke(runtime.initial_state, config=run_config)

    return result


def _is_toxic_failure(exc: Exception) -> bool:
    if isinstance(exc, PolicyViolationError):
        return True

    error_type = type(exc).__name__
    if "StructuredOutput" in error_type:
        return True

    if isinstance(exc, ValueError) and str(exc).startswith("Structured response"):
        return True

    return False


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
