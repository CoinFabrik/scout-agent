from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Final

from scout_agent.domain.audit import AuditState, Finding
from scout_agent.domain.facts import AggregateFactsDocument
from scout_agent.runtime.time_utils import utc_now_iso

SEVERITY_ORDER: Final[dict[str, int]] = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


def validate_report_coverage(
    *,
    state: AuditState,
) -> None:
    if state["files_to_review"]:
        raise ValueError(
            "Cannot generate REPORT.md while files_to_review is not empty: "
            f"{state['files_to_review']}"
        )
    if not state["execution_path_consistency_completed"]:
        raise ValueError(
            "Cannot generate REPORT.md before execution_path_consistency completes."
        )


def render_report_markdown(
    *,
    aggregate_facts_document: AggregateFactsDocument,
    state: AuditState,
) -> str:
    validate_report_coverage(state=state)

    findings = _sorted_findings(state["verified_findings"])
    severity_counts = Counter(finding.severity for finding in findings)
    generated_at_utc = utc_now_iso()

    sections = [
        "# Scout-Agent Report",
        "",
        "## Metadata",
        f"- Project root: `{aggregate_facts_document.project_root}`",
        f"- Generated at UTC: `{generated_at_utc}`",
        f"- Model: `{aggregate_facts_document.model}`",
        f"- LLM mode: `{aggregate_facts_document.llm_mode}`",
        f"- execution_path_consistency completed: `{state['execution_path_consistency_completed']}`",
        f"- Final dedup status: `{state['final_dedup_status']}`",
        f"- Findings before final dedup: {state['pre_final_dedup_finding_count']}",
        f"- Duplicates removed by final dedup: {state['final_dedup_removed_count']}",
        "",
        "## Summary",
        f"- Files reviewed: {len(state['files_reviewed'])}",
        f"- Findings after final dedup: {len(findings)}",
        f"- CRITICAL: {severity_counts.get('CRITICAL', 0)}",
        f"- HIGH: {severity_counts.get('HIGH', 0)}",
        f"- MEDIUM: {severity_counts.get('MEDIUM', 0)}",
        f"- LOW: {severity_counts.get('LOW', 0)}",
        "",
        "## Findings",
    ]

    if not findings:
        sections.extend(
            [
                "",
                "No verified findings.",
            ]
        )
    else:
        for index, finding in enumerate(findings, start=1):
            sections.extend(
                [
                    "",
                    f"## Finding {index}",
                    f"- Pattern: {finding.pattern}",
                    f"- Severity: {finding.severity}",
                    f"- Location: {finding.location}",
                    f"- Description: {finding.description}",
                    f"- Evidence: {finding.evidence}",
                ]
            )

    sections.extend(
        [
            "",
            "## Coverage Appendix",
            "",
            *[f"- {path}" for path in state["files_reviewed"]],
            "",
        ]
    )

    return "\n".join(sections)


def write_report(
    *,
    report_path: Path,
    aggregate_facts_document: AggregateFactsDocument,
    state: AuditState,
) -> Path:
    markdown = render_report_markdown(
        aggregate_facts_document=aggregate_facts_document,
        state=state,
    )
    resolved_path = report_path.resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(markdown, encoding="utf-8")
    return resolved_path


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            SEVERITY_ORDER[finding.severity],
            finding.location,
        ),
    )
