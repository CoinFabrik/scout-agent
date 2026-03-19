from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ExpertStatus = Literal["VULNERABLE", "SAFE", "NEEDS_INFO"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1)
    severity: Severity
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class ExpertResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


class AuditFailure(TypedDict):
    index: int
    relative_path: str
    error_type: str
    message: str


class AuditState(TypedDict):
    files_to_review: list[str]
    files_reviewed: Annotated[list[str], operator.add]
    execution_path_consistency_completed: bool
    verified_findings: Annotated[list[Finding], operator.add]
    failures: Annotated[list[AuditFailure], operator.add]
