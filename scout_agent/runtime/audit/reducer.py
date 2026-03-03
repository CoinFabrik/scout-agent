from __future__ import annotations

from dataclasses import dataclass

from scout_agent.domain.audit import (
    Delegation,
    ExpertResult,
    Finding,
    NeedsInfoNote,
)


@dataclass(frozen=True, slots=True)
class ReducedExpertBatch:
    new_findings: list[Finding]
    new_finding_keys: list[str]
    new_completed_delegation_keys: list[str]
    new_needs_info_notes: list[NeedsInfoNote]


def make_delegation_key(delegation: Delegation) -> str:
    return "|".join(
        [
            delegation.expert_type.value.strip(),
            delegation.target_file.strip(),
            _normalize_text(delegation.context_snippet),
        ]
    )


def make_finding_key(finding: Finding) -> str:
    return "|".join(
        [
            finding.pattern.strip(),
            finding.location.strip(),
            _normalize_text(finding.description),
        ]
    )


def make_needs_info_note(
    delegation: Delegation,
    result: ExpertResult,
) -> NeedsInfoNote:
    if result.status != "NEEDS_INFO":
        raise ValueError(
            "Needs-info notes can only be created from NEEDS_INFO expert results."
        )

    return NeedsInfoNote(
        expert_type=delegation.expert_type,
        target_file=delegation.target_file,
        note=("More context is required for delegation: " f"{delegation.reasoning}"),
    )


def reduce_expert_batch(
    *,
    delegations: list[Delegation],
    results: list[ExpertResult],
    existing_finding_keys: list[str],
    existing_completed_delegation_keys: list[str],
    existing_needs_info_notes: list[NeedsInfoNote],
) -> ReducedExpertBatch:
    if len(delegations) != len(results):
        raise ValueError(
            "Reducer requires the same number of delegations and expert results."
        )

    known_finding_keys = set(existing_finding_keys)
    known_completed_keys = set(existing_completed_delegation_keys)
    known_note_keys = {_make_note_key(note) for note in existing_needs_info_notes}

    new_findings: list[Finding] = []
    new_finding_keys: list[str] = []
    new_completed_delegation_keys: list[str] = []
    new_needs_info_notes: list[NeedsInfoNote] = []

    for delegation, result in zip(delegations, results, strict=True):
        delegation_key = make_delegation_key(delegation)

        if result.status != "NEEDS_INFO":
            if delegation_key not in known_completed_keys:
                known_completed_keys.add(delegation_key)
                new_completed_delegation_keys.append(delegation_key)

        if result.status == "VULNERABLE":
            if result.finding is None:
                raise ValueError("VULNERABLE expert result must include a finding.")

            finding_key = make_finding_key(result.finding)
            if finding_key not in known_finding_keys:
                known_finding_keys.add(finding_key)
                new_finding_keys.append(finding_key)
                new_findings.append(result.finding)

        elif result.status == "NEEDS_INFO":
            note = make_needs_info_note(delegation, result)
            note_key = _make_note_key(note)
            if note_key not in known_note_keys:
                known_note_keys.add(note_key)
                new_needs_info_notes.append(note)

    return ReducedExpertBatch(
        new_findings=new_findings,
        new_finding_keys=new_finding_keys,
        new_completed_delegation_keys=new_completed_delegation_keys,
        new_needs_info_notes=new_needs_info_notes,
    )


def _make_note_key(note: NeedsInfoNote) -> str:
    return "|".join(
        [
            note.expert_type.value.strip(),
            note.target_file.strip(),
            _normalize_text(note.note),
        ]
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())
