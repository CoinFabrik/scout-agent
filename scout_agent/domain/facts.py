from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .base import StrictModel

FACTS_SCHEMA_VERSION: Final[str] = "1"

FactStatus = Literal["present", "absent", "unknown"]
FunctionKind = Literal["function", "method"]
FunctionVisibility = Literal["public", "private", "unknown"]


class AuthorizationFact(StrictModel):
    status: FactStatus
    reasoning: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class VectorParametersFact(StrictModel):
    status: FactStatus
    reasoning: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)


class TimeDependentStateFact(StrictModel):
    status: FactStatus
    reasoning: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)


class SentinelValuesFact(StrictModel):
    status: FactStatus
    reasoning: str = Field(min_length=1)
    values: list[str] = Field(default_factory=list)


class FunctionFactBundle(StrictModel):
    authorization: AuthorizationFact
    vector_parameters: VectorParametersFact
    time_dependent_state: TimeDependentStateFact
    sentinel_values: SentinelValuesFact


class FunctionFacts(StrictModel):
    function_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: FunctionKind
    visibility: FunctionVisibility
    line_start: int
    line_end: int
    signature: str = Field(min_length=1)
    impl_target: str | None = None
    facts: FunctionFactBundle

    @field_validator("line_start", "line_end")
    @classmethod
    def _line_numbers_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("line numbers must be >= 1")
        return value

    @field_validator("line_end")
    @classmethod
    def _line_end_not_before_start(
        cls,
        value: int,
        info: ValidationInfo,
    ) -> int:
        start = info.data.get("line_start")
        if start is not None and value < start:
            raise ValueError("line_end must be >= line_start")
        return value


class FileFacts(StrictModel):
    path: str = Field(min_length=1)
    content_sha256: str = Field(min_length=1)
    functions: list[FunctionFacts] = Field(default_factory=list)


class FactsDocument(StrictModel):
    schema_version: Literal["1"] = FACTS_SCHEMA_VERSION
    generated_at_utc: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    model: str = Field(min_length=1)
    llm_mode: str = Field(min_length=1)
    scope_fingerprint: str = Field(min_length=1)
    files: list[FileFacts] = Field(default_factory=list)

    @field_validator("files")
    @classmethod
    def _file_paths_unique(cls, value: list[FileFacts]) -> list[FileFacts]:
        seen: set[str] = set()
        duplicates: list[str] = []

        for file_facts in value:
            if file_facts.path in seen:
                duplicates.append(file_facts.path)
            seen.add(file_facts.path)

        if duplicates:
            duplicate_list = ", ".join(sorted(set(duplicates)))
            raise ValueError(
                f"Duplicate file paths in facts document: {duplicate_list}"
            )

        return value


def write_facts_document(path: Path, document: FactsDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document.model_dump(mode="python")

    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            payload,
            handle,
            sort_keys=False,
            default_flow_style=False,
        )


def load_facts_document(path: Path) -> FactsDocument:
    if not path.exists():
        raise FileNotFoundError(f"FACTS document does not exist: {path}")

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"FACTS document must be a mapping: {path}")

    return FactsDocument.model_validate(payload)


def build_facts_index(document: FactsDocument) -> dict[str, FileFacts]:
    return {file_facts.path: file_facts for file_facts in document.files}


def list_fact_paths(document: FactsDocument) -> list[str]:
    return [file_facts.path for file_facts in document.files]
