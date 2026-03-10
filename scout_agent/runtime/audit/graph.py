from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from langchain.agents import create_agent
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import HumanMessage

from deepagents.graph import BASE_AGENT_PROMPT
from deepagents.middleware import (
    SubAgentMiddleware,
)
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import create_summarization_middleware
from scout_agent.domain.audit import AuditState, FileAuditResponse, Finding
from scout_agent.domain.facts import (
    FactsDocument,
    FunctionSummary,
    build_file_fact_index,
)
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.audit_backend import FileScopedAuditBackend
from scout_agent.runtime.audit.audit_callbacks import RuntimeProgressHandler
from scout_agent.runtime.audit.audit_prompts import (
    PARENT_SYSTEM_PROMPT,
    build_parent_audit_prompt,
    build_parent_system_prompt,
    get_supervisor_few_shots,
)
from scout_agent.runtime.audit.experts import (
    SUBAGENT_MANIFEST,
    CompiledSubAgent,
    build_expert_subagents,
)
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt

from scout_agent.runtime.audit.reporting import PlainAuditProgressReporter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    report_path: Path
    facts_document: FactsDocument
    model_name: str
    llm_mode: str
    reporter: PlainAuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None


def run_audit(
    *,
    runtime: AuditContext,
) -> AuditState:
    reporter = runtime.reporter
    state = runtime.initial_state

    file_fact_index = build_file_fact_index(runtime.facts_document)
    total_files = len(state["files_to_review"])
    allowed_paths = list(state["files_to_review"])
    
    reporter.started(
        project_root=runtime.project_root,
        total_files=total_files,
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )

    while state["files_to_review"]:
        current_file = state["files_to_review"][0]
        
        expert_subagents = build_expert_subagents(
            model_name=runtime.model_name,
            llm_mode=runtime.llm_mode,
            project_root=runtime.project_root,
            allowed_paths=allowed_paths,
            extra_prompt=runtime.extra_prompt,
        )

        reporter.file_started(
            index=len(state["files_reviewed"]) + 1,
            total=total_files,
            current_file=current_file,
        )

        response = _run_file_audit(
            runtime=runtime,
            current_file=current_file,
            file_fact_index=file_fact_index,
            expert_subagents=expert_subagents,
        )

        for finding in response.findings:
            finding_key = _make_finding_key(finding)
            if finding_key in state["finding_keys"]:
                continue
            state["finding_keys"].append(finding_key)
            state["verified_findings"].append(finding)
            reporter.finding_verified(
                total_verified_findings=len(state["verified_findings"]),
                finding=finding,
            )

        state["files_reviewed"].append(current_file)
        state["files_to_review"] = state["files_to_review"][1:]
        reporter.file_completed(
            reviewed=len(state["files_reviewed"]),
            total=total_files,
            current_file=current_file,
        )

    return state


def _run_file_audit(
    *,
    runtime: AuditContext,
    current_file: str,
    file_fact_index: dict[str, dict[str, FunctionSummary]],
    expert_subagents: list[CompiledSubAgent],
) -> FileAuditResponse:
    backend = FileScopedAuditBackend(
        root_dir=runtime.project_root,
        virtual_mode=True,
        current_file=current_file,
    )
    system_prompt = build_parent_system_prompt(
        current_file=current_file,
        current_file_facts=file_fact_index.get(current_file, {}),
        all_facts=runtime.facts_document.functions,
        extra_prompt=runtime.extra_prompt,
    )

    model = build_chat_model(runtime.model_name, runtime.llm_mode)
    
    # Strictly define the supervisor's toolset.
    # We do NOT use FilesystemMiddleware here to physically strip ls, grep, glob, etc.
    # Instead, we provide only the read_file tool.
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

    subagent_middleware = SubAgentMiddleware(
        backend=backend,
        subagents=expert_subagents,  # type: ignore
    )
    
    middleware_stack = [
        subagent_middleware,
        create_summarization_middleware(model, backend),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        PatchToolCallsMiddleware(),
    ]
    
    # Prepend system_prompt to BASE_AGENT_PROMPT as create_deep_agent does
    final_system_prompt = system_prompt + "\n\n" + BASE_AGENT_PROMPT
    # Escape braces for potential LangChain prompt template interpolation
    escaped_system_prompt = final_system_prompt.replace("{", "{{").replace("}", "}}")
    
    agent = create_agent(
        model=model,
        system_prompt=escaped_system_prompt,
        middleware=middleware_stack,
        tools=[read_file],
        response_format=FileAuditResponse,
        name="scout-agent",
    ).with_config({"recursion_limit": 1000})

    prompt = build_parent_audit_prompt(
        current_file=current_file,
        extra_prompt=runtime.extra_prompt,
    )
    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names={spec.name for spec in SUBAGENT_MANIFEST},
        current_file=current_file,
    )
    few_shots = get_supervisor_few_shots()
    messages = few_shots + [HumanMessage(content=prompt)]
    
    result = agent.invoke(
        {"messages": messages},
        config={"callbacks": [callback_handler]},
    )

    structured = result.get("structured_response")
    if structured is not None:
        if isinstance(structured, FileAuditResponse):
            return structured
        return FileAuditResponse.model_validate(structured)

    logger.warning(
        "Structured response missing for %s, falling back to text parse",
        current_file,
    )

    messages = result.get("messages", [])
    if not messages:
        logger.warning(
            "No parseable audit response for %s, returning empty findings",
            current_file,
        )
        return FileAuditResponse(findings=[])

    last_message = messages[-1]
    content = getattr(last_message, "content", last_message)
    if isinstance(content, str) and content.strip():
        try:
            return FileAuditResponse.model_validate_json(content)
        except (json.JSONDecodeError, ValueError):
            pass

    logger.warning(
        "No parseable audit response for %s, returning empty findings",
        current_file,
    )
    return FileAuditResponse(findings=[])


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
