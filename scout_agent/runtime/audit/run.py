from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import (
    FactsDocument,
    build_facts_index,
    list_fact_paths,
    load_facts_document,
)
from scout_agent.runtime.source.discovery import (
    compute_scope_fingerprint,
    discover_rust_files,
)


@dataclass(frozen=True, slots=True)
class InitializedAudit:
    facts_document: FactsDocument
    initial_state: AuditState
    current_scope_fingerprint: str


def initialize_audit(
    *,
    project_root: Path,
    facts_path: Path,
    scout_files: list[str] | None = None,
) -> InitializedAudit:
    resolved_project_root = project_root.resolve()
    resolved_facts_path = facts_path.resolve()

    facts_document = load_facts_document(resolved_facts_path)
    _validate_facts_project_root(facts_document, resolved_project_root)

    discovered_files = discover_rust_files(
        resolved_project_root,
        configured_paths=scout_files,
    )
    if not discovered_files:
        raise ValueError(
            "No in-scope production Rust source files were discovered under "
            f"{resolved_project_root}"
        )

    current_scope_fingerprint = compute_scope_fingerprint(discovered_files)
    expected_scope_fingerprint = facts_document.scope_fingerprint

    if current_scope_fingerprint != expected_scope_fingerprint:
        raise ValueError(
            "FACTS.yaml is stale. "
            f"Expected scope fingerprint {expected_scope_fingerprint}, "
            f"found {current_scope_fingerprint}. "
            "Rerun 'scout-agent extract-facts <project_root>'."
        )

    facts_index = build_facts_index(facts_document)
    files_to_review = list_fact_paths(facts_document)

    initial_state: AuditState = {
        "project_root": str(resolved_project_root),
        "facts_path": str(resolved_facts_path),
        "facts_index": facts_index,
        "files_to_review": files_to_review,
        "current_file": files_to_review[0] if files_to_review else None,
        "last_supervisor_decision": None,
        "pending_delegations": [],
        "completed_delegation_keys": [],
        "needs_info_notes": [],
        "finding_keys": [],
        "files_reviewed": [],
        "verified_findings": [],
        "expert_batch_items": [],
        "completed_expert_batch_items": [],
        "last_announced_file": None,
        "supervisor_pass_counts": {},
    }

    return InitializedAudit(
        facts_document=facts_document,
        initial_state=initial_state,
        current_scope_fingerprint=current_scope_fingerprint,
    )


def _validate_facts_project_root(
    facts_document: FactsDocument,
    project_root: Path,
) -> None:
    recorded_project_root = Path(facts_document.project_root).expanduser().resolve()
    if recorded_project_root != project_root:
        raise ValueError(
            "FACTS.yaml project root does not match the requested audit root. "
            f"FACTS recorded {recorded_project_root}, "
            f"but audit was requested for {project_root}."
        )
