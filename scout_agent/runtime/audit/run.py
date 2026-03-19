from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scout_agent.domain.audit import AuditState
from scout_agent.domain.facts import (
    AggregateFactsDocument,
    AggregateFileFacts,
    FactsDocument,
    aggregate_facts_file_path,
    facts_file_path,
    load_aggregate_facts_document,
    load_facts_document,
)
from scout_agent.runtime.source.scope import discover_in_scope_files_or_raise


@dataclass(frozen=True, slots=True)
class InitializedAudit:
    aggregate_facts_document: AggregateFactsDocument
    initial_state: AuditState


def initialize_audit(
    *,
    project_root: Path,
    facts_path: Path,
    scout_files: list[str] | None = None,
) -> InitializedAudit:
    resolved_project_root = project_root.resolve()
    resolved_facts_path = facts_path.resolve()

    discovered_files = discover_in_scope_files_or_raise(
        project_root=resolved_project_root,
        configured_paths=scout_files,
    )

    aggregate_document = load_aggregate_facts_document(
        aggregate_facts_file_path(resolved_facts_path)
    )
    _validate_aggregate_facts_document(
        aggregate_facts_document=aggregate_document,
        expected_project_root=resolved_project_root,
    )

    discovered_paths: set[str] = set()
    for discovered_file in discovered_files:
        discovered_paths.add(discovered_file.relative_path)
        current_facts_path = facts_file_path(
            resolved_facts_path,
            discovered_file.relative_path,
        )
        current_facts_document = load_facts_document(current_facts_path)
        _validate_facts_document(
            facts_document=current_facts_document,
            expected_project_root=resolved_project_root,
            expected_relative_path=discovered_file.relative_path,
            expected_content_sha256=discovered_file.content_sha256,
            facts_path=current_facts_path,
        )
        _validate_facts_document_against_aggregate(
            facts_document=current_facts_document,
            aggregate_facts_document=aggregate_document,
            expected_relative_path=discovered_file.relative_path,
            expected_content_sha256=discovered_file.content_sha256,
        )

    extra_paths = sorted(set(aggregate_document.files) - discovered_paths)
    if extra_paths:
        raise ValueError(
            "Aggregate FACTS document contains files outside the requested audit scope: "
            f"{', '.join(extra_paths)}. Rerun 'scout-agent extract-facts <project_root>'."
        )

    files_to_review = [file.relative_path for file in discovered_files]

    initial_state: AuditState = {
        "files_to_review": files_to_review,
        "files_reviewed": [],
        "execution_path_consistency_completed": False,
        "verified_findings": [],
        "failures": [],
    }

    return InitializedAudit(
        aggregate_facts_document=aggregate_document,
        initial_state=initial_state,
    )


def _validate_facts_document(
    *,
    facts_document: FactsDocument,
    expected_project_root: Path,
    expected_relative_path: str,
    expected_content_sha256: str,
    facts_path: Path,
) -> None:
    recorded_project_root = Path(facts_document.project_root).expanduser().resolve()
    if recorded_project_root != expected_project_root:
        raise ValueError(
            "Facts document project root does not match the requested audit root. "
            f"Facts file {facts_path} recorded {recorded_project_root}, "
            f"but audit was requested for {expected_project_root}."
        )
    if facts_document.path != expected_relative_path:
        raise ValueError(
            "Facts document path does not match the requested source file. "
            f"Facts file {facts_path} recorded {facts_document.path}, "
            f"expected {expected_relative_path}."
        )
    if facts_document.content_sha256 != expected_content_sha256:
        raise ValueError(
            "Facts document is stale. "
            f"Facts file {facts_path} recorded sha {facts_document.content_sha256}, "
            f"expected {expected_content_sha256}. "
            "Rerun 'scout-agent extract-facts <project_root>'."
        )


def _validate_aggregate_facts_document(
    *,
    aggregate_facts_document: AggregateFactsDocument,
    expected_project_root: Path,
) -> None:
    recorded_project_root = Path(
        aggregate_facts_document.project_root
    ).expanduser().resolve()
    if recorded_project_root != expected_project_root:
        raise ValueError(
            "Aggregate FACTS document project root does not match the requested audit root. "
            f"Aggregate FACTS recorded {recorded_project_root}, "
            f"but audit was requested for {expected_project_root}."
        )


def _validate_facts_document_against_aggregate(
    *,
    facts_document: FactsDocument,
    aggregate_facts_document: AggregateFactsDocument,
    expected_relative_path: str,
    expected_content_sha256: str,
) -> None:
    aggregate_file_facts = aggregate_facts_document.files.get(expected_relative_path)
    if aggregate_file_facts is None:
        raise ValueError(
            "Aggregate FACTS document is missing an in-scope file entry. "
            f"Missing file: {expected_relative_path}. "
            "Rerun 'scout-agent extract-facts <project_root>'."
        )

    if aggregate_file_facts.content_sha256 != expected_content_sha256:
        raise ValueError(
            "Aggregate FACTS document is stale. "
            f"Aggregate FACTS recorded sha {aggregate_file_facts.content_sha256} "
            f"for {expected_relative_path}, expected {expected_content_sha256}. "
            "Rerun 'scout-agent extract-facts <project_root>'."
        )

    if facts_document.project_root != aggregate_facts_document.project_root:
        raise ValueError(
            "Facts document project root does not match aggregate FACTS metadata. "
            f"Facts file {expected_relative_path} recorded {facts_document.project_root}, "
            f"aggregate FACTS recorded {aggregate_facts_document.project_root}."
        )
    if facts_document.model != aggregate_facts_document.model:
        raise ValueError(
            "Facts document model does not match aggregate FACTS metadata. "
            f"Facts file {expected_relative_path} recorded {facts_document.model}, "
            f"aggregate FACTS recorded {aggregate_facts_document.model}."
        )
    if facts_document.llm_mode != aggregate_facts_document.llm_mode:
        raise ValueError(
            "Facts document llm_mode does not match aggregate FACTS metadata. "
            f"Facts file {expected_relative_path} recorded {facts_document.llm_mode}, "
            f"aggregate FACTS recorded {aggregate_facts_document.llm_mode}."
        )

    if _serialize_aggregate_file_facts(
        aggregate_file_facts
    ) != _serialize_facts_document_functions(facts_document):
        raise ValueError(
            "Facts document functions do not match aggregate FACTS entry. "
            f"Mismatched file: {expected_relative_path}. "
            "Rerun 'scout-agent extract-facts <project_root>'."
        )


def _serialize_aggregate_file_facts(
    aggregate_file_facts: AggregateFileFacts,
) -> dict[str, object]:
    return aggregate_file_facts.model_dump(mode="python")["functions"]


def _serialize_facts_document_functions(
    facts_document: FactsDocument,
) -> dict[str, object]:
    return facts_document.model_dump(mode="python")["functions"]
