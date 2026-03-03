import re

import pytest
from langchain_core.exceptions import OutputParserException

from scout_agent.domain.facts import (
    AuthorizationFact,
    FunctionFactBundle,
    SentinelValuesFact,
    TimeDependentStateFact,
    VectorParametersFact,
)
from scout_agent.runtime.extract.facts_extractor import (
    ExtractedFunctionFacts,
    FileFactsExtractionResponse,
    extract_file_facts_with_llm,
    merge_extracted_file_facts,
)
from scout_agent.runtime.source.rust_parser import parse_rust_source
from scout_agent.runtime.source.source_filter import sanitize_rust_source_for_analysis


def test_facts_extractor_rejects_inventory_mismatch() -> None:
    parsed = parse_rust_source(
        b"""pub fn vote(e: Env, ids: Vec<u32>) {
    require_auth();
}

fn helper() {}
""",
        relative_path="contracts/gateway.rs",
    )

    bad_response = FileFactsExtractionResponse(
        functions=[
            ExtractedFunctionFacts(
                function_id="contracts/gateway.rs::vote#L1",
                facts=FunctionFactBundle(
                    authorization=AuthorizationFact(
                        status="present",
                        reasoning="x",
                        evidence=["contracts/gateway.rs:2 require_auth()"],
                    ),
                    vector_parameters=VectorParametersFact(
                        status="present",
                        reasoning="x",
                        parameters=["ids: Vec<u32>"],
                    ),
                    time_dependent_state=TimeDependentStateFact(
                        status="absent",
                        reasoning="x",
                        evidence=[],
                    ),
                    sentinel_values=SentinelValuesFact(
                        status="absent",
                        reasoning="x",
                        values=[],
                    ),
                ),
            )
        ]
    )

    with pytest.raises(ValueError, match="missing="):
        merge_extracted_file_facts(
            parsed,
            content_sha256="deadbeef",
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


def test_extract_file_facts_with_llm_splits_batches_after_parser_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "\n\n".join(f"pub fn f{i:02}() {{}}" for i in range(13))
    parsed = parse_rust_source(
        source.encode("utf-8"),
        relative_path="contracts/gateway.rs",
    )

    def make_bundle() -> FunctionFactBundle:
        return FunctionFactBundle(
            authorization=AuthorizationFact(
                status="absent",
                reasoning="x",
                evidence=[],
            ),
            vector_parameters=VectorParametersFact(
                status="absent",
                reasoning="x",
                parameters=[],
            ),
            time_dependent_state=TimeDependentStateFact(
                status="absent",
                reasoning="x",
                evidence=[],
            ),
            sentinel_values=SentinelValuesFact(
                status="absent",
                reasoning="x",
                values=[],
            ),
        )

    class FakeStructuredModel:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def invoke(self, messages):
            prompt = messages[-1].content
            ids = re.findall(r"id=([^,]+),", prompt)
            self.batch_sizes.append(len(ids))
            if len(ids) > 1:
                raise OutputParserException("malformed JSON")

            return FileFactsExtractionResponse(
                functions=[
                    ExtractedFunctionFacts(
                        function_id=ids[0],
                        facts=make_bundle(),
                    )
                ]
            )

    class FakeModel:
        def __init__(self, structured_model: FakeStructuredModel) -> None:
            self._structured_model = structured_model

        def with_structured_output(self, *_args, **_kwargs):
            return self._structured_model

    structured_model = FakeStructuredModel()
    monkeypatch.setattr(
        "scout_agent.runtime.extract.facts_extractor.build_chat_model",
        lambda *_args, **_kwargs: FakeModel(structured_model),
    )

    file_facts = extract_file_facts_with_llm(
        parsed,
        content_sha256="deadbeef",
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
    )

    assert [function.function_id for function in file_facts.functions] == [
        function.function_id for function in parsed.functions
    ]
    assert structured_model.batch_sizes[0] == 12
    assert 1 in structured_model.batch_sizes
