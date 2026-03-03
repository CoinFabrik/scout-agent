from __future__ import annotations
from scout_agent.app.console_reporting import PlainAuditProgressReporter

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from scout_agent.domain.audit import (
    AuditState,
    Delegation,
    ExpertBatchItem,
    SupervisorDecision,
)
from scout_agent.domain.facts import FactsDocument, FileFacts
from scout_agent.runtime.audit.expert_context import build_expert_execution_context
from scout_agent.runtime.audit.experts import run_expert_analysis
from scout_agent.runtime.audit.reducer import (
    make_delegation_key,
    reduce_expert_batch,
)
from scout_agent.runtime.audit.report_writer import write_report
from scout_agent.runtime.audit.supervisor import (
    run_supervisor_decision,
    validate_supervisor_decision_contract,
)


@dataclass(frozen=True, slots=True)
class AuditContext:
    project_root: Path
    report_path: Path
    facts_document: FactsDocument
    facts_index: dict[str, FileFacts]
    model_name: str
    llm_mode: str
    reporter: PlainAuditProgressReporter


class ExpertTaskInput(TypedDict):
    delegation: Delegation


def build_audit_graph(runtime: AuditContext):
    graph = StateGraph(AuditState)
    reporter = runtime.reporter

    def supervisor_node(state: AuditState) -> dict[str, object]:
        current_file = state["current_file"]
        if current_file is None:
            raise ValueError("Supervisor requires a non-empty current_file.")

        last_announced_file = state["last_announced_file"]
        supervisor_pass_counts = dict(state["supervisor_pass_counts"])

        if current_file != last_announced_file:
            reporter.file_started(
                index=len(state["files_reviewed"]) + 1,
                total=len(runtime.facts_document.files),
                current_file=current_file,
            )
            last_announced_file = current_file

        supervisor_pass_counts[current_file] = (
            supervisor_pass_counts.get(current_file, 0) + 1
        )
        reporter.supervisor_pass(
            current_file=current_file,
            pass_index=supervisor_pass_counts[current_file],
            completed_checks=_count_completed_checks_for_file(state, current_file),
            verified_findings=_count_verified_findings_for_file(state, current_file),
            needs_info_notes=_count_needs_info_notes_for_file(state, current_file),
        )

        decision = run_supervisor_decision(
            project_root=runtime.project_root,
            state=state,
            model_name=runtime.model_name,
            llm_mode=runtime.llm_mode,
        )
        validate_supervisor_decision_contract(
            decision=decision,
            current_file=current_file,
        )
        completed_delegation_keys = set(state["completed_delegation_keys"])
        filtered_delegations = [
            delegation
            for delegation in decision.delegations
            if make_delegation_key(delegation) not in completed_delegation_keys
        ]
        dropped_delegations = len(decision.delegations) - len(filtered_delegations)
        if dropped_delegations > 0:
            reporter.duplicate_delegations_filtered(
                current_file=current_file,
                requested=len(decision.delegations),
                dropped=dropped_delegations,
                remaining=len(filtered_delegations),
            )

        if not filtered_delegations and decision.delegations:
            filtered_decision = SupervisorDecision(
                file_fully_analyzed=True,
                delegations=[],
            )
        else:
            filtered_decision = SupervisorDecision(
                file_fully_analyzed=decision.file_fully_analyzed,
                delegations=filtered_delegations,
            )

        return {
            "last_supervisor_decision": filtered_decision,
            "pending_delegations": filtered_delegations,
            "last_announced_file": last_announced_file,
            "supervisor_pass_counts": supervisor_pass_counts,
        }

    def router_node(state: AuditState) -> Command:
        decision = state["last_supervisor_decision"]
        if decision is None:
            raise ValueError("Router requires a last_supervisor_decision.")

        pending_delegations = state["pending_delegations"]
        if pending_delegations:
            # reporter.delegation_batch(
            #     current_file=state["current_file"] or "<unknown>",
            #     delegation_count=len(pending_delegations),
            # )
            print(
                "Delegating the following experts for: ",
                file=runtime.reporter._stdout,
                flush=True,
            )
            for delegation in pending_delegations:
                print(
                    f"- {delegation.expert_type}",
                    file=runtime.reporter._stdout,
                    flush=True,
                )
            return Command(
                goto=[
                    Send("expert", {"delegation": delegation})
                    for delegation in pending_delegations
                ]
            )

        if not decision.file_fully_analyzed:
            current_file = state["current_file"] or "<unknown>"
            raise ValueError(
                "Router received an invalid SupervisorDecision for "
                f"{current_file}: file_fully_analyzed=false with no active delegations."
            )

        return Command(goto="advance_file")

    def expert_node(task: ExpertTaskInput) -> dict[str, object]:
        delegation = task["delegation"]
        target_file_facts = runtime.facts_index[delegation.target_file]
        context = build_expert_execution_context(
            project_root=runtime.project_root,
            facts_index=runtime.facts_index,
            delegation=delegation,
        )
        result = run_expert_analysis(
            delegation=delegation,
            target_file_facts=target_file_facts,
            code_snapshot=context.code_snapshot,
            model_name=runtime.model_name,
            llm_mode=runtime.llm_mode,
            search_results=context.search_results,
            project_root=runtime.project_root,
            allowed_paths=context.allowed_paths,
        )
        return {
            "expert_batch_items": [
                ExpertBatchItem(
                    delegation=delegation,
                    result=result,
                )
            ]
        }

    def reducer_node(state: AuditState) -> dict[str, object]:
        pending_delegations = state["pending_delegations"]
        if not pending_delegations:
            raise ValueError("Reducer requires pending_delegations to be non-empty.")

        ordered_results = _ordered_current_batch_results(
            pending_delegations=pending_delegations,
            expert_batch_items=state["expert_batch_items"],
        )
        reduced = reduce_expert_batch(
            delegations=pending_delegations,
            results=ordered_results,
            existing_finding_keys=state["finding_keys"],
            existing_completed_delegation_keys=state["completed_delegation_keys"],
            existing_needs_info_notes=state["needs_info_notes"],
        )
        for offset, finding in enumerate(reduced.new_findings, start=1):
            reporter.finding_verified(
                total_verified_findings=len(state["verified_findings"]) + offset,
                finding=finding,
            )
        return {
            "completed_delegation_keys": [
                *state["completed_delegation_keys"],
                *reduced.new_completed_delegation_keys,
            ],
            "finding_keys": [
                *state["finding_keys"],
                *reduced.new_finding_keys,
            ],
            "needs_info_notes": [
                *state["needs_info_notes"],
                *reduced.new_needs_info_notes,
            ],
            "verified_findings": [*state["verified_findings"], *reduced.new_findings],
            "pending_delegations": [],
            "last_supervisor_decision": None,
            "completed_expert_batch_items": list(state["expert_batch_items"]),
            "expert_batch_items": [],
        }

    def advance_file_node(state: AuditState) -> dict[str, object]:
        current_file = state["current_file"]
        if current_file is None:
            raise ValueError("advance_file requires a non-empty current_file.")
        if not state["files_to_review"]:
            raise ValueError("advance_file requires files_to_review to be non-empty.")
        if state["files_to_review"][0] != current_file:
            raise ValueError(
                "advance_file requires current_file to match the head of files_to_review."
            )

        remaining_files = state["files_to_review"][1:]
        next_file = remaining_files[0] if remaining_files else None
        reporter.file_completed(
            reviewed=len(state["files_reviewed"]) + 1,
            total=len(runtime.facts_document.files),
            current_file=current_file,
        )

        return {
            "files_to_review": remaining_files,
            "files_reviewed": [current_file],
            "current_file": next_file,
            "last_supervisor_decision": None,
            "pending_delegations": [],
        }

    def report_node(state: AuditState) -> dict[str, object]:
        write_report(
            report_path=runtime.report_path,
            facts_document=runtime.facts_document,
            state=state,
        )
        return {}

    graph.add_node("supervisor", supervisor_node)
    graph.add_node(
        "router",
        router_node,
        destinations=("expert", "advance_file"),
    )
    graph.add_node("expert", expert_node, input_schema=ExpertTaskInput)
    graph.add_node("reducer", reducer_node)
    graph.add_node("advance_file", advance_file_node)
    graph.add_node("report", report_node)

    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "router")
    graph.add_edge("expert", "reducer")
    graph.add_edge("reducer", "supervisor")
    graph.add_conditional_edges(
        "advance_file",
        _after_advance_file,
        {
            "supervisor": "supervisor",
            "report": "report",
        },
    )
    graph.add_edge("report", END)

    return graph.compile(name="audit_graph")


