from __future__ import annotations
from pydantic import BaseModel

from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import ConfigDict, Field, field_validator


FACTS_SCHEMA_VERSION: Final[str] = "2"
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
    schema_version: Literal["2"] = FACTS_SCHEMA_VERSION
    generated_at_utc: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    model: str = Field(min_length=1)
    llm_mode: str = Field(min_length=1)
    scope_fingerprint: str = Field(min_length=1)
    functions: dict[str, FunctionSummary] = Field(default_factory=dict)

    @field_validator("functions")
    @classmethod
    def _function_keys_unique_and_non_empty(
        cls,
        value: dict[str, FunctionSummary],
    ) -> dict[str, FunctionSummary]:
        normalized: dict[str, FunctionSummary] = {}

        for function_key, summary in value.items():
            cleaned_key = function_key.strip()
            if not cleaned_key:
                raise ValueError("Function keys in facts document must be non-empty.")
            if cleaned_key in normalized:
                raise ValueError(
                    f"Duplicate function key in facts document: {cleaned_key}"
                )
            normalized[cleaned_key] = summary

        return normalized


def write_facts_document(path: Path, document: FactsDocument) -> None:
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

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"FACTS document must be a mapping: {path}")

    return FactsDocument.model_validate(payload)


def functions_for_file(
    document: FactsDocument,
    relative_path: str,
) -> dict[str, FunctionSummary]:
    return {
        function_key: summary
        for function_key, summary in document.functions.items()
        if file_path_from_function_key(function_key) == relative_path
    }


def count_functions(document: FactsDocument) -> int:
    return len(document.functions)


def fact_paths(document: FactsDocument) -> set[str]:
    return {
        file_path_from_function_key(function_key) for function_key in document.functions
    }


def build_file_fact_index(
    document: FactsDocument,
) -> dict[str, dict[str, FunctionSummary]]:
    file_index: dict[str, dict[str, FunctionSummary]] = {}

    for function_key, summary in document.functions.items():
        file_path = file_path_from_function_key(function_key)
        file_index.setdefault(file_path, {})[function_key] = summary

    return file_index


def present_summary_fields(summary: FunctionSummary) -> list[tuple[str, str]]:
    return [
        (field_name, value)
        for field_name in OPTIONAL_FUNCTION_SUMMARY_FIELDS
        if (value := getattr(summary, field_name)) is not None
    ]


def file_path_from_function_key(function_key: str) -> str:
    cleaned_key = function_key.strip()
    separator_index = cleaned_key.find(".rs::")
    if separator_index == -1:
        raise ValueError(
            "Function key does not contain a Rust source path prefix: "
            f"{function_key}"
        )

    return cleaned_key[: separator_index + len(".rs")]
