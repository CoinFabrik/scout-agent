from __future__ import annotations

from pathlib import Path

import pytest

from scout_agent.domain.audit import AuditState, Finding
from scout_agent.domain.facts import FactsDocument, FunctionSummary
from scout_agent.runtime.audit.report_writer import (
    render_report_markdown,
    validate_report_coverage,
    write_report,
)


def _facts_document(project_root: Path) -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(project_root),
        model="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scope_fingerprint="abc123",
        functions={
            "contracts/gateway.rs::vote": FunctionSummary(
                authorization="Requires caller authorization.",
                vector_params="Accepts a vote vector.",
                time_dependent="None.",
                sentinel_values="None.",
            )
        },
    )


def _state(
    *,
    findings: list[Finding],
    files_to_review: list[str] | None = None,
) -> AuditState:
    return {
        "project_root": "/tmp/project",
        "facts_path": "/tmp/project/FACTS.yaml",
        "files_to_review": [] if files_to_review is None else files_to_review,
        "files_reviewed": ["contracts/gateway.rs"],
        "verified_findings": findings,
        "finding_keys": [],
    }


def test_report_writer_rejects_incomplete_coverage(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="files_to_review"):
        validate_report_coverage(
            state=_state(findings=[], files_to_review=["contracts/gateway.rs"]),
        )


def test_report_writer_renders_zero_findings(tmp_path: Path) -> None:
    markdown = render_report_markdown(
        facts_document=_facts_document(tmp_path),
        state=_state(findings=[]),
    )

    assert "No verified findings." in markdown
    assert "- contracts/gateway.rs" in markdown


def test_report_writer_orders_findings_by_severity_then_location(
    tmp_path: Path,
) -> None:
    markdown = render_report_markdown(
        facts_document=_facts_document(tmp_path),
        state=_state(
            findings=[
                Finding(
                    pattern="Low issue",
                    severity="LOW",
                    location="contracts/gateway.rs:99",
                    description="x",
                    evidence="y",
                ),
                Finding(
                    pattern="High issue",
                    severity="HIGH",
                    location="contracts/gateway.rs:22",
                    description="x",
                    evidence="y",
                ),
            ]
        ),
    )

    assert markdown.index("High issue") < markdown.index("Low issue")


def test_write_report_writes_markdown_file(tmp_path: Path) -> None:
    written_path = write_report(
        report_path=tmp_path / "REPORT.md",
        facts_document=_facts_document(tmp_path),
        state=_state(findings=[]),
    )

    assert written_path == (tmp_path / "REPORT.md").resolve()
    assert written_path.read_text(encoding="utf-8").startswith("# Scout-Agent Report")
