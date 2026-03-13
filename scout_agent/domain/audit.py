from __future__ import annotations
from typing import Literal, TypedDict

from pydantic import BaseModel
from pydantic import Field, model_validator


Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ExpertStatus = Literal["VULNERABLE", "SAFE", "NEEDS_INFO"]
FinalDedupStatus = Literal["not_run", "applied", "skipped"]


class Finding(BaseModel):
    pattern: str = Field(min_length=1)
    severity: Severity
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class ExpertResult(BaseModel):
    status: ExpertStatus
    finding: Finding | None = None

    @model_validator(mode="after")
    def _validate_finding_presence(self) -> ExpertResult:
        if self.status == "VULNERABLE" and self.finding is None:
            raise ValueError("VULNERABLE results must include a finding.")
        if self.status != "VULNERABLE" and self.finding is not None:
            raise ValueError("Only VULNERABLE results may include a finding.")
        return self


class FileAuditResponse(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class FinalDedupGroup(BaseModel):
    member_indices: list[int] = Field(min_length=1)


class FinalDedupResponse(BaseModel):
    groups: list[FinalDedupGroup] = Field(min_length=1)


class AuditState(TypedDict):
    files_to_review: list[str]
    files_reviewed: list[str]
    execution_path_consistency_completed: bool
    final_dedup_status: FinalDedupStatus
    final_dedup_removed_count: int
    pre_final_dedup_finding_count: int
    verified_findings: list[Finding]
