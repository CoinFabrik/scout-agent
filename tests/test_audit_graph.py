from pathlib import Path
from unittest.mock import patch

import pytest

from scout_agent.domain.audit import (
    AuditState,
    Delegation,
    ExpertBatchItem,
    ExpertResult,
    ExpertTypeEnum,
    Finding,
    SupervisorDecision,
)
from scout_agent.domain.facts import (
    AuthorizationFact,
    FactsDocument,
    FileFacts,
    FunctionFactBundle,
    FunctionFacts,
    SentinelValuesFact,
    TimeDependentStateFact,
    VectorParametersFact,
)
from scout_agent.runtime.audit.graph import AuditContext, run_audit
from scout_agent.runtime.audit.reducer import make_delegation_key
from scout_agent.runtime.audit.supervisor import build_supervisor_messages


def _facts_document(project_root: Path) -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(project_root),
        model="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scope_fingerprint="abc123",
        files=[
            FileFacts(
                path="contracts/gateway.rs",
                content_sha256="x",
                functions=[
                    FunctionFacts(
                        function_id="contracts/gateway.rs::vote#L1",
                        name="vote",
                        kind="function",
                        visibility="public",
                        line_start=1,
                        line_end=1,
                        signature="pub fn vote() {}",
                        impl_target=None,
                        facts=FunctionFactBundle(
                            authorization=AuthorizationFact(
                                status="unknown",
                                reasoning="x",
                                evidence=[],
                            ),
                            vector_parameters=VectorParametersFact(
                                status="present",
                                reasoning="x",
                                parameters=["ids: Vec<u32>"],
                            ),
                            time_dependent_state=TimeDependentStateFact(
                                status="absent",
                                reasoning="x",
                                evidence=[],
                            ),
                            sentinel_values=SentinelValuesFact(
                                status="absent",
                                reasoning="x",
                                values=[],
                            ),
                        ),
                    )
                ],
            )
        ],
    )


def _initial_state(project_root: Path, facts_document: FactsDocument) -> AuditState:
    return {
        "project_root": str(project_root),
        "facts_path": str(project_root / "FACTS.yaml"),
        "facts_index": {"contracts/gateway.rs": facts_document.files[0]},
        "files_to_review": ["contracts/gateway.rs"],
        "current_file": "contracts/gateway.rs",
        "last_supervisor_decision": None,
        "pending_delegations": [],
        "completed_delegation_keys": [],
        "needs_info_notes": [],
        "finding_keys": [],
        "files_reviewed": [],
        "verified_findings": [],
        "expert_batch_items": [],
    }


def _runtime(project_root: Path, facts_document: FactsDocument) -> AuditContext:
    return AuditContext(
        project_root=project_root,
        report_path=project_root / "REPORT.md",
        facts_document=facts_document,
        facts_index={"contracts/gateway.rs": facts_document.files[0]},
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=FakeAuditReporter(),
    )


def _delegation(
    *,
    expert_type: ExpertTypeEnum = ExpertTypeEnum.COLLECTION_VALIDATION,
    context_snippet: str = "pub fn vote() {}",
    reasoning: str = "Vector input requires review.",
) -> Delegation:
    return Delegation(
        expert_type=expert_type,
        target_file="contracts/gateway.rs",
        context_snippet=context_snippet,
        reasoning=reasoning,
    )


