from pathlib import Path

import pytest

from scout_agent.domain.audit import AuditState, Finding
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
from scout_agent.runtime.audit.report_writer import (
    render_report_markdown,
    validate_report_coverage,
    write_report,
)


def _document() -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root="/tmp/project",
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
                                status="unknown",
                                reasoning="x",
                                parameters=[],
                            ),
                            time_dependent_state=TimeDependentStateFact(
                                status="unknown",
                                reasoning="x",
                                evidence=[],
                            ),
                            sentinel_values=SentinelValuesFact(
                                status="unknown",
                                reasoning="x",
                                values=[],
                            ),
                        ),
                    )
                ],
            )
        ],
    )


def _state(*, findings: list[Finding], files_to_review: list[str] | None = None) -> AuditState:
    return {
        "project_root": "/tmp/project",
        "facts_path": "/tmp/project/FACTS.yaml",
        "facts_index": {"contracts/gateway.rs": _document().files[0]},
        "files_to_review": [] if files_to_review is None else files_to_review,
        "current_file": None if files_to_review in (None, []) else files_to_review[0],
        "last_supervisor_decision": None,
        "pending_delegations": [],
        "completed_delegation_keys": [],
        "needs_info_notes": [],
        "finding_keys": [],
        "files_reviewed": ["contracts/gateway.rs"],
        "verified_findings": findings,
        "expert_batch_items": [],
    }


def test_report_writer_rejects_incomplete_coverage() -> None:
    with pytest.raises(ValueError, match="files_to_review"):
        validate_report_coverage(
            facts_document=_document(),
            state=_state(findings=[], files_to_review=["contracts/gateway.rs"]),
        )


def test_report_writer_renders_zero_findings() -> None:
    markdown = render_report_markdown(
        facts_document=_document(),
        state=_state(findings=[]),
    )

    assert "# Scout-Agent Report" in markdown
    assert "No verified findings." in markdown
    assert "- contracts/gateway.rs" in markdown


def test_report_writer_orders_findings_by_severity_then_location(
    tmp_path: Path,
) -> None:
    low_finding = Finding(
        pattern="Low issue",
        severity="LOW",
        location="contracts/gateway.rs:99",
        description="A low severity issue.",
        evidence="contracts/gateway.rs:99",
    )
    high_finding = Finding(
        pattern="High issue",
        severity="HIGH",
        location="contracts/gateway.rs:22",
        description="A high severity issue.",
        evidence="contracts/gateway.rs:22",
    )

    state = _state(findings=[low_finding, high_finding])
    markdown = render_report_markdown(
        facts_document=_document(),
        state=state,
    )

    assert markdown.index("High issue") < markdown.index("Low issue")

    report_path = tmp_path / "REPORT.md"
    written_path = write_report(
        report_path=report_path,
        facts_document=_document(),
        state=state,
    )
    assert written_path == report_path.resolve()
    assert report_path.exists()


