from scout_agent.runtime.audit.engine.tools import build_readonly_tools
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass
from pathlib import Path

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from scout_agent.domain.audit import AuditState, FileAuditResponse, Finding
from scout_agent.domain.facts import (
    AggregateFactsDocument,
    FunctionSummary,
    facts_file_path,
    load_facts_document,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.engine.audit_callbacks import RuntimeProgressHandler
from scout_agent.runtime.audit.engine.experts import (
    SUBAGENT_MANIFEST,
    CompiledSubAgent,
    build_expert_subagents,
)
from scout_agent.runtime.audit.io.reporting import AuditProgressReporter
from scout_agent.runtime.audit.prompts.audit_prompts import (
    build_execution_path_consistency_audit_prompt,
    build_execution_path_consistency_system_prompt,
    build_parent_audit_prompt,
    build_parent_system_prompt,
)


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    facts_path: Path
    report_path: Path
    aggregate_facts_document: AggregateFactsDocument
    model_name: str
    llm_mode: str
    max_parallel_files: int
    recursion_limit: int
    agent_read_limit: int
    reporter: AuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class FileAuditFailure:
    index: int
    relative_path: str
    error_type: str
    message: str


class AuditParallelError(ValueError):
    def __init__(self, failures: list[FileAuditFailure]) -> None:
        self.failures = failures
        super().__init__(self._render_message(failures))

    @staticmethod
    def _render_message(failures: list[FileAuditFailure]) -> str:
        lines = [f"audit failed for {len(failures)} file(s):"]
        for failure in sorted(failures, key=lambda item: item.index):
            lines.append(
                f"- {failure.relative_path}: {failure.error_type}: {failure.message}"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _AuditTask:
    index: int
    relative_path: str


@dataclass(frozen=True, slots=True)
class _CompletedFileAudit:
    index: int
    relative_path: str
    response: FileAuditResponse


def run_audit(
    *,
    runtime: AuditContext,
) -> AuditState:
    state = runtime.initial_state
    files_to_review = list(state["files_to_review"])
    total_files = len(files_to_review)
    allowed_paths = list(files_to_review)
    finding_keys: set[str] = set()

    runtime.reporter.started(
        project_root=runtime.project_root,
        total_files=total_files,
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )

    tasks = [
        _AuditTask(index=index, relative_path=relative_path)
        for index, relative_path in enumerate(files_to_review, start=1)
    ]
    completed_audits: list[_CompletedFileAudit] = []
    failures: list[FileAuditFailure] = []
    completed_count = 0
    next_task_index = 0
    stop_submission = False

    with ThreadPoolExecutor(max_workers=runtime.max_parallel_files + 1) as executor:
        active: dict[Future[_CompletedFileAudit], _AuditTask] = {}
        runtime.reporter.execution_path_consistency_started()
        execution_path_consistency_future: Future[FileAuditResponse] | None = (
            executor.submit(
                _run_execution_path_consistency_task,
                runtime=runtime,
                allowed_paths=allowed_paths,
            )
        )

        while (
            active
            or next_task_index < len(tasks)
            or execution_path_consistency_future is not None
        ):
            while (
                not stop_submission
                and len(active) < runtime.max_parallel_files
                and next_task_index < len(tasks)
            ):
                task = tasks[next_task_index]
                next_task_index += 1
                _mark_file_started(runtime=runtime, task=task, total_files=total_files)
                future = executor.submit(
                    _run_audit_task,
                    task=task,
                    runtime=runtime,
                    allowed_paths=allowed_paths,
                )
                active[future] = task

            wait_targets: set[Future[object]] = {future for future in active}
            if execution_path_consistency_future is not None:
                wait_targets.add(execution_path_consistency_future)

            if not wait_targets:
                break

            done, _ = wait(wait_targets, return_when=FIRST_COMPLETED)
            for future in done:
                if (
                    execution_path_consistency_future is not None
                    and future is execution_path_consistency_future
                ):
                    try:
                        execution_path_consistency_response = future.result()
                    except CancelledError:
                        execution_path_consistency_future = None
                        continue
                    except Exception as exc:
                        failures.append(
                            FileAuditFailure(
                                index=0,
                                relative_path="execution_path_consistency",
                                error_type=type(exc).__name__,
                                message=str(exc),
                            )
                        )
                        execution_path_consistency_future = None
                        stop_submission = True
                        for pending_future in active:
                            pending_future.cancel()
                        continue

                    execution_path_consistency_future = None
                    _merge_findings(
                        state=state,
                        runtime=runtime,
                        response=execution_path_consistency_response,
                        finding_keys=finding_keys,
                    )
                    state["execution_path_consistency_completed"] = True
                    runtime.reporter.execution_path_consistency_completed()
                    continue

                task = active.pop(future)
                try:
                    completed_audit = future.result()
                except CancelledError:
                    continue
                except Exception as exc:
                    failures.append(
                        FileAuditFailure(
                            index=task.index,
                            relative_path=task.relative_path,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    stop_submission = True
                    if execution_path_consistency_future is not None:
                        execution_path_consistency_future.cancel()
                    for pending_future in active:
                        pending_future.cancel()
                    continue

                completed_count += 1
                completed_audits.append(completed_audit)
                _merge_completed_audit(
                    state=state,
                    runtime=runtime,
                    completed_audit=completed_audit,
                    reviewed_count=completed_count,
                    total_files=total_files,
                    finding_keys=finding_keys,
                )

    if failures:
        raise AuditParallelError(failures)

    state["files_reviewed"] = [
        completed.relative_path
        for completed in sorted(completed_audits, key=lambda item: item.index)
    ]
    state["files_to_review"] = []
    return state


def _mark_file_started(
    *,
    runtime: AuditContext,
    task: _AuditTask,
    total_files: int,
) -> None:
    runtime.reporter.file_started(
        index=task.index,
        total=total_files,
        current_file=task.relative_path,
    )


def _run_audit_task(
    *,
    task: _AuditTask,
    runtime: AuditContext,
    allowed_paths: list[str],
) -> _CompletedFileAudit:
    expert_subagents = build_expert_subagents(
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
        project_root=runtime.project_root,
        allowed_paths=allowed_paths,
        recursion_limit=runtime.recursion_limit,
        agent_read_limit=runtime.agent_read_limit,
        extra_prompt=runtime.extra_prompt,
    )
    response = _run_file_audit(
        runtime=runtime,
        current_file=task.relative_path,
        expert_subagents=expert_subagents,
    )
    return _CompletedFileAudit(
        index=task.index,
        relative_path=task.relative_path,
        response=response,
    )


def _run_execution_path_consistency_task(
    *,
    runtime: AuditContext,
    allowed_paths: list[str],
) -> FileAuditResponse:
    return _run_execution_path_consistency_audit(
        runtime=runtime,
        allowed_paths=allowed_paths,
    )


def _merge_completed_audit(
    *,
    state: AuditState,
    runtime: AuditContext,
    completed_audit: _CompletedFileAudit,
    reviewed_count: int,
    total_files: int,
    finding_keys: set[str],
) -> None:
    _merge_findings(
        state=state,
        runtime=runtime,
        response=completed_audit.response,
        finding_keys=finding_keys,
    )

    runtime.reporter.file_completed(
        reviewed=reviewed_count,
        total=total_files,
        current_file=completed_audit.relative_path,
    )


def _merge_findings(
    *,
    state: AuditState,
    runtime: AuditContext,
    response: FileAuditResponse,
    finding_keys: set[str],
) -> None:
    for finding in response.findings:
        finding_key = _make_finding_key(finding)
        if finding_key in finding_keys:
            continue
        finding_keys.add(finding_key)
        state["verified_findings"].append(finding)
        runtime.reporter.finding_verified(
            total_verified_findings=len(state["verified_findings"]),
            finding=finding,
        )


def _run_file_audit(
    *,
    runtime: AuditContext,
    current_file: str,
    expert_subagents: list[CompiledSubAgent],
) -> FileAuditResponse:
    current_file_path = (runtime.project_root / current_file).resolve()
    current_file_facts = _load_current_file_facts(
        facts_root=runtime.facts_path,
        current_file=current_file,
    )

    system_prompt = build_parent_system_prompt(
        current_file=current_file_path.as_posix(),
        current_file_facts=current_file_facts,
        extra_prompt=runtime.extra_prompt,
    )
    model = build_chat_model(runtime.model_name, runtime.llm_mode)

    agent = create_deep_agent(
        model=model,
        system_prompt=_escape_prompt_text(system_prompt),
        tools=build_readonly_tools(
            root_dir=runtime.project_root,
            scope_path=current_file_path,
            agent_read_limit=runtime.agent_read_limit,
            default_grep_path=current_file_path.as_posix(),
        ),
        subagents=expert_subagents,
        response_format=ProviderStrategy(FileAuditResponse, strict=True),
        name="scout-agent",
    )

    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names={spec.name for spec in SUBAGENT_MANIFEST},
        current_file=current_file,
    )

    result = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content=build_parent_audit_prompt(
                        current_file=current_file_path.as_posix(),
                        extra_prompt=runtime.extra_prompt,
                    )
                )
            ]
        },
        config={
            "callbacks": [callback_handler],
            "recursion_limit": runtime.recursion_limit,
        },
    )
    return _parse_structured_audit_response(
        result=result,
        actor_name=current_file,
    )