class FakeAuditReporter:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def started(self, **kwargs) -> None:
        self.events.append(("started", kwargs["total_files"]))

    def file_started(self, **kwargs) -> None:
        self.events.append(("file_started", kwargs["index"], kwargs["current_file"]))

    def delegation_batch(self, **kwargs) -> None:
        self.events.append(
            ("delegation_batch", kwargs["current_file"], kwargs["delegation_count"])
        )

    def supervisor_pass(self, **kwargs) -> None:
        self.events.append(
            (
                "supervisor_pass",
                kwargs["current_file"],
                kwargs["pass_index"],
                kwargs["completed_checks"],
                kwargs["verified_findings"],
                kwargs["needs_info_notes"],
            )
        )

    def duplicate_delegations_filtered(self, **kwargs) -> None:
        self.events.append(
            (
                "duplicate_delegations_filtered",
                kwargs["current_file"],
                kwargs["requested"],
                kwargs["dropped"],
                kwargs["remaining"],
            )
        )

    def finding_verified(self, **kwargs) -> None:
        self.events.append(
            (
                "finding_verified",
                kwargs["total_verified_findings"],
                kwargs["finding"].pattern,
            )
        )

    def file_completed(self, **kwargs) -> None:
        self.events.append(("file_completed", kwargs["reviewed"], kwargs["current_file"]))

    def close(self) -> None:
        self.events.append(("close",))


