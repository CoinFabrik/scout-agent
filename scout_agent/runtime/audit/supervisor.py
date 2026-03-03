from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from scout_agent.domain.audit import (
    AuditState,
    ExpertBatchItem,
    Finding,
    NeedsInfoNote,
    SupervisorDecision,
)
from scout_agent.domain.facts import FileFacts, FunctionFacts
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.audit.tools import (
    SUPERVISOR_READ_MAX_LINES,
    supervisor_read_code,
)

SUPERVISOR_SYSTEM_PROMPT = """You are the Supervisor agent for a Soroban smart contract audit.

Your role:
- understand the current file
- decide whether the file is fully analyzed
- delegate narrow follow-up investigations to expert subagents

You are NOT a direct vulnerability adjudicator.
You must not produce findings yourself.
You only decide whether to delegate and to which expert.

Available expert types:
- execution_path_consistency
- collection_validation
- time_state
- sentinel_logic

Delegation rules:
- delegate only when there is concrete support in the file facts or file code
- each delegation must be narrow and specific
- prefer multiple narrow delegations over one broad delegation
- use the current file as target_file unless cross-file context clearly requires another in-scope file
- context_snippet must be short and specific
- reasoning must explain why the expert is needed

Completion rules:
- valid response shape 1: file_fully_analyzed=false and delegations has at least one item
- valid response shape 2: file_fully_analyzed=true and delegations is empty
- file_fully_analyzed=false with zero delegations is invalid
- file_fully_analyzed=true with any delegations is invalid
- set file_fully_analyzed=true only when no additional expert work is needed for this file
- do not re-delegate a concern already covered in "Previously completed expert checks for this file"
- if the completed checks and verified findings already cover the remaining concrete concerns, return file_fully_analyzed=true with delegations=[]
- do not invent functions, files, or code
- rely only on the provided file facts, source snapshot, notes, completed checks, and verified findings
"""


def run_supervisor_decision(
    *,
    project_root: Path,
    state: AuditState,
    model_name: str,
    llm_mode: str,
) -> SupervisorDecision:
    current_file = state.get("current_file")
    if not current_file:
        raise ValueError("Audit state has no current_file for supervisor evaluation.")

    facts_index = state["facts_index"]
    if current_file not in facts_index:
        raise ValueError(f"Current file is missing from facts_index: {current_file}")

    current_file_facts = facts_index[current_file]
    file_notes = _notes_for_file(state.get("needs_info_notes", []), current_file)
    completed_checks = _completed_checks_for_file(
        state.get("completed_expert_batch_items", []),
        current_file,
    )
    verified_findings = _findings_for_file(
        state.get("verified_findings", []),
        current_file,
    )
    allowed_paths = frozenset(facts_index.keys())
    code_snapshot = _read_full_file_snapshot(
        project_root=project_root.resolve(),
        relative_path=current_file,
        allowed_paths=allowed_paths,
    )

    model = build_chat_model(model_name, llm_mode)
    structured_model = model.with_structured_output(
        SupervisorDecision,
        method="json_schema",
    )

    response = structured_model.invoke(
        build_supervisor_messages(
            current_file=current_file,
            current_file_facts=current_file_facts,
            code_snapshot=code_snapshot,
            notes=file_notes,
            completed_checks=completed_checks,
            verified_findings=verified_findings,
        )
    )

    if isinstance(response, SupervisorDecision):
        decision = response
    else:
        decision = SupervisorDecision.model_validate(response)

    validate_supervisor_decision_contract(
        decision=decision,
        current_file=current_file,
    )
    return decision


def validate_supervisor_decision_contract(
    *,
    decision: SupervisorDecision,
    current_file: str,
) -> None:
    has_delegations = bool(decision.delegations)
    if decision.file_fully_analyzed and has_delegations:
        raise ValueError(
            "Invalid SupervisorDecision for "
            f"{current_file}: file_fully_analyzed=true with "
            f"{len(decision.delegations)} delegation(s)."
        )

    if not decision.file_fully_analyzed and not has_delegations:
        raise ValueError(
            "Invalid SupervisorDecision for "
            f"{current_file}: file_fully_analyzed=false with 0 delegations."
        )


