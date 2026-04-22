from scout_agent.runtime.audit.engine.context import AuditContext
from pathlib import Path

from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage

from scout_agent.domain.audit import FileAuditResponse, Finding
from scout_agent.domain.facts import (
    FunctionSummary,
    facts_file_path,
    load_facts_document,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.engine.audit_callbacks import RuntimeProgressHandler
from scout_agent.runtime.audit.engine.experts import SUBAGENT_MANIFEST, CompiledSubAgent
from scout_agent.runtime.audit.engine.memory import get_sqlite_saver
from scout_agent.runtime.audit.engine.tools import build_readonly_tools
from scout_agent.runtime.audit.prompts.audit_prompts import (
    build_execution_path_consistency_audit_prompt,
    build_execution_path_consistency_system_prompt,
    build_parent_audit_prompt,
    build_parent_system_prompt,
)


def run_file_audit(
    *,
    runtime: AuditContext,
    current_file: str,
    expert_subagents: list[CompiledSubAgent],
    outer_thread_id: str,
    generation: int,
) -> FileAuditResponse:
    current_file_path = (runtime.project_root / current_file).resolve()
    current_file_facts = _load_current_file_facts(
        facts_root=runtime.facts_path,
        current_file=current_file,
    )

    system_prompt = build_parent_system_prompt(
        current_file=current_file_path.as_posix(),
        current_file_facts=current_file_facts,
        agent_grep_limit=runtime.agent_grep_limit,
        extra_prompt=runtime.extra_prompt,
    )
    model = build_chat_model(runtime.model_name, runtime.llm_mode)

    memory_dir = runtime.project_root / ".scout-ai"
    memory_dir.mkdir(parents=True, exist_ok=True)
    checkpointer = get_sqlite_saver(memory_dir / ".audit_memory.sqlite")

    agent = create_deep_agent(
        model=model,
        system_prompt=_escape_prompt_text(system_prompt),
        tools=build_readonly_tools(
            root_dir=runtime.project_root,
            scope_path=current_file_path,
            agent_read_limit=runtime.agent_read_limit,
            agent_grep_limit=runtime.agent_grep_limit,
            default_grep_path=current_file_path.as_posix(),
        ),
        subagents=expert_subagents,
        response_format=ProviderStrategy(FileAuditResponse, strict=True),
        name="scout-agent",
        checkpointer=checkpointer,
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
            "configurable": {
                "thread_id": _build_file_thread_id(
                    outer_thread_id=outer_thread_id,
                    current_file=current_file,
                    generation=generation,
                )
            },
        },
    )
    return _parse_structured_audit_response(
        result=result,
        actor_name=current_file,
    )


def run_execution_path_consistency_audit(
    *,
    runtime: AuditContext,
    allowed_paths: list[str],
    outer_thread_id: str,
    generation: int,
) -> FileAuditResponse:
    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    system_prompt = build_execution_path_consistency_system_prompt(
        aggregate_facts_document=runtime.aggregate_facts_document,
        agent_grep_limit=runtime.agent_grep_limit,
        extra_prompt=runtime.extra_prompt,
    )

    memory_dir = runtime.project_root / ".scout-ai"
    memory_dir.mkdir(parents=True, exist_ok=True)
    checkpointer = get_sqlite_saver(memory_dir / ".audit_memory.sqlite")

    agent = create_agent(
        model=model,
        system_prompt=_escape_prompt_text(system_prompt),
        tools=build_readonly_tools(
            root_dir=runtime.project_root,
            scope_path=runtime.project_root,
            agent_read_limit=0,
            agent_grep_limit=runtime.agent_grep_limit,
        ),
        response_format=ProviderStrategy(FileAuditResponse, strict=True),
        name="execution_path_consistency",
        checkpointer=checkpointer,
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
            "configurable": {
                "thread_id": _build_execution_path_consistency_thread_id(
                    outer_thread_id=outer_thread_id,
                    generation=generation,
                )
            },
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


def _build_file_thread_id(
    *,
    outer_thread_id: str,
    current_file: str,
    generation: int,
) -> str:
    return f"{outer_thread_id}:file:{current_file}:g{generation}"


def _build_execution_path_consistency_thread_id(
    *,
    outer_thread_id: str,
    generation: int,
) -> str:
    return f"{outer_thread_id}:execution_path_consistency:g{generation}"


def _escape_prompt_text(prompt_text: str) -> str:
    return prompt_text.replace("{", "{{").replace("}", "}}")


def _load_current_file_facts(
    *,
    facts_root: Path,
    current_file: str,
) -> dict[str, FunctionSummary]:
    return load_facts_document(facts_file_path(facts_root, current_file)).functions


def _relativize_findings(
    findings: list[Finding],
    project_root: Path,
) -> list[Finding]:
    """Relativize finding locations."""
    for finding in findings:
        finding.location = _relativize_location(finding.location, project_root)
    return findings


def _relativize_location(location: str, project_root: Path) -> str:
    if ":" not in location:
        return location

    parts = location.rsplit(":", 1)
    path_part = parts[0]
    line_part = parts[1]

    try:
        path = Path(path_part).expanduser()
        if path.is_absolute() and path.is_relative_to(project_root):
            rel_path = path.relative_to(project_root).as_posix()
            return f"{rel_path}:{line_part}"
    except (ValueError, RuntimeError):
        pass

    return location