def test_audit_graph_happy_path(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    runtime = _runtime(tmp_path, facts_document)

    decisions = [
        SupervisorDecision(
            file_fully_analyzed=False,
            delegations=[_delegation()],
        ),
        SupervisorDecision(file_fully_analyzed=True, delegations=[]),
    ]

    with patch("scout_agent.runtime.audit.graph.run_supervisor_decision", side_effect=decisions):
        with patch("scout_agent.runtime.audit.graph.build_expert_execution_context") as mock_context:
            mock_context.return_value.code_snapshot = "   1: pub fn vote() {}"
            mock_context.return_value.search_results = None
            with patch("scout_agent.runtime.audit.graph.run_expert_analysis") as mock_expert:
                mock_expert.return_value = ExpertResult(
                    status="VULNERABLE",
                    finding=Finding(
                        pattern="Duplicate vector elements",
                        severity="HIGH",
                        location="contracts/gateway.rs:22",
                        description="Vector elements are aggregated without uniqueness checks.",
                        evidence="contracts/gateway.rs:22-31",
                    ),
                )

                final_state = run_audit(runtime=runtime, initial_state=initial_state)

    assert final_state["files_to_review"] == []
    assert final_state["files_reviewed"] == ["contracts/gateway.rs"]
    assert len(final_state["verified_findings"]) == 1
    assert (tmp_path / "REPORT.md").exists()


def test_audit_graph_no_finding_path_still_writes_report(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    runtime = _runtime(tmp_path, facts_document)

    decisions = [
        SupervisorDecision(
            file_fully_analyzed=False,
            delegations=[_delegation()],
        ),
        SupervisorDecision(file_fully_analyzed=True, delegations=[]),
    ]

    with patch("scout_agent.runtime.audit.graph.run_supervisor_decision", side_effect=decisions):
        with patch("scout_agent.runtime.audit.graph.build_expert_execution_context") as mock_context:
            mock_context.return_value.code_snapshot = "   1: pub fn vote() {}"
            mock_context.return_value.search_results = None
            with patch("scout_agent.runtime.audit.graph.run_expert_analysis") as mock_expert:
                mock_expert.return_value = ExpertResult(status="SAFE", finding=None)

                final_state = run_audit(runtime=runtime, initial_state=initial_state)

    assert final_state["verified_findings"] == []
    assert (tmp_path / "REPORT.md").exists()


def test_audit_graph_emits_high_level_reporter_events(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    reporter = FakeAuditReporter()
    runtime = AuditContext(
        project_root=tmp_path,
        report_path=tmp_path / "REPORT.md",
        facts_document=facts_document,
        facts_index={"contracts/gateway.rs": facts_document.files[0]},
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=reporter,
    )

    decisions = [
        SupervisorDecision(
            file_fully_analyzed=False,
            delegations=[_delegation()],
        ),
        SupervisorDecision(file_fully_analyzed=True, delegations=[]),
    ]

    with patch("scout_agent.runtime.audit.graph.run_supervisor_decision", side_effect=decisions):
        with patch("scout_agent.runtime.audit.graph.build_expert_execution_context") as mock_context:
            mock_context.return_value.code_snapshot = "   1: pub fn vote() {}"
            mock_context.return_value.search_results = None
            with patch("scout_agent.runtime.audit.graph.run_expert_analysis") as mock_expert:
                mock_expert.return_value = ExpertResult(
                    status="VULNERABLE",
                    finding=Finding(
                        pattern="Duplicate vector elements",
                        severity="HIGH",
                        location="contracts/gateway.rs:22",
                        description="Vector elements are aggregated without uniqueness checks.",
                        evidence="contracts/gateway.rs:22-31",
                    ),
                )

                run_audit(runtime=runtime, initial_state=initial_state)

    assert reporter.events == [
        ("started", 1),
        ("file_started", 1, "contracts/gateway.rs"),
        ("supervisor_pass", "contracts/gateway.rs", 1, 0, 0, 0),
        ("delegation_batch", "contracts/gateway.rs", 1),
        ("finding_verified", 1, "Duplicate vector elements"),
        ("supervisor_pass", "contracts/gateway.rs", 2, 1, 1, 0),
        ("file_completed", 1, "contracts/gateway.rs"),
    ]


def test_audit_graph_rejects_incomplete_file_without_delegations(
    tmp_path: Path,
) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    runtime = _runtime(tmp_path, facts_document)

    with patch(
        "scout_agent.runtime.audit.graph.run_supervisor_decision",
        return_value=SupervisorDecision(file_fully_analyzed=False, delegations=[]),
    ):
        with pytest.raises(
            ValueError,
            match="file_fully_analyzed=false with 0 delegations",
        ):
            run_audit(runtime=runtime, initial_state=initial_state)


def test_audit_graph_rejects_complete_file_with_delegations(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    runtime = _runtime(tmp_path, facts_document)

    with patch(
        "scout_agent.runtime.audit.graph.run_supervisor_decision",
        return_value=SupervisorDecision(
            file_fully_analyzed=True,
            delegations=[_delegation()],
        ),
    ):
        with pytest.raises(
            ValueError,
            match="file_fully_analyzed=true with 1 delegation",
        ):
            run_audit(runtime=runtime, initial_state=initial_state)


def test_audit_graph_duplicate_only_delegations_fail_fast(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    reporter = FakeAuditReporter()
    runtime = AuditContext(
        project_root=tmp_path,
        report_path=tmp_path / "REPORT.md",
        facts_document=facts_document,
        facts_index={"contracts/gateway.rs": facts_document.files[0]},
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=reporter,
    )
    prior_delegation = _delegation(
        expert_type=ExpertTypeEnum.TIME_STATE,
        context_snippet="let ts = e.ledger().timestamp();",
        reasoning="Time-based state update requires review.",
    )
    initial_state["completed_delegation_keys"] = [make_delegation_key(prior_delegation)]
    initial_state["expert_batch_items"] = [
        ExpertBatchItem(
            delegation=prior_delegation,
            result=ExpertResult(status="SAFE", finding=None),
        )
    ]

    with patch(
        "scout_agent.runtime.audit.graph.run_supervisor_decision",
        return_value=SupervisorDecision(
            file_fully_analyzed=False,
            delegations=[prior_delegation],
        ),
    ):
        with pytest.raises(
            ValueError,
            match="only already-completed expert checks",
        ):
            run_audit(runtime=runtime, initial_state=initial_state)

    assert reporter.events == [
        ("started", 1),
        ("file_started", 1, "contracts/gateway.rs"),
        ("supervisor_pass", "contracts/gateway.rs", 1, 1, 0, 0),
        ("duplicate_delegations_filtered", "contracts/gateway.rs", 1, 1, 0),
    ]


def test_audit_graph_partial_duplicate_delegations_continue(tmp_path: Path) -> None:
    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path, facts_document)
    reporter = FakeAuditReporter()
    runtime = AuditContext(
        project_root=tmp_path,
        report_path=tmp_path / "REPORT.md",
        facts_document=facts_document,
        facts_index={"contracts/gateway.rs": facts_document.files[0]},
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        reporter=reporter,
    )
    completed_delegation = _delegation(
        expert_type=ExpertTypeEnum.COLLECTION_VALIDATION,
        context_snippet="pub fn vote() {}",
        reasoning="Vector input requires review.",
    )
    new_delegation = _delegation(
        expert_type=ExpertTypeEnum.SENTINEL_LOGIC,
        context_snippet="if id == 0 { return; }",
        reasoning="Sentinel handling requires review.",
    )
    initial_state["completed_delegation_keys"] = [
        make_delegation_key(completed_delegation)
    ]
    initial_state["expert_batch_items"] = [
        ExpertBatchItem(
            delegation=completed_delegation,
            result=ExpertResult(status="SAFE", finding=None),
        )
    ]

    decisions = [
        SupervisorDecision(
            file_fully_analyzed=False,
            delegations=[completed_delegation, new_delegation],
        ),
        SupervisorDecision(file_fully_analyzed=True, delegations=[]),
    ]

    with patch("scout_agent.runtime.audit.graph.run_supervisor_decision", side_effect=decisions):
        with patch("scout_agent.runtime.audit.graph.build_expert_execution_context") as mock_context:
            mock_context.return_value.code_snapshot = "   1: if id == 0 { return; }"
            mock_context.return_value.search_results = None
            with patch("scout_agent.runtime.audit.graph.run_expert_analysis") as mock_expert:
                mock_expert.return_value = ExpertResult(status="SAFE", finding=None)

                final_state = run_audit(runtime=runtime, initial_state=initial_state)

    assert final_state["files_reviewed"] == ["contracts/gateway.rs"]
    assert reporter.events == [
        ("started", 1),
        ("file_started", 1, "contracts/gateway.rs"),
        ("supervisor_pass", "contracts/gateway.rs", 1, 1, 0, 0),
        ("duplicate_delegations_filtered", "contracts/gateway.rs", 2, 1, 1),
        ("delegation_batch", "contracts/gateway.rs", 1),
        ("supervisor_pass", "contracts/gateway.rs", 2, 2, 0, 0),
        ("file_completed", 1, "contracts/gateway.rs"),
    ]


def test_build_supervisor_messages_include_current_file_memory() -> None:
    facts_document = _facts_document(Path("/tmp/project"))
    completed_delegation = _delegation(
        expert_type=ExpertTypeEnum.TIME_STATE,
        context_snippet="let ts = e.ledger().timestamp();",
        reasoning="Time-based state update requires review.",
    )
    completed_check = ExpertBatchItem(
        delegation=completed_delegation,
        result=ExpertResult(
            status="VULNERABLE",
            finding=Finding(
                pattern="Late accrual update",
                severity="HIGH",
                location="contracts/gateway.rs:22",
                description="Accrual happens after state mutation.",
                evidence="contracts/gateway.rs:20-24",
            ),
        ),
    )
    verified_finding = Finding(
        pattern="Duplicate vector elements",
        severity="HIGH",
        location="contracts/gateway.rs:31",
        description="Duplicate inputs are aggregated without uniqueness checks.",
        evidence="contracts/gateway.rs:31-40",
    )

    messages = build_supervisor_messages(
        current_file="contracts/gateway.rs",
        current_file_facts=facts_document.files[0],
        code_snapshot="   1: pub fn vote() {}",
        notes=[],
        completed_checks=[completed_check],
        verified_findings=[verified_finding],
    )

    prompt = messages[1].content
    assert "Previously completed expert checks for this file:" in prompt
    assert "expert_type=time_state" in prompt
    assert "status=VULNERABLE" in prompt
    assert "finding=Late accrual update at contracts/gateway.rs:22" in prompt
    assert "Already verified findings for this file:" in prompt
    assert "pattern=Duplicate vector elements" in prompt
