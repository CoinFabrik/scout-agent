from __future__ import annotations
from pydantic import BaseModel
from pathlib import Path

from enum import Enum
from typing import Literal, TypedDict

from pydantic import Field, model_validator


class ExpertTypeEnum(str, Enum):
    EXECUTION_PATH_CONSISTENCY = "execution_path_consistency"
    COLLECTION_VALIDATION = "collection_validation"
    TIME_STATE = "time_state"
    SENTINEL_LOGIC = "sentinel_logic"


Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
ExpertStatus = Literal["VULNERABLE", "SAFE", "NEEDS_INFO"]


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
    def _validate_finding_presence(self) -> "ExpertResult":
        if self.status == "VULNERABLE" and self.finding is None:
            raise ValueError("VULNERABLE results must include a finding.")
        if self.status != "VULNERABLE" and self.finding is not None:
            raise ValueError("Only VULNERABLE results may include a finding.")
        return self


class FileAuditResponse(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class AuditState(TypedDict):
    project_root: Path
    facts_path: Path
    files_to_review: list[str]
    files_reviewed: list[str]
    verified_findings: list[Finding]
    finding_keys: list[str]
