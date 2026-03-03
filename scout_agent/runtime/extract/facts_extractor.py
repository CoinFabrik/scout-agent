from __future__ import annotations

from collections.abc import Sequence

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from scout_agent.domain.facts import FileFacts, FunctionFactBundle, FunctionFacts
from scout_agent.llm.providers import build_chat_model
from scout_agent.runtime.source.rust_parser import ParsedRustFile, ParsedRustFunction

FACTS_EXTRACTION_SYSTEM_PROMPT = """You extract semantic facts for Rust smart contract functions.

You are given:
- one Rust source file
- a structural inventory slice listing one or more functions discovered in that file

Your job:
- return semantic facts for every listed function_id in the provided inventory slice
- fill only these four fact groups:
  1. authorization
  2. vector_parameters
  3. time_dependent_state
  4. sentinel_values

Rules:
- Use only the provided file source and function inventory.
- Do not report vulnerabilities.
- Do not omit any function_id.
- Do not invent functions that are not in the inventory.
- Use status='present' when the fact is supported by the file.
- Use status='absent' when the fact is confidently not present in the file.
- Use status='unknown' when semantic intent is unclear or broader codebase context would be required.
- Keep reasoning concise and specific.
- Evidence strings should be compact and cite the file and line when possible, for example:
  contracts/gateway.rs:14 require_auth()
- For vector_parameters.parameters, include only actual vector or array-like parameters relevant to that function.
- For sentinel_values.values, include only values that appear to be used as sentinel or special-status markers, such as u32::MAX, None, or 0.
"""

MAX_FUNCTIONS_PER_EXTRACTION_BATCH = 12


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedFunctionFacts(StrictModel):
    function_id: str = Field(min_length=1)
    facts: FunctionFactBundle


class FileFactsExtractionResponse(StrictModel):
    functions: list[ExtractedFunctionFacts] = Field(default_factory=list)

    @field_validator("functions")
    @classmethod
    def _function_ids_unique(
        cls,
        value: list[ExtractedFunctionFacts],
    ) -> list[ExtractedFunctionFacts]:
        seen: set[str] = set()
        duplicates: list[str] = []

        for item in value:
            if item.function_id in seen:
                duplicates.append(item.function_id)
            seen.add(item.function_id)

        if duplicates:
            duplicate_list = ", ".join(sorted(set(duplicates)))
            raise ValueError(
                f"Duplicate function_id values in extraction response: {duplicate_list}"
            )

        return value


def extract_file_facts_with_llm(
    parsed_file: ParsedRustFile,
    *,
    content_sha256: str,
    model_name: str,
    llm_mode: str,
) -> FileFacts:
    model = build_chat_model(model_name, llm_mode)
    structured_model = model.with_structured_output(
        FileFactsExtractionResponse,
        method="json_schema",
    )
    response = FileFactsExtractionResponse(
        functions=_extract_file_batches(
            structured_model,
            parsed_file,
        )
    )

    return merge_extracted_file_facts(
        parsed_file,
        content_sha256=content_sha256,
        extracted=response,
    )