def run_audit(
    *,
    runtime: AuditContext,
    initial_state: AuditState,
) -> AuditState:
    reporter = runtime.reporter
    reporter.started(
        project_root=runtime.project_root,
        total_files=len(runtime.facts_document.files),
        model_name=runtime.model_name,
        llm_mode=runtime.llm_mode,
    )
    graph = build_audit_graph(runtime)
    return graph.invoke(initial_state)


def _after_advance_file(state: AuditState) -> str:
    return "report" if state["current_file"] is None else "supervisor"


def _ordered_current_batch_results(
    *,
    pending_delegations: list[Delegation],
    expert_batch_items: list[ExpertBatchItem],
) -> list:
    ordered_results = []
    consumed_indexes: set[int] = set()

    for delegation in pending_delegations:
        delegation_key = make_delegation_key(delegation)
        match_index: int | None = None

        # Scan in reverse so that if a delegation was retried and appears multiple
        # times in expert_batch_items, we use the most recent result.
        # consumed_indexes prevents the same item being claimed by two different
        # delegations in the same batch.
        for index in range(len(expert_batch_items) - 1, -1, -1):
            if index in consumed_indexes:
                continue
            if (
                make_delegation_key(expert_batch_items[index].delegation)
                == delegation_key
            ):
                match_index = index
                break

        if match_index is None:
            raise ValueError(
                "Reducer could not find an expert result for delegation "
                f"{delegation_key}."
            )

        consumed_indexes.add(match_index)
        ordered_results.append(expert_batch_items[match_index].result)

    return ordered_results


def _count_completed_checks_for_file(state: AuditState, current_file: str) -> int:
    return sum(
        1
        for item in state["completed_expert_batch_items"]
        if item.delegation.target_file == current_file
    )


def _count_verified_findings_for_file(state: AuditState, current_file: str) -> int:
    file_prefix = f"{current_file}:"
    return sum(
        1
        for finding in state["verified_findings"]
        if finding.location.startswith(file_prefix)
    )


def _count_needs_info_notes_for_file(state: AuditState, current_file: str) -> int:
    return sum(
        1 for note in state["needs_info_notes"] if note.target_file == current_file
    )
