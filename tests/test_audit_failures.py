from __future__ import annotations

from scout_agent.domain.audit import (
    AuditFailure,
    AuditState,
    latest_unresolved_failures,
)


def _failure(relative_path: str) -> AuditFailure:
    return {
        "index": 1,
        "relative_path": relative_path,
        "error_type": "PolicyViolationError",
        "message": "failed",
    }


def test_latest_unresolved_failures_ignores_stale_retry_keys_after_success() -> None:
    state: AuditState = {
        "files_to_review": [],
        "files_reviewed": ["src/lib.rs"],
        "execution_path_consistency_completed": True,
        "verified_findings": [],
        "failures": [_failure("src/lib.rs")],
        "retry_generations": {"src/lib.rs": 1},
    }

    assert latest_unresolved_failures(state) == []


def test_latest_unresolved_failures_reports_pending_file_failures_without_retry() -> (
    None
):
    failure = _failure("src/lib.rs")
    state: AuditState = {
        "files_to_review": ["src/lib.rs"],
        "files_reviewed": [],
        "execution_path_consistency_completed": True,
        "verified_findings": [],
        "failures": [failure],
        "retry_generations": {},
    }

    assert latest_unresolved_failures(state) == [failure]


def test_latest_unresolved_failures_reports_incomplete_epc_failure() -> None:
    failure = _failure("execution_path_consistency")
    state: AuditState = {
        "files_to_review": [],
        "files_reviewed": [],
        "execution_path_consistency_completed": False,
        "verified_findings": [],
        "failures": [failure],
        "retry_generations": {"__execution_path_consistency__": 1},
    }

    assert latest_unresolved_failures(state) == [failure]
