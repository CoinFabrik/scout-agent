from __future__ import annotations
from pydantic import BaseModel

from collections.abc import Sequence

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ConfigDict, Field, ValidationError, field_validator
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_exponential_jitter

from scout_agent.domain.facts import FunctionSummary
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.extract.models import RetryableExtractionError
from scout_agent.runtime.source.rust_parser import ParsedRustFile, ParsedRustFunction

FACTS_EXTRACTION_SYSTEM_PROMPT = """You extract concise semantic summaries for Rust smart-contract functions.

You are given:
- one sanitized Rust source file
- the exact canonical function keys that must be summarized

Your job:
- return a one line summary for each listed function key
- summarize only these four fields:
  1. authorization
  2. vector_params
  3. time_dependent
  4. sentinel_values

Rules:
- Each summary must be a single line.
- Use only the provided file source and function inventory.
- Do not omit any function key.
- Do not invent functions or keys.
- Keep summaries specific to the function and file.
"""

EXTRACTION_RETRY_NOTE = (
    "Previous response used invalid function_key values or malformed structure. "
    "Return the same schema again and reuse the exact function_key strings from "
    "the inventory verbatim, preserving every path character, slash, colon, and "
    "whitespace exactly as shown."
)


class ExtractedFunctionSummary(BaseModel):
    function_key: str = Field(min_length=1)
    summary: FunctionSummary


class FileFactsExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    functions: list[ExtractedFunctionSummary] = Field(default_factory=list)

    @field_validator("functions")
    @classmethod
    def _function_keys_unique(
        cls,
        value: list[ExtractedFunctionSummary],
    ) -> list[ExtractedFunctionSummary]:
        seen: set[str] = set()
        duplicates: list[str] = []

        for item in value:
            if item.function_key in seen:
                duplicates.append(item.function_key)
            seen.add(item.function_key)

        if duplicates:
            duplicate_list = ", ".join(sorted(set(duplicates)))
            raise ValueError(
                f"Duplicate function_key values in extraction response: {duplicate_list}"
            )

        return value


def extract_file_facts_with_llm(
    parsed_file: ParsedRustFile,
    *,
    model_name: str,
    llm_mode: str,
) -> dict[str, FunctionSummary]:
    function_inventory = build_function_inventory(parsed_file)
    if not function_inventory:
        return {}

    model = build_chat_model(model_name, llm_mode)
    structured_model = model.with_structured_output(
        FileFactsExtractionResponse,
        method="json_schema",
    )
    for attempt in Retrying(
        retry=retry_if_exception_type(RetryableExtractionError),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        reraise=True,
    ):
        with attempt:
            retry_note = (
                EXTRACTION_RETRY_NOTE
                if attempt.retry_state.attempt_number > 1
                else None
            )
            return _extract_file_facts_once(
                parsed_file,
                function_inventory=function_inventory,
                structured_model=structured_model,
                retry_note=retry_note,
            )

    raise AssertionError("Retry loop exited without returning or raising.")


def build_canonical_function_key(
    *,
    relative_path: str,
    function: ParsedRustFunction,
) -> str:
    if function.impl_target:
        return f"{relative_path}::{function.impl_target}::{function.name}"
    return f"{relative_path}::{function.name}"


def build_function_inventory(
    parsed_file: ParsedRustFile,
) -> list[tuple[str, ParsedRustFunction]]:
    inventory: list[tuple[str, ParsedRustFunction]] = []
    seen_keys: set[str] = set()

    for function in parsed_file.functions:
        function_key = build_canonical_function_key(
            relative_path=parsed_file.relative_path,
            function=function,
        )
        if function_key in seen_keys:
            raise ValueError(
                "Canonical function key collision in "
                f"{parsed_file.relative_path}: {function_key}"
            )
        seen_keys.add(function_key)
        inventory.append((function_key, function))

    return inventory


def build_file_facts_extraction_messages(
    parsed_file: ParsedRustFile,
    function_inventory: Sequence[tuple[str, ParsedRustFunction]],
    *,
    retry_note: str | None = None,
) -> list[BaseMessage]:
    inventory = _format_function_inventory(function_inventory)
    numbered_source = _format_numbered_source(parsed_file.source_text)
    retry_section = ""
    if retry_note is not None:
        retry_section = f"Retry correction:\n{retry_note}\n\n"
    user_prompt = (
        f"{retry_section}"
        f"File: {parsed_file.relative_path}\n\n"
        "Function inventory:\n"
        f"{inventory}\n\n"
        "Full sanitized file source with line numbers:\n"
        f"{numbered_source}\n"
    )

    return [
        SystemMessage(content=FACTS_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]


def merge_extracted_file_facts(
    parsed_file: ParsedRustFile,
    *,
    extracted: FileFactsExtractionResponse,
) -> dict[str, FunctionSummary]:
    expected_inventory = build_function_inventory(parsed_file)
    expected_keys = [function_key for function_key, _ in expected_inventory]
    extracted_by_key = {item.function_key: item.summary for item in extracted.functions}

    missing_keys = [
        function_key
        for function_key in expected_keys
        if function_key not in extracted_by_key
    ]
    unexpected_keys = [
        function_key
        for function_key in extracted_by_key
        if function_key not in set(expected_keys)
    ]

    if missing_keys or unexpected_keys:
        problems: list[str] = []
        if missing_keys:
            problems.append(f"missing={missing_keys}")
        if unexpected_keys:
            problems.append(f"unexpected={unexpected_keys}")
        joined = "; ".join(problems)
        raise RetryableExtractionError(
            "Extraction response does not match parsed function inventory for "
            f"{parsed_file.relative_path}: {joined}"
        )

    return {
        function_key: extracted_by_key[function_key] for function_key in expected_keys
    }


def _format_function_inventory(
    function_inventory: Sequence[tuple[str, ParsedRustFunction]],
) -> str:
    lines: list[str] = []

    for function_key, function in function_inventory:
        impl_part = (
            f", impl_target={function.impl_target}"
            if function.impl_target is not None
            else ""
        )
        lines.append(
            "- "
            f"key={function_key}, "
            f"name={function.name}, "
            f"kind={function.kind}, "
            f"visibility={function.visibility}, "
            f"lines={function.line_start}-{function.line_end}"
            f"{impl_part}, "
            f"signature={function.signature}"
        )

    return "\n".join(lines) if lines else "- No functions discovered."


def _format_numbered_source(source_text: str) -> str:
    numbered_lines = [
        f"{line_number:4}: {line}"
        for line_number, line in enumerate(source_text.splitlines(), start=1)
    ]
    return "\n".join(numbered_lines)


def _extract_file_facts_once(
    parsed_file: ParsedRustFile,
    *,
    function_inventory: Sequence[tuple[str, ParsedRustFunction]],
    structured_model,
    retry_note: str | None,
) -> dict[str, FunctionSummary]:
    try:
        response = structured_model.invoke(
            build_file_facts_extraction_messages(
                parsed_file,
                function_inventory,
                retry_note=retry_note,
            )
        )
    except OutputParserException as exc:
        raise RetryableExtractionError(
            "Extraction response could not be parsed for "
            f"{parsed_file.relative_path}: {exc}"
        ) from exc

    try:
        if not isinstance(response, FileFactsExtractionResponse):
            response = FileFactsExtractionResponse.model_validate(response)
    except ValidationError as exc:
        raise RetryableExtractionError(
            "Extraction response failed schema validation for "
            f"{parsed_file.relative_path}: {exc}"
        ) from exc

    return merge_extracted_file_facts(
        parsed_file,
        extracted=response,
    )