def _run_execution_path_consistency_audit(
    *,
    runtime: AuditContext,
    allowed_paths: list[str],
) -> FileAuditResponse:
    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    system_prompt = build_execution_path_consistency_system_prompt(
        aggregate_facts_document=runtime.aggregate_facts_document,
        extra_prompt=runtime.extra_prompt,
    )

    agent = create_agent(
        model=model,
        system_prompt=_escape_prompt_text(system_prompt),
        tools=build_readonly_tools(
            root_dir=runtime.project_root,
            scope_path=runtime.project_root,
            agent_read_limit=0,
        ),
        response_format=ProviderStrategy(FileAuditResponse, strict=True),
        name="execution_path_consistency",
    )

    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names=set(),
        current_file="repo",
        primary_actor_name="execution_path_consistency",
    )

    result = agent.invoke(
        {
            "messages": [
                HumanMessage(
                    content=build_execution_path_consistency_audit_prompt(
                        extra_prompt=runtime.extra_prompt
                    )
                )
            ]
        },
        config={
            "callbacks": [callback_handler],
            "recursion_limit": runtime.recursion_limit,
        },
    )
    return _parse_structured_audit_response(
        result=result,
        actor_name="execution_path_consistency",
    )


def _parse_structured_audit_response(
    *,
    result: dict[str, object],
    actor_name: str,
) -> FileAuditResponse:
    structured = result.get("structured_response")
    if structured is None:
        raise ValueError(f"Structured response missing for {actor_name}.")

    try:
        return (
            structured
            if isinstance(structured, FileAuditResponse)
            else FileAuditResponse.model_validate(structured)
        )
    except ValueError as exc:
        raise ValueError(
            f"Structured response failed validation for {actor_name}: {exc}"
        ) from exc


def _make_finding_key(finding: Finding) -> str:
    return "|".join(
        [
            finding.pattern,
            finding.severity,
            finding.location,
            finding.description,
            finding.evidence,
        ]
    )


def _escape_prompt_text(prompt_text: str) -> str:
    return prompt_text.replace("{", "{{").replace("}", "}}")


def _load_current_file_facts(
    *,
    facts_root: Path,
    current_file: str,
) -> dict[str, FunctionSummary]:
    return load_facts_document(facts_file_path(facts_root, current_file)).functions
