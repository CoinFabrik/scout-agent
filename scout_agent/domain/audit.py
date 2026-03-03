from __future__ import annotations

import operator
from collections.abc import Sequence
from enum import Enum
from typing import Annotated, Literal, TypedDict

from pydantic import Field, model_validator

from scout_agent.domain.base import StrictModel
from scout_agent.domain.facts import FileFacts


class ExpertTypeEnum(str, Enum):
    EXECUTION_PATH_CONSISTENCY = "execution_path_consistency"
    COLLECTION_VALIDATION = "collection_validation"
    TIME_STATE = "time_state"
    SENTINEL_LOGIC = "sentinel_logic"


class Delegation(StrictModel):
    expert_type: ExpertTypeEnum
    target_file: str = Field(min_length=1)
    context_snippet: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)


class SupervisorDecision(StrictModel):
    file_fully_analyzed: bool
    delegations: list[Delegation] = Field(default_factory=list)


Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ExpertStatus = Literal["VULNERABLE", "SAFE", "NEEDS_INFO"]


class Finding(StrictModel):
    pattern: str = Field(min_length=1)
    severity: Severity
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class ExpertResult(StrictModel):
    status: ExpertStatus
    finding: Finding | None = None

    @model_validator(mode="after")
    def _validate_finding_presence(self) -> "ExpertResult":
        if self.status == "VULNERABLE" and self.finding is None:
            raise ValueError("VULNERABLE results must include a finding.")
        if self.status != "VULNERABLE" and self.finding is not None:
            raise ValueError("Only VULNERABLE results may include a finding.")
        return self


class ExpertBatchItem(StrictModel):
    delegation: Delegation
    result: ExpertResult


class NeedsInfoNote(StrictModel):
    expert_type: ExpertTypeEnum
    target_file: str = Field(min_length=1)
    note: str = Field(min_length=1)


def _replace_or_append(
    existing: list[ExpertBatchItem],
    update: Sequence[ExpertBatchItem],
) -> list[ExpertBatchItem]:
    """Accumulate items from parallel fan-in, but treat an explicit empty
    list as a signal to clear (used by the reducer after processing a batch)."""
    if isinstance(update, list) and len(update) == 0:
        return []
    return [*existing, *update]


class AuditState(TypedDict):
    project_root: str
    facts_path: str
    facts_index: dict[str, FileFacts]
    files_to_review: list[str]
    current_file: str | None
    last_supervisor_decision: SupervisorDecision | None
    pending_delegations: list[Delegation]
    completed_delegation_keys: list[str]
    needs_info_notes: list[NeedsInfoNote]
    finding_keys: list[str]
    files_reviewed: Annotated[list[str], operator.add]
    verified_findings: Annotated[list[Finding], operator.add]
    completed_expert_batch_items: Annotated[list[ExpertBatchItem], _replace_or_append]
    expert_batch_items: Annotated[list[ExpertBatchItem], _replace_or_append]
    last_announced_file: str | None
    supervisor_pass_counts: dict[str, int]
