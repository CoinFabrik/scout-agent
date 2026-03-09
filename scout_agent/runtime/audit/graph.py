from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from deepagents import create_deep_agent

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
)
from scout_agent.runtime.audit.dump import AuditDumpWriter, extract_message_text
from scout_agent.runtime.audit.experts import (
    SUBAGENT_MANIFEST,
    CompiledSubAgent,
    build_expert_subagents,
)
from scout_agent.runtime.audit.prompt_utils import append_extra_prompt
from scout_agent.runtime.audit.reporting import AuditProgressReporter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    report_path: Path
    facts_document: FactsDocument
    model_name: str
    llm_mode: str
    reporter: AuditProgressReporter
    initial_state: AuditState
    extra_prompt: str | None = None
    dump_writer: AuditDumpWriter | None = None


def run_audit(
    *,
    runtime: AuditContext,
) -> AuditState:
    reporter = runtime.reporter
    state = runtime.initial_state

    file_fact_index = build_file_fact_index(runtime.facts_document)
    total_files = len(state["files_to_review"])
    allowed_paths = list(state["files_to_review"])
    expert_subagents = build_expert_subagents(
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
        project_root=runtime.project_root,
        allowed_paths=allowed_paths,
        extra_prompt=runtime.extra_prompt,
    )

    reporter.started(
        project_root=runtime.project_root,
        total_files=total_files,
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )

    while state["files_to_review"]:
        current_file = state["files_to_review"][0]
        if runtime.dump_writer is not None:
            runtime.dump_writer.file_started(relative_path=current_file)
        reporter.file_started(
            index=len(state["files_reviewed"]) + 1,
            total=total_files,
            current_file=current_file,
        )

        try:
            response = _run_file_audit(
                runtime=runtime,
                current_file=current_file,
                file_fact_index=file_fact_index,
                expert_subagents=expert_subagents,
            )
        except Exception:
            if runtime.dump_writer is not None:
                runtime.dump_writer.file_failed(relative_path=current_file)
            raise

        for finding in response.findings:
            finding_key = _make_finding_key(finding)
            if finding_key in state["finding_keys"]:
                continue
            state["finding_keys"].append(finding_key)
            state["verified_findings"].append(finding)
            if runtime.dump_writer is not None:
                runtime.dump_writer.finding_verified(relative_path=current_file)
            reporter.finding_verified(
                total_verified_findings=len(state["verified_findings"]),
                finding=finding,
            )

        state["files_reviewed"].append(current_file)
        state["files_to_review"] = state["files_to_review"][1:]
        if runtime.dump_writer is not None:
            runtime.dump_writer.file_completed(relative_path=current_file)
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
    agent = create_deep_agent(
        name="scout-agent",
        model=build_chat_model(runtime.model_name, runtime.llm_mode),
        system_prompt=system_prompt,
        backend=backend,
        subagents=expert_subagents,
        response_format=FileAuditResponse,
    )
    prompt = build_parent_audit_prompt(
        current_file=current_file,
        extra_prompt=runtime.extra_prompt,
    )
    callback_handler = RuntimeProgressHandler(
        reporter=runtime.reporter,
        expert_names={spec.name for spec in SUBAGENT_MANIFEST},
        current_file=current_file,
        dump_writer=runtime.dump_writer,
    )
    if runtime.dump_writer is not None:
        runtime.dump_writer.supervisor_started(relative_path=current_file)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={"callbacks": [callback_handler]},
    )
    final_message_text = _extract_final_message_text(result)

    structured = result.get("structured_response")
    if structured is not None:
        try:
            response = (
                structured
                if isinstance(structured, FileAuditResponse)
                else FileAuditResponse.model_validate(structured)
            )
        except ValueError:
            if runtime.dump_writer is not None:
                runtime.dump_writer.supervisor_response_received(
                    relative_path=current_file,
                    structured_response_present=True,
                    used_text_fallback=False,
                    finding_count=0,
                    parse_failed=True,
                    final_message_text=final_message_text,
                    final_message_truncated=False if final_message_text else None,
                )
            raise
        if runtime.dump_writer is not None:
            runtime.dump_writer.supervisor_response_received(
                relative_path=current_file,
                structured_response_present=True,
                used_text_fallback=False,
                finding_count=len(response.findings),
                parse_failed=False,
                final_message_text=final_message_text,
                final_message_truncated=False if final_message_text else None,
            )
            runtime.dump_writer.supervisor_completed(relative_path=current_file)
        return response

    logger.warning(
        "Structured response missing for %s, falling back to text parse",
        current_file,
    )

    messages = result.get("messages", [])
    if not messages:
        if runtime.dump_writer is not None:
            runtime.dump_writer.supervisor_response_received(
                relative_path=current_file,
                structured_response_present=False,
                used_text_fallback=True,
                finding_count=0,
                parse_failed=True,
                final_message_text=final_message_text,
                final_message_truncated=False if final_message_text else None,
            )
            runtime.dump_writer.supervisor_completed(relative_path=current_file)
        logger.warning(
            "No parseable audit response for %s, returning empty findings",
            current_file,
        )
        return FileAuditResponse(findings=[])

    last_message = messages[-1]
    content = getattr(last_message, "content", last_message)
    if isinstance(content, str) and content.strip():
        try:
            response = FileAuditResponse.model_validate_json(content)
            if runtime.dump_writer is not None:
                runtime.dump_writer.supervisor_response_received(
                    relative_path=current_file,
                    structured_response_present=False,
                    used_text_fallback=True,
                    finding_count=len(response.findings),
                    parse_failed=False,
                    final_message_text=final_message_text,
                    final_message_truncated=False if final_message_text else None,
                )
                runtime.dump_writer.supervisor_completed(relative_path=current_file)
            return response
        except (json.JSONDecodeError, ValueError):
            pass

    if runtime.dump_writer is not None:
        runtime.dump_writer.supervisor_response_received(
            relative_path=current_file,
            structured_response_present=False,
            used_text_fallback=True,
            finding_count=0,
            parse_failed=True,
            final_message_text=final_message_text,
            final_message_truncated=False if final_message_text else None,
        )
        runtime.dump_writer.supervisor_completed(relative_path=current_file)
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


def _extract_final_message_text(result: dict[str, object]) -> str | None:
    messages = result.get("messages")
    if isinstance(messages, list) and messages:
        extracted = extract_message_text(messages[-1])
        if extracted:
            return extracted

    output_value = result.get("output")
    extracted = extract_message_text(output_value)
    if extracted:
        return extracted
    return None