def build_supervisor_messages(
    *,
    current_file: str,
    current_file_facts: FileFacts,
    code_snapshot: str,
    notes: list[NeedsInfoNote],
    completed_checks: list[ExpertBatchItem],
    verified_findings: list[Finding],
) -> list[BaseMessage]:
    user_prompt = (
        f"Current file: {current_file}\n\n"
        "Function facts for this file:\n"
        f"{_format_file_facts(current_file_facts)}\n\n"
        "Previously completed expert checks for this file:\n"
        f"{_format_completed_checks(completed_checks)}\n\n"
        "Already verified findings for this file:\n"
        f"{_format_verified_findings(verified_findings)}\n\n"
        "Outstanding needs-info notes for this file:\n"
        f"{_format_notes(notes)}\n\n"
        "Code snapshot:\n"
        f"{code_snapshot}\n"
    )

    return [
        SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]


def _read_full_file_snapshot(
    *,
    project_root: Path,
    relative_path: str,
    allowed_paths: frozenset[str],
) -> str:
    chunks: list[str] = []
    start_line = 1

    while True:
        chunk = supervisor_read_code(
            project_root,
            relative_path,
            allowed_paths=allowed_paths,
            start_line=start_line,
            max_lines=SUPERVISOR_READ_MAX_LINES,
        )

        if not chunk:
            break

        chunks.append(chunk)
        line_count = len(chunk.splitlines())
        if line_count < SUPERVISOR_READ_MAX_LINES:
            break

        start_line += line_count

    return "\n".join(chunks)


def _format_file_facts(file_facts: FileFacts) -> str:
    if not file_facts.functions:
        return "- No functions discovered in this file."

    return "\n".join(
        _format_function_facts(function) for function in file_facts.functions
    )


def _format_function_facts(function: FunctionFacts) -> str:
    impl_part = f", impl_target={function.impl_target}" if function.impl_target else ""
    return (
        f"- function_id={function.function_id}, "
        f"name={function.name}, "
        f"kind={function.kind}, "
        f"visibility={function.visibility}, "
        f"lines={function.line_start}-{function.line_end}"
        f"{impl_part}, "
        f"signature={function.signature}, "
        f"authorization={function.facts.authorization.status}, "
        f"vector_parameters={function.facts.vector_parameters.status}, "
        f"time_dependent_state={function.facts.time_dependent_state.status}, "
        f"sentinel_values={function.facts.sentinel_values.status}"
    )


def _format_notes(notes: list[NeedsInfoNote]) -> str:
    if not notes:
        return "- None."

    return "\n".join(
        f"- expert_type={note.expert_type.value}, note={note.note}" for note in notes
    )


def _format_completed_checks(items: list[ExpertBatchItem]) -> str:
    if not items:
        return "- None."

    lines = []
    for item in items:
        finding_summary = "finding=none"
        if item.result.finding is not None:
            finding_summary = (
                "finding="
                f"{item.result.finding.pattern} at {item.result.finding.location}"
            )

        lines.append(
            f"- expert_type={item.delegation.expert_type.value}, "
            f"status={item.result.status}, "
            f"context_snippet={_inline_text(item.delegation.context_snippet)}, "
            f"{finding_summary}"
        )

    return "\n".join(lines)


def _format_verified_findings(findings: list[Finding]) -> str:
    if not findings:
        return "- None."

    return "\n".join(
        (
            f"- severity={finding.severity}, "
            f"pattern={finding.pattern}, "
            f"location={finding.location}"
        )
        for finding in findings
    )


def _notes_for_file(
    notes: list[NeedsInfoNote],
    current_file: str,
) -> list[NeedsInfoNote]:
    return [note for note in notes if note.target_file == current_file]


def _completed_checks_for_file(
    expert_batch_items: list[ExpertBatchItem],
    current_file: str,
) -> list[ExpertBatchItem]:
    return [
        item
        for item in expert_batch_items
        if item.delegation.target_file == current_file
    ]


def _findings_for_file(
    findings: list[Finding],
    current_file: str,
) -> list[Finding]:
    file_prefix = f"{current_file}:"
    return [finding for finding in findings if finding.location.startswith(file_prefix)]


def _inline_text(value: str) -> str:
    return " ".join(value.strip().split())
