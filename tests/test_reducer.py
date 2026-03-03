from scout_agent.domain.audit import (
    Delegation,
    ExpertResult,
    ExpertTypeEnum,
    Finding,
)
from scout_agent.runtime.audit.reducer import reduce_expert_batch


def test_reducer_deduplicates_existing_finding() -> None:
    delegation = Delegation(
        expert_type=ExpertTypeEnum.SENTINEL_LOGIC,
        target_file="contracts/gateway.rs",
        context_snippet="u32::MAX",
        reasoning="Sentinel handling needs review.",
    )
    finding = Finding(
        pattern="Unsafe sentinel handling",
        severity="MEDIUM",
        location="contracts/gateway.rs:40",
        description="Reader path does not branch on sentinel state.",
        evidence="contracts/gateway.rs:40-48",
    )
    result = ExpertResult(status="VULNERABLE", finding=finding)

    reduced = reduce_expert_batch(
        delegations=[delegation],
        results=[result],
        existing_finding_keys=[
            "Unsafe sentinel handling|contracts/gateway.rs:40|Reader path does not branch on sentinel state."
        ],
        existing_completed_delegation_keys=[
            "sentinel_logic|contracts/gateway.rs|u32::MAX"
        ],
        existing_needs_info_notes=[],
    )

    assert reduced.new_findings == []
    assert reduced.new_finding_keys == []
    assert reduced.new_completed_delegation_keys == []


def test_reducer_materializes_needs_info_note() -> None:
    delegation = Delegation(
        expert_type=ExpertTypeEnum.TIME_STATE,
        target_file="contracts/gateway.rs",
        context_snippet="e.ledger().timestamp()",
        reasoning="Time sequencing may matter.",
    )
    result = ExpertResult(status="NEEDS_INFO", finding=None)

    reduced = reduce_expert_batch(
        delegations=[delegation],
        results=[result],
        existing_finding_keys=[],
        existing_completed_delegation_keys=[],
        existing_needs_info_notes=[],
    )

    assert reduced.new_findings == []
    assert reduced.new_finding_keys == []
    assert reduced.new_completed_delegation_keys == []
    assert len(reduced.new_needs_info_notes) == 1
    assert reduced.new_needs_info_notes[0].note.startswith(
        "More context is required for delegation:"
    )
