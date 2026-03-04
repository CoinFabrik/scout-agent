from __future__ import annotations

import pytest
from langchain_core.exceptions import OutputParserException

from scout_agent.domain.facts import FunctionSummary
from scout_agent.runtime.extract.facts_extractor import (
    ExtractedFunctionSummary,
    FileFactsExtractionResponse,
    build_function_inventory,
    extract_file_facts_with_llm,
    merge_extracted_file_facts,
)
from scout_agent.runtime.extract.models import RetryableExtractionError
from scout_agent.runtime.source.rust_parser import parse_rust_source
from scout_agent.runtime.source.source_filter import sanitize_rust_source_for_analysis


def _parse_single_function_file(
    *,
    relative_path: str = "src/validator.rs",
):
    return parse_rust_source(
        b"pub fn require_nonnegative() {}\n",
        relative_path=relative_path,
    )


def _valid_response(function_key: str) -> FileFactsExtractionResponse:
    return FileFactsExtractionResponse(
        functions=[
            ExtractedFunctionSummary(
                function_key=function_key,
                summary=FunctionSummary(),
            )
        ]
    )


def test_merge_extracted_file_facts_rejects_inventory_mismatch() -> None:
    parsed = parse_rust_source(
        b"""pub fn vote() {}

fn helper() {}
""",
        relative_path="contracts/gateway.rs",
    )

    bad_response = FileFactsExtractionResponse(
        functions=[
            ExtractedFunctionSummary(
                function_key="contracts/gateway.rs::vote",
                summary=FunctionSummary(
                    authorization="None.",
                    vector_params="None.",
                    time_dependent="None.",
                    sentinel_values="None.",
                ),
            )
        ]
    )

    with pytest.raises(RetryableExtractionError, match="missing="):
        merge_extracted_file_facts(
            parsed,
            extracted=bad_response,
        )


def test_sanitized_inventory_excludes_inline_tests() -> None:
    raw_source = """pub fn vote() {}

#[cfg(test)]
mod tests {
    #[test]
    fn unit_test() {}
}

fn helper() {}
"""

    sanitized = sanitize_rust_source_for_analysis(
        raw_source,
        relative_path="contracts/gateway.rs",
    )
    parsed = parse_rust_source(
        sanitized.encode("utf-8"),
        relative_path="contracts/gateway.rs",
    )

    assert [fn.name for fn in parsed.functions] == ["vote", "helper"]


def test_build_function_inventory_rejects_canonical_collisions() -> None:
    parsed = parse_rust_source(
        b"""impl Foo { pub fn vote() {} }
impl Foo { pub fn vote() {} }
""",
        relative_path="contracts/gateway.rs",
    )

    with pytest.raises(ValueError, match="Canonical function key collision"):
        build_function_inventory(parsed)


def test_extract_file_facts_with_llm_returns_flat_function_summary_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parse_rust_source(
        b"""pub fn vote() {}
impl Foo { pub fn set_admin() {} }
""",
        relative_path="contracts/gateway.rs",
    )

    class FakeStructuredModel:
        def invoke(self, _messages):
            return FileFactsExtractionResponse(
                functions=[
                    ExtractedFunctionSummary(
                        function_key="contracts/gateway.rs::vote",
                        summary=FunctionSummary(
                            authorization="Requires caller authorization.",
                            vector_params="None.",
                            time_dependent="None.",
                            sentinel_values="None.",
                        ),
                    ),
                    ExtractedFunctionSummary(
                        function_key="contracts/gateway.rs::Foo::set_admin",
                        summary=FunctionSummary(
                            authorization="Requires current admin authorization.",
                            vector_params="None.",
                            time_dependent="None.",
                            sentinel_values="Should reject zero address.",
                        ),
                    ),
                ]
            )

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return FakeStructuredModel()

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    summaries = extract_file_facts_with_llm(
        parsed,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )

    assert list(summaries) == [
        "contracts/gateway.rs::vote",
        "contracts/gateway.rs::Foo::set_admin",
    ]
    assert summaries["contracts/gateway.rs::vote"].authorization.startswith(
        "Requires caller"
    )


