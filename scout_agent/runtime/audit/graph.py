from __future__ import annotations

import json
import logging
import re
from concurrent.futures import (
    FIRST_COMPLETED,
    CancelledError,
    Future,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass
from pathlib import Path

import yaml
from langchain.agents import create_agent
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import HumanMessage

from deepagents.graph import BASE_AGENT_PROMPT
from deepagents.middleware import SubAgentMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from scout_agent.domain.audit import AuditState, FileAuditResponse, Finding
from scout_agent.domain.facts import (
    AggregateFactsDocument,
    FunctionSummary,
    facts_file_path,
    load_facts_document,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.audit_backend import (
    FileScopedAuditBackend,
    RepoScopedAuditBackend,
)
from scout_agent.runtime.audit.audit_callbacks import RuntimeProgressHandler
from scout_agent.runtime.audit.audit_prompts import (
    build_execution_path_consistency_audit_prompt,
    build_execution_path_consistency_system_prompt,
    build_parent_audit_prompt,
    build_parent_system_prompt,
    get_supervisor_few_shots,
)
from scout_agent.runtime.audit.dump import AuditDumpWriter, extract_message_text
from scout_agent.runtime.audit.experts import (
    SUBAGENT_MANIFEST,
    CompiledSubAgent,
    build_expert_subagents,
)
from scout_agent.runtime.audit.reporting import AuditProgressReporter
from scout_agent.runtime.audit.tools import read_sanitized_code_chunk

logger = logging.getLogger(__name__)
EXECUTION_PATH_CONSISTENCY_ACTOR_NAME = "execution_path_consistency"
EXECUTION_PATH_CONSISTENCY_SCOPE_LABEL = "repo"


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
    reporter: AuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None
    dump_writer: AuditDumpWriter | None = None


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


@dataclass(frozen=True, slots=True)
class _CompletedExecutionPathConsistencyAudit:
    response: FileAuditResponse


@dataclass(frozen=True, slots=True)
class _ParsedSupervisorResponse:
    response: FileAuditResponse
    structured_response_present: bool
    used_text_fallback: bool
    parse_failed: bool
    final_message_text: str | None


class _StructuredResponseParseError(ValueError):
    def __init__(self, final_message_text: str | None) -> None:
        self.final_message_text = final_message_text
        super().__init__("Structured audit response failed validation.")


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
        execution_path_consistency_future: Future[
            _CompletedExecutionPathConsistencyAudit
        ] | None = (
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
                        completed_execution_path_consistency = future.result()
                    except CancelledError:
                        execution_path_consistency_future = None
                        continue
                    except Exception as exc:
                        failures.append(
                            FileAuditFailure(
                                index=0,
                                relative_path=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
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
                        response=completed_execution_path_consistency.response,
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
    if runtime.dump_writer is not None:
        runtime.dump_writer.file_started(relative_path=task.relative_path)
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
) -> _CompletedExecutionPathConsistencyAudit:
    return _CompletedExecutionPathConsistencyAudit(
        response=_run_execution_path_consistency_audit(
            runtime=runtime,
            allowed_paths=allowed_paths,
        )
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
    current_file_facts = _load_current_file_facts(
        facts_root=runtime.facts_path,
        current_file=current_file,
    )
    backend = FileScopedAuditBackend(
        root_dir=runtime.project_root,
        virtual_mode=True,
        current_file=current_file,
    )
    system_prompt = build_parent_system_prompt(
        current_file=current_file,
        current_file_facts=current_file_facts,
        extra_prompt=runtime.extra_prompt,
    )
    model = build_chat_model(runtime.model_name, runtime.llm_mode)

    def read_file(
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> str:
        """Read a file from the local filesystem. Defaults: offset=0, limit=2000."""
        try:
            return backend.read(file_path, offset=offset, limit=limit)
        except Exception as exc:
            return f"Error: {exc}"

    agent = create_agent(
        model=model,
        system_prompt=_escape_prompt_text(f"{system_prompt}\n\n{BASE_AGENT_PROMPT}"),
        middleware=[
            SubAgentMiddleware(
                backend=backend,
                subagents=expert_subagents,  # type: ignore[arg-type]
            ),
            create_summarization_middleware(model, backend),
            AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
            PatchToolCallsMiddleware(),
        ],
        tools=[read_file],
        response_format=FileAuditResponse,
        name="scout-agent",
    ).with_config({"recursion_limit": runtime.recursion_limit})

    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names={spec.name for spec in SUBAGENT_MANIFEST},
        current_file=current_file,
        dump_writer=runtime.dump_writer,
    )
    messages = get_supervisor_few_shots() + [
        HumanMessage(
            content=build_parent_audit_prompt(
                current_file=current_file,
                extra_prompt=runtime.extra_prompt,
            )
        )
    ]

    if runtime.dump_writer is not None:
        runtime.dump_writer.record_supervisor_event(
            relative_path=current_file,
            event_type="started",
        )
    result = agent.invoke(
        {"messages": messages},
        config={
            "callbacks": [callback_handler],
            "recursion_limit": runtime.recursion_limit,
        },
    )

    try:
        parsed = _parse_supervisor_response(result=result, current_file=current_file)
    except _StructuredResponseParseError as exc:
        _record_supervisor_parse_failure(
            dump_writer=runtime.dump_writer,
            current_file=current_file,
            final_message_text=exc.final_message_text,
        )
        raise

    _record_supervisor_response(
        dump_writer=runtime.dump_writer,
        current_file=current_file,
        parsed=parsed,
    )
    return parsed.response


def _run_execution_path_consistency_audit(
    *,
    runtime: AuditContext,
    allowed_paths: list[str],
) -> FileAuditResponse:
    allowed_path_set = set(allowed_paths)
    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    backend = RepoScopedAuditBackend(
        root_dir=runtime.project_root,
        virtual_mode=True,
        allowed_paths=allowed_path_set,
    )
    system_prompt = build_execution_path_consistency_system_prompt(
        aggregate_facts_document=runtime.aggregate_facts_document,
        extra_prompt=runtime.extra_prompt,
    )

    def read_code_chunk(
        file: str,
        start_line: int = 1,
        max_lines: int = 100,
    ) -> str:
        """Read up to 100 lines of sanitized source code from an in-scope file. Defaults: start_line=1, max_lines=100."""
        try:
            return read_sanitized_code_chunk(
                runtime.project_root,
                file,
                allowed_paths=allowed_paths,
                start_line=start_line,
                max_lines=max_lines,
            )
        except (ValueError, FileNotFoundError) as exc:
            return f"Error: {exc}"

    def grep(
        pattern: str,
        path: str | None = None,
    ) -> str:
        """
        Search for a regex pattern across all in-scope Rust files.
        Optional 'path' argument restricts the search to a specific in-scope file or directory.
        """
        try:
            return _grep_allowed_paths(
                project_root=runtime.project_root,
                allowed_paths=allowed_paths,
                pattern=pattern,
                path=path,
            )
        except ValueError as exc:
            return f"Error: {exc}"

    def read_fact_entry(relative_path: str) -> str:
        """Read the aggregate FACTS.yml entry for one in-scope file."""
        normalized_path = Path(relative_path.strip()).as_posix()
        if not normalized_path or normalized_path == ".":
            return "Error: File path must be non-empty."
        if normalized_path not in allowed_path_set:
            return f"Error: File is outside FACTS scope: {normalized_path}"

        aggregate_entry = runtime.aggregate_facts_document.files.get(normalized_path)
        if aggregate_entry is None:
            return f"Error: Aggregate FACTS entry not found: {normalized_path}"

        payload = {
            "path": normalized_path,
            **aggregate_entry.model_dump(mode="python", exclude_none=True),
        }
        return yaml.safe_dump(
            payload,
            sort_keys=False,
            default_flow_style=False,
        ).strip()

    agent = create_agent(
        model=model,
        system_prompt=_escape_prompt_text(f"{system_prompt}\n\n{BASE_AGENT_PROMPT}"),
        middleware=[
            create_summarization_middleware(model, backend),
            AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
            PatchToolCallsMiddleware(),
        ],
        tools=[read_code_chunk, grep, read_fact_entry],
        response_format=FileAuditResponse,
        name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
    ).with_config({"recursion_limit": runtime.recursion_limit})

    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names=set(),
        current_file=EXECUTION_PATH_CONSISTENCY_SCOPE_LABEL,
        dump_writer=runtime.dump_writer,
        primary_actor_name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
        dump_scope="repo",
    )
    if runtime.dump_writer is not None:
        runtime.dump_writer.record_repo_event(
            actor_name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
            event_type="started",
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

    try:
        parsed = _parse_supervisor_response(
            result=result,
            current_file=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
        )
    except _StructuredResponseParseError as exc:
        _record_execution_path_consistency_parse_failure(
            dump_writer=runtime.dump_writer,
            final_message_text=exc.final_message_text,
        )
        raise

    _record_execution_path_consistency_response(
        dump_writer=runtime.dump_writer,
        parsed=parsed,
    )
    return parsed.response


def _record_supervisor_response(
    *,
    dump_writer: AuditDumpWriter | None,
    current_file: str,
    parsed: _ParsedSupervisorResponse,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_supervisor_event(
        relative_path=current_file,
        event_type="response_received",
        structured_response_present=parsed.structured_response_present,
        used_text_fallback=parsed.used_text_fallback,
        finding_count=len(parsed.response.findings),
        parse_failed=parsed.parse_failed,
        final_message_text=parsed.final_message_text,
        final_message_truncated=False if parsed.final_message_text else None,
    )
    dump_writer.record_supervisor_event(
        relative_path=current_file,
        event_type="completed",
    )


def _record_execution_path_consistency_response(
    *,
    dump_writer: AuditDumpWriter | None,
    parsed: _ParsedSupervisorResponse,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_repo_event(
        actor_name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
        event_type="response_received",
        structured_response_present=parsed.structured_response_present,
        used_text_fallback=parsed.used_text_fallback,
        finding_count=len(parsed.response.findings),
        parse_failed=parsed.parse_failed,
        final_message_text=parsed.final_message_text,
        final_message_truncated=False if parsed.final_message_text else None,
    )
    dump_writer.record_repo_event(
        actor_name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
        event_type="completed",
    )


def _record_supervisor_parse_failure(
    *,
    dump_writer: AuditDumpWriter | None,
    current_file: str,
    final_message_text: str | None,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_supervisor_event(
        relative_path=current_file,
        event_type="response_received",
        structured_response_present=True,
        used_text_fallback=False,
        finding_count=0,
        parse_failed=True,
        final_message_text=final_message_text,
        final_message_truncated=False if final_message_text else None,
    )


def _record_execution_path_consistency_parse_failure(
    *,
    dump_writer: AuditDumpWriter | None,
    final_message_text: str | None,
) -> None:
    if dump_writer is None:
        return
    dump_writer.record_repo_event(
        actor_name=EXECUTION_PATH_CONSISTENCY_ACTOR_NAME,
        event_type="response_received",
        structured_response_present=True,
        used_text_fallback=False,
        finding_count=0,
        parse_failed=True,
        final_message_text=final_message_text,
        final_message_truncated=False if final_message_text else None,
    )


def _parse_supervisor_response(
    *,
    result: dict[str, object],
    current_file: str,
) -> _ParsedSupervisorResponse:
    final_message_text = _extract_final_message_text(result)
    structured = result.get("structured_response")
    if structured is not None:
        try:
            response = (
                structured
                if isinstance(structured, FileAuditResponse)
                else FileAuditResponse.model_validate(structured)
            )
        except ValueError as exc:
            raise _StructuredResponseParseError(final_message_text) from exc
        return _ParsedSupervisorResponse(
            response=response,
            structured_response_present=True,
            used_text_fallback=False,
            parse_failed=False,
            final_message_text=final_message_text,
        )

    logger.warning(
        "Structured response missing for %s, falling back to text parse",
        current_file,
    )

    text_response = _parse_text_fallback_response(result)
    if text_response is not None:
        return _ParsedSupervisorResponse(
            response=text_response,
            structured_response_present=False,
            used_text_fallback=True,
            parse_failed=False,
            final_message_text=final_message_text,
        )

    logger.warning(
        "No parseable audit response for %s, returning empty findings",
        current_file,
    )
    return _ParsedSupervisorResponse(
        response=FileAuditResponse(findings=[]),
        structured_response_present=False,
        used_text_fallback=True,
        parse_failed=True,
        final_message_text=final_message_text,
    )


def _parse_text_fallback_response(
    result: dict[str, object],
) -> FileAuditResponse | None:
    messages = result.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    last_message = messages[-1]
    content = getattr(last_message, "content", last_message)
    if not isinstance(content, str) or not content.strip():
        return None

    try:
        return FileAuditResponse.model_validate_json(content)
    except (json.JSONDecodeError, ValueError):
        return None


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


def _extract_final_message_text(result: dict[str, object]) -> str | None:
    messages = result.get("messages")
    if isinstance(messages, list) and messages:
        extracted = extract_message_text(messages[-1])
        if extracted:
            return extracted

    extracted = extract_message_text(result.get("output"))
    if extracted:
        return extracted
    return None


def _escape_prompt_text(prompt_text: str) -> str:
    return prompt_text.replace("{", "{{").replace("}", "}}")


def _grep_allowed_paths(
    *,
    project_root: Path,
    allowed_paths: list[str],
    pattern: str,
    path: str | None = None,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc

    search_paths = allowed_paths
    if path is not None and path.strip():
        normalized_path = Path(path.strip()).as_posix()
        search_paths = [
            candidate
            for candidate in allowed_paths
            if candidate == normalized_path or candidate.startswith(f"{normalized_path}/")
        ]
        if not search_paths:
            raise ValueError(f"Path '{path}' is not in scope or does not exist.")

    results: list[str] = []
    max_results = 50
    for relative_path in search_paths:
        file_path = project_root / relative_path
        if not file_path.is_file():
            continue

        for line_number, line in enumerate(
            file_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if regex.search(line):
                results.append(f"{relative_path}:{line_number}: {line.strip()}")
                if len(results) >= max_results:
                    results.append("... (too many results, showing first 50)")
                    return "\n".join(results)

    if not results:
        return f"No matches found for pattern: {pattern}"
    return "\n".join(results)


def _load_current_file_facts(
    *,
    facts_root: Path,
    current_file: str,
) -> dict[str, FunctionSummary]:
    return load_facts_document(facts_file_path(facts_root, current_file)).functions
