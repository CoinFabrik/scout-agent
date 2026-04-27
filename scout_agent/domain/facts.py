from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

FACTS_SCHEMA_VERSION: Final[str] = "3"
AGGREGATE_FACTS_FILENAME: Final[str] = "FACTS.yml"
OPTIONAL_FUNCTION_SUMMARY_FIELDS: Final[tuple[str, ...]] = (
    "authorization",
    "vector_params",
    "time_dependent",
    "sentinel_values",
)


class FunctionSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authorization: str | None = Field(default=None, min_length=1)
    vector_params: str | None = Field(default=None, min_length=1)
    time_dependent: str | None = Field(default=None, min_length=1)
    sentinel_values: str | None = Field(default=None, min_length=1)

    @field_validator(*OPTIONAL_FUNCTION_SUMMARY_FIELDS, mode="before")
    @classmethod
    def _normalize_optional_summary_field(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.lower().rstrip(".") == "none":
            return None
        return cleaned


class FactsDocument(BaseModel):
    schema_version: Literal["3"] = FACTS_SCHEMA_VERSION
    generated_at_utc: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    model: str = Field(min_length=1)
    llm_mode: str = Field(min_length=1)
    path: str = Field(min_length=1)
    content_sha256: str = Field(min_length=1)
    functions: dict[str, FunctionSummary] = Field(default_factory=dict)

    @field_validator("functions")
    @classmethod
    def _function_keys_unique_and_non_empty(
        cls,
        value: dict[str, FunctionSummary],
    ) -> dict[str, FunctionSummary]:
        return _normalize_function_mapping(value, "facts document")


class AggregateFileFacts(BaseModel):
    content_sha256: str = Field(min_length=1)
    functions: dict[str, FunctionSummary] = Field(default_factory=dict)

    @field_validator("functions")
    @classmethod
    def _function_keys_unique_and_non_empty(
        cls,
        value: dict[str, FunctionSummary],
    ) -> dict[str, FunctionSummary]:
        return _normalize_function_mapping(value, "aggregate facts entry")


class AggregateFactsDocument(BaseModel):
    schema_version: Literal["3"] = FACTS_SCHEMA_VERSION
    generated_at_utc: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    model: str = Field(min_length=1)
    llm_mode: str = Field(min_length=1)
    files: dict[str, AggregateFileFacts] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def _file_keys_unique_and_non_empty(
        cls,
        value: dict[str, AggregateFileFacts],
    ) -> dict[str, AggregateFileFacts]:
        normalized: dict[str, AggregateFileFacts] = {}

        for relative_path, file_facts in value.items():
            cleaned_path = Path(relative_path.strip()).as_posix()
            if not cleaned_path or cleaned_path == ".":
                raise ValueError(
                    "File paths in aggregate facts document must be non-empty."
                )
            if cleaned_path in normalized:
                raise ValueError(
                    f"Duplicate file path in aggregate facts document: {cleaned_path}"
                )
            normalized[cleaned_path] = file_facts

        return normalized


def facts_file_path(facts_root: Path, relative_path: str) -> Path:
    normalized_relative_path = Path(relative_path.strip()).as_posix()
    if not normalized_relative_path or normalized_relative_path == ".":
        raise ValueError(f"Facts relative path must be non-empty: {relative_path!r}")

    return facts_root / f"{normalized_relative_path}.facts.yaml"


def aggregate_facts_file_path(facts_root: Path) -> Path:
    return facts_root / AGGREGATE_FACTS_FILENAME


def compose_aggregate_facts_document(
    documents: Sequence[FactsDocument],
) -> AggregateFactsDocument:
    if not documents:
        raise ValueError("Cannot compose aggregate facts without file facts.")

    sorted_documents = sorted(documents, key=lambda document: document.path)
    first_document = sorted_documents[0]

    for document in sorted_documents[1:]:
        if document.project_root != first_document.project_root:
            raise ValueError("All facts documents must share the same project_root.")
        if document.model != first_document.model:
            raise ValueError("All facts documents must share the same model.")
        if document.llm_mode != first_document.llm_mode:
            raise ValueError("All facts documents must share the same llm_mode.")

    return AggregateFactsDocument(
        generated_at_utc=max(
            document.generated_at_utc for document in sorted_documents
        ),
        project_root=first_document.project_root,
        model=first_document.model,
        llm_mode=first_document.llm_mode,
        files={
            document.path: AggregateFileFacts(
                content_sha256=document.content_sha256,
                functions=document.functions,
            )
            for document in sorted_documents
        },
    )


def write_facts_document(path: Path, document: FactsDocument) -> None:
    _write_yaml_document(path, document)


def write_aggregate_facts_document(
    path: Path,
    document: AggregateFactsDocument,
) -> None:
    _write_yaml_document(path, document)


def _write_yaml_document(path: Path, document: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = document.model_dump(mode="python", exclude_none=True)

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

    return FactsDocument.model_validate(_load_yaml_mapping(path))


def load_aggregate_facts_document(path: Path) -> AggregateFactsDocument:
    if not path.exists():
        raise FileNotFoundError(f"Aggregate FACTS document does not exist: {path}")

    return AggregateFactsDocument.model_validate(_load_yaml_mapping(path))


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"FACTS document must be a mapping: {path}")
    return payload


def present_summary_fields(summary: FunctionSummary) -> list[tuple[str, str]]:
    return [
        (field_name, value)
        for field_name in OPTIONAL_FUNCTION_SUMMARY_FIELDS
        if (value := getattr(summary, field_name)) is not None
    ]


def _normalize_function_mapping(
    value: dict[str, FunctionSummary],
    document_kind: str,
) -> dict[str, FunctionSummary]:
    normalized: dict[str, FunctionSummary] = {}

    for function_key, summary in value.items():
        cleaned_key = function_key.strip()
        if not cleaned_key:
            raise ValueError(f"Function keys in {document_kind} must be non-empty.")
        if cleaned_key in normalized:
            raise ValueError(
                f"Duplicate function key in {document_kind}: {cleaned_key}"
            )
        normalized[cleaned_key] = summary

    return normalized