def test_extract_file_facts_with_llm_retries_inventory_mismatch_and_adds_retry_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = _parse_single_function_file()
    prompts: list[str] = []

    class FakeStructuredModel:
        def __init__(self) -> None:
            self.invocations = 0

        def invoke(self, messages):
            self.invocations += 1
            prompts.append(messages[-1].content)
            if self.invocations == 1:
                return _valid_response("src/ validator.rs::require_nonnegative")
            return _valid_response("src/validator.rs::require_nonnegative")

    structured_model = FakeStructuredModel()

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return structured_model

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    summaries = extract_file_facts_with_llm(
        parsed,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )

    assert summaries == {"src/validator.rs::require_nonnegative": FunctionSummary()}
    assert structured_model.invocations == 2
    assert "Retry correction:" not in prompts[0]
    assert "Retry correction:" in prompts[1]
    assert "reuse the exact function_key strings" in prompts[1]


def test_extract_file_facts_with_llm_retries_output_parser_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = _parse_single_function_file()

    class FakeStructuredModel:
        def __init__(self) -> None:
            self.invocations = 0

        def invoke(self, _messages):
            self.invocations += 1
            if self.invocations == 1:
                raise OutputParserException("malformed JSON")
            return _valid_response("src/validator.rs::require_nonnegative")

    structured_model = FakeStructuredModel()

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return structured_model

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    summaries = extract_file_facts_with_llm(
        parsed,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )

    assert summaries == {"src/validator.rs::require_nonnegative": FunctionSummary()}
    assert structured_model.invocations == 2


def test_extract_file_facts_with_llm_retries_duplicate_function_key_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = _parse_single_function_file()

    class FakeStructuredModel:
        def __init__(self) -> None:
            self.invocations = 0

        def invoke(self, _messages):
            self.invocations += 1
            if self.invocations == 1:
                return {
                    "functions": [
                        {
                            "function_key": "src/validator.rs::require_nonnegative",
                            "summary": {},
                        },
                        {
                            "function_key": "src/validator.rs::require_nonnegative",
                            "summary": {},
                        },
                    ]
                }
            return _valid_response("src/validator.rs::require_nonnegative")

    structured_model = FakeStructuredModel()

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return structured_model

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    summaries = extract_file_facts_with_llm(
        parsed,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )

    assert summaries == {"src/validator.rs::require_nonnegative": FunctionSummary()}
    assert structured_model.invocations == 2


def test_extract_file_facts_with_llm_raises_after_retry_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = _parse_single_function_file()

    class FakeStructuredModel:
        def __init__(self) -> None:
            self.invocations = 0

        def invoke(self, _messages):
            self.invocations += 1
            return _valid_response("src/ validator.rs::require_nonnegative")

    structured_model = FakeStructuredModel()

    class FakeModel:
        def with_structured_output(self, *_args, **_kwargs):
            return structured_model

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(),
    )

    with pytest.raises(RetryableExtractionError, match="unexpected="):
        extract_file_facts_with_llm(
            parsed,
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
        )

    assert structured_model.invocations == 3


def test_extract_file_facts_with_llm_does_not_retry_deterministic_inventory_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = parse_rust_source(
        b"""impl Foo { pub fn vote() {} }
impl Foo { pub fn vote() {} }
""",
        relative_path="contracts/gateway.rs",
    )
    build_chat_calls = 0

    def fake_build_chat_model(*_args, **_kwargs):
        nonlocal build_chat_calls
        build_chat_calls += 1
        raise AssertionError("build_chat_model should not be called")

    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        fake_build_chat_model,
    )

    with pytest.raises(ValueError, match="Canonical function key collision"):
        extract_file_facts_with_llm(
            parsed,
            model_name="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
        )

    assert build_chat_calls == 0


def test_merge_extracted_file_facts_treats_none_markers_as_absent() -> None:
    parsed = _parse_single_function_file()

    response = FileFactsExtractionResponse(
        functions=[
            ExtractedFunctionSummary(
                function_key="src/validator.rs::require_nonnegative",
                summary=FunctionSummary(
                    authorization="None",
                    vector_params="None.",
                    time_dependent="",
                    sentinel_values=None,
                ),
            )
        ]
    )

    summaries = merge_extracted_file_facts(parsed, extracted=response)

    assert summaries["src/validator.rs::require_nonnegative"] == FunctionSummary()
