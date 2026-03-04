from __future__ import annotations

from pathlib import Path

from scout_agent.domain.facts import (
    FactsDocument,
    FunctionSummary,
    file_path_from_function_key,
    load_facts_document,
    write_facts_document,
)


def test_write_facts_document_omits_absent_summary_fields(tmp_path: Path) -> None:
    facts_path = tmp_path / "FACTS.yaml"
    write_facts_document(
        facts_path,
        FactsDocument(
            generated_at_utc="2026-03-02T12:00:00Z",
            project_root=str(tmp_path),
            model="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            scope_fingerprint="abc123",
            functions={
                "src/validator.rs::require_nonnegative": FunctionSummary(
                    authorization="None",
                    vector_params="None.",
                    time_dependent="",
                )
            },
        ),
    )

    written = facts_path.read_text(encoding="utf-8")
    assert "src/validator.rs::require_nonnegative:" in written
    assert "file:" not in written
    assert "authorization:" not in written
    assert "vector_params:" not in written
    assert "time_dependent:" not in written
    assert "sentinel_values:" not in written

    loaded = load_facts_document(facts_path)
    assert loaded.functions["src/validator.rs::require_nonnegative"] == FunctionSummary()


def test_file_path_from_function_key_extracts_path_prefix() -> None:
    assert (
        file_path_from_function_key("src/validator.rs::Foo::require_nonnegative")
        == "src/validator.rs"
    )
