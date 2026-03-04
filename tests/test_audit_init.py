from pathlib import Path

import pytest

from scout_agent.domain.facts import FactsDocument, write_facts_document
from scout_agent.runtime.audit.run import initialize_audit


def test_audit_init_rejects_test_only_projects(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "integration.rs").write_text(
        "pub fn ignored() {}\n",
        encoding="utf-8",
    )

    facts_path = tmp_path / "FACTS.yaml"
    write_facts_document(
        facts_path,
        FactsDocument(
            generated_at_utc="2026-03-02T12:00:00Z",
            project_root=str(tmp_path.resolve()),
            model="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            scope_fingerprint="abc123",
            functions={},
        ),
    )

    with pytest.raises(
        ValueError,
        match="No in-scope production Rust source files were discovered",
    ):
        initialize_audit(
            project_root=tmp_path,
            facts_path=facts_path,
        )