def build_file_facts_extraction_messages(
    parsed_file: ParsedRustFile,
    functions: Sequence[ParsedRustFunction] | None = None,
) -> list[BaseMessage]:
    selected_functions = list(functions or parsed_file.functions)
    inventory = _format_function_inventory(selected_functions)
    numbered_source = _format_numbered_source(parsed_file.source_text)
    batch_instruction = ""
    if len(selected_functions) != len(parsed_file.functions):
        batch_instruction = (
            "Return facts only for the function_ids listed in this batch.\n\n"
        )

    user_prompt = (
        f"File: {parsed_file.relative_path}\n\n"
        f"{batch_instruction}"
        "Function inventory:\n"
        f"{inventory}\n\n"
        "Full file source with line numbers:\n"
        f"{numbered_source}\n"
    )

    return [
        SystemMessage(content=FACTS_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]


def merge_extracted_file_facts(
    parsed_file: ParsedRustFile,
    *,
    content_sha256: str,
    extracted: FileFactsExtractionResponse,
) -> FileFacts:
    extracted_by_id = {item.function_id: item for item in extracted.functions}
    expected_ids = [function.function_id for function in parsed_file.functions]

    missing_ids = [
        function_id
        for function_id in expected_ids
        if function_id not in extracted_by_id
    ]
    unexpected_ids = [
        function_id
        for function_id in extracted_by_id
        if function_id not in set(expected_ids)
    ]

    if missing_ids or unexpected_ids:
        problems: list[str] = []
        if missing_ids:
            problems.append(f"missing={missing_ids}")
        if unexpected_ids:
            problems.append(f"unexpected={unexpected_ids}")
        joined = "; ".join(problems)
        raise ValueError(
            f"Extraction response does not match parsed function inventory for "
            f"{parsed_file.relative_path}: {joined}"
        )

    merged_functions: list[FunctionFacts] = []
    for function in parsed_file.functions:
        merged = extracted_by_id[function.function_id]
        merged_functions.append(
            FunctionFacts(
                function_id=function.function_id,
                name=function.name,
                kind=function.kind,
                visibility=function.visibility,
                line_start=function.line_start,
                line_end=function.line_end,
                signature=function.signature,
                impl_target=function.impl_target,
                facts=merged.facts,
            )
        )

    return FileFacts(
        path=parsed_file.relative_path,
        content_sha256=content_sha256,
        functions=merged_functions,
    )


def _extract_file_batches(
    structured_model,
    parsed_file: ParsedRustFile,
) -> list[ExtractedFunctionFacts]:
    extracted: list[ExtractedFunctionFacts] = []
    for batch in _iter_function_batches(
        parsed_file.functions,
        batch_size=MAX_FUNCTIONS_PER_EXTRACTION_BATCH,
    ):
        extracted.extend(
            _extract_function_batch(
                structured_model,
                parsed_file,
                batch,
            )
        )
    return extracted


def _extract_function_batch(
    structured_model,
    parsed_file: ParsedRustFile,
    functions: Sequence[ParsedRustFunction],
) -> list[ExtractedFunctionFacts]:
    batch = list(functions)
    try:
        response = structured_model.invoke(
            build_file_facts_extraction_messages(parsed_file, batch)
        )
        if not isinstance(response, FileFactsExtractionResponse):
            response = FileFactsExtractionResponse.model_validate(response)
    except (OutputParserException, ValidationError):
        if len(batch) == 1:
            raise
        midpoint = len(batch) // 2
        return _extract_function_batch(
            structured_model,
            parsed_file,
            batch[:midpoint],
        ) + _extract_function_batch(
            structured_model,
            parsed_file,
            batch[midpoint:],
        )

    if _batch_matches_inventory(response, batch):
        return response.functions

    if len(batch) == 1:
        expected_id = batch[0].function_id
        actual_ids = [item.function_id for item in response.functions]
        raise ValueError(
            "Extraction response does not match parsed function inventory for "
            f"{parsed_file.relative_path}: expected=[{expected_id!r}], "
            f"received={actual_ids!r}"
        )

    midpoint = len(batch) // 2
    return _extract_function_batch(
        structured_model,
        parsed_file,
        batch[:midpoint],
    ) + _extract_function_batch(
        structured_model,
        parsed_file,
        batch[midpoint:],
    )


def _batch_matches_inventory(
    response: FileFactsExtractionResponse,
    functions: Sequence[ParsedRustFunction],
) -> bool:
    expected_ids = [function.function_id for function in functions]
    received_ids = [item.function_id for item in response.functions]
    return received_ids == expected_ids


def _iter_function_batches(
    functions: Sequence[ParsedRustFunction],
    *,
    batch_size: int,
) -> list[Sequence[ParsedRustFunction]]:
    return [
        functions[start : start + batch_size]
        for start in range(0, len(functions), batch_size)
    ]


def _format_function_inventory(functions: Sequence[ParsedRustFunction]) -> str:
    lines: list[str] = []

    for function in functions:
        impl_part = (
            f", impl_target={function.impl_target}"
            if function.impl_target is not None
            else ""
        )
        lines.append(
            "- "
            f"id={function.function_id}, "
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
