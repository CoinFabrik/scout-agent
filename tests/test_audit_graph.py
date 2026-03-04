from __future__ import annotations

from pathlib import Path

import pytest

from scout_agent.domain.audit import AuditState, FileAuditResponse, Finding
from scout_agent.domain.facts import FactsDocument, FunctionSummary
from scout_agent.runtime.audit.graph import (
    AuditContext,
    FileScopedAuditBackend,
    build_parent_audit_prompt,
    run_audit,
)


def _facts_document(project_root: Path) -> FactsDocument:
    return FactsDocument(
        generated_at_utc="2026-03-02T12:00:00Z",
        project_root=str(project_root),
        model="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        scope_fingerprint="abc123",
        functions={
            "contracts/gateway.rs::vote": FunctionSummary(
                authorization="Requires caller authorization.",
                vector_params="Accepts a vote vector.",
                time_dependent="None.",
                sentinel_values="None.",
            )
        },
    )


def _initial_state(project_root: Path) -> AuditState:
    return {
        "project_root": project_root,
        "facts_path": project_root / "FACTS.yaml",
        "files_to_review": ["contracts/gateway.rs", "contracts/plain.rs"],
        "files_reviewed": [],
        "verified_findings": [],
        "finding_keys": [],
    }


class FakeAuditReporter:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def started(self, **kwargs) -> None:
        self.events.append(("started", kwargs["total_files"]))

    def file_started(self, **kwargs) -> None:
        self.events.append(("file_started", kwargs["index"], kwargs["current_file"]))

    def finding_verified(self, **kwargs) -> None:
        self.events.append(
            (
                "finding_verified",
                kwargs["total_verified_findings"],
                kwargs["finding"].pattern,
            )
        )

    def file_completed(self, **kwargs) -> None:
        self.events.append(("file_completed", kwargs["reviewed"], kwargs["current_file"]))

    def expert_spawned(self, **kwargs) -> None:
        self.events.append(("expert_spawned", kwargs["expert_name"]))

    def tool_used(self, **kwargs) -> None:
        self.events.append(
            (
                "tool_used",
                kwargs.get("expert_name"),
                kwargs["tool_name"],
                kwargs["target"],
                kwargs.get("line_start"),
                kwargs.get("line_end"),
                kwargs.get("offset"),
                kwargs.get("limit"),
            )
        )

    def tool_denied(self, **kwargs) -> None:
        self.events.append(
            (
                "tool_denied",
                kwargs.get("expert_name"),
                kwargs["tool_name"],
                kwargs["target"],
                kwargs["current_file"],
                kwargs["reason"],
            )
        )

    def close(self) -> None:
        self.events.append(("close",))


def _patch_filesystem_backend(monkeypatch) -> list[tuple[str, str]]:
    backend_calls: list[tuple[str, str]] = []
    base_backend = FileScopedAuditBackend.__bases__[0]

    def fake_init(self, *, root_dir: str, virtual_mode: bool = False, **_kwargs) -> None:
        self.root_dir = Path(root_dir)
        self.virtual_mode = virtual_mode

    def fake_ls_info(self, path: str):
        backend_calls.append(("ls_info", path))
        return [{"path": path}]

    def fake_read(self, file_path: str, offset: int = 0, limit: int = 2000):
        backend_calls.append(("read", file_path))
        resolved = self.root_dir / file_path.lstrip("/")
        if not resolved.exists():
            return f"Error: File '{file_path}' not found"
        return resolved.read_text(encoding="utf-8")

    def fake_glob_info(self, pattern: str, path: str = "/"):
        backend_calls.append(("glob_info", path))
        return [{"path": path, "pattern": pattern}]

    def fake_grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ):
        backend_calls.append(("grep_raw", path or ""))
        return [{"path": path, "pattern": pattern, "glob": glob}]

    monkeypatch.setattr(base_backend, "__init__", fake_init)
    monkeypatch.setattr(base_backend, "ls_info", fake_ls_info)
    monkeypatch.setattr(base_backend, "read", fake_read)
    monkeypatch.setattr(base_backend, "glob_info", fake_glob_info)
    monkeypatch.setattr(base_backend, "grep_raw", fake_grep_raw)
    return backend_calls


def test_run_audit_reviews_files_sequentially_and_writes_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_filesystem_backend(monkeypatch)
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "gateway.rs").write_text("pub fn vote() {}\n", encoding="utf-8")
    (contracts / "plain.rs").write_text("pub fn helper() {}\n", encoding="utf-8")

    facts_document = _facts_document(tmp_path)
    initial_state = _initial_state(tmp_path)
    reporter = FakeAuditReporter()
    runtime = AuditContext(
        project_root=tmp_path,
        report_path=tmp_path / "REPORT.md",
        facts_document=facts_document,
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        initial_state=initial_state,
        reporter=reporter,
    )

    responses = [
        FileAuditResponse(
            findings=[
                Finding(
                    pattern="Duplicate vector elements",
                    severity="HIGH",
                    location="contracts/gateway.rs:22",
                    description="Vector elements are aggregated without uniqueness checks.",
                    evidence="contracts/gateway.rs:22-31",
                )
            ]
        ),
        FileAuditResponse(findings=[]),
    ]
    seen_backends: list[FileScopedAuditBackend] = []

    class FakeAgent:
        def __init__(self, response: FileAuditResponse) -> None:
            self._response = response

        def invoke(self, payload):
            assert payload["messages"][0]["role"] == "user"
            return {"structured_response": self._response}

    def fake_create_deep_agent(*, backend, subagents, response_format, **_kwargs):
        assert response_format is FileAuditResponse
        assert len(subagents) == 4
        seen_backends.append(backend)
        return FakeAgent(responses[len(seen_backends) - 1])

    monkeypatch.setattr(
        "scout_agent.runtime.audit.graph.create_deep_agent",
        fake_create_deep_agent,
    )
    monkeypatch.setattr(
        "scout_agent.runtime.audit.graph.build_chat_model",
        lambda *_args, **_kwargs: object(),
    )

    final_state = run_audit(runtime=runtime)

    assert final_state["files_to_review"] == []
    assert final_state["files_reviewed"] == [
        "contracts/gateway.rs",
        "contracts/plain.rs",
    ]
    assert len(final_state["verified_findings"]) == 1
    assert (tmp_path / "REPORT.md").exists()
    assert reporter.events == [
        ("started", 2),
        ("file_started", 1, "contracts/gateway.rs"),
        ("finding_verified", 1, "Duplicate vector elements"),
        ("file_completed", 1, "contracts/gateway.rs"),
        ("file_started", 2, "contracts/plain.rs"),
        ("file_completed", 2, "contracts/plain.rs"),
    ]
    assert not (tmp_path / ".scout-ai").exists()


def test_file_scoped_backend_normalizes_parent_agent_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend_calls = _patch_filesystem_backend(monkeypatch)
    reporter = FakeAuditReporter()
    auctions = tmp_path / "src" / "auctions"
    auctions.mkdir(parents=True)
    (auctions / "auction.rs").write_text(
        "pub fn settle() {}\n#[cfg(test)]\nmod tests { #[test] fn unit() {} }\n",
        encoding="utf-8",
    )
    ((tmp_path / "src") / "other.rs").write_text("pub fn helper() {}\n", encoding="utf-8")

    backend = FileScopedAuditBackend(
        root_dir=tmp_path,
        current_file="src/auctions/auction.rs",
        reporter=reporter,
    )

    read_result = backend.read("/src/auctions/auction.rs", offset=0, limit=50)
    compatibility_result = backend.read("src/auctions/auction.rs", offset=0, limit=50)
    grep_result = backend.grep_raw("settle", path="src/./auctions/auction.rs")
    grep_default_result = backend.grep_raw("settle", path=None)
    glob_result = backend.glob_info("*.rs", path="/src/auctions/auction.rs")
    ls_result = backend.ls_info(" src/auctions/auction.rs ")

    assert "pub fn settle()" in read_result
    assert "pub fn settle()" in compatibility_result
    assert grep_result == [
        {
            "path": "/src/auctions/auction.rs",
            "pattern": "settle",
            "glob": None,
        }
    ]
    assert grep_default_result == [
        {
            "path": "/src/auctions/auction.rs",
            "pattern": "settle",
            "glob": None,
        }
    ]
    assert glob_result == [{"path": "/src/auctions/auction.rs", "pattern": "*.rs"}]
    assert ls_result == [{"path": "/src/auctions/auction.rs"}]
    assert backend_calls == [
        ("read", "/src/auctions/auction.rs"),
        ("read", "/src/auctions/auction.rs"),
        ("grep_raw", "/src/auctions/auction.rs"),
        ("grep_raw", "/src/auctions/auction.rs"),
        ("glob_info", "/src/auctions/auction.rs"),
        ("ls_info", "/src/auctions/auction.rs"),
    ]
    assert [event for event in reporter.events if event[0] == "tool_used"] == [
        ("tool_used", None, "read", "/src/auctions/auction.rs", 1, 3, 0, 50),
        ("tool_used", None, "read", "/src/auctions/auction.rs", 1, 3, 0, 50),
        ("tool_used", None, "grep_raw", "/src/auctions/auction.rs", None, None, None, None),
        ("tool_used", None, "grep_raw", "/src/auctions/auction.rs", None, None, None, None),
        ("tool_used", None, "glob_info", "/src/auctions/auction.rs", None, None, None, None),
        ("tool_used", None, "ls_info", "/src/auctions/auction.rs", None, None, None, None),
    ]


def test_file_scoped_backend_rejects_out_of_scope_and_invalid_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_filesystem_backend(monkeypatch)
    reporter = FakeAuditReporter()
    auctions = tmp_path / "src" / "auctions"
    auctions.mkdir(parents=True)
    (auctions / "auction.rs").write_text("pub fn settle() {}\n", encoding="utf-8")
    ((tmp_path / "src") / "other.rs").write_text("pub fn helper() {}\n", encoding="utf-8")

    backend = FileScopedAuditBackend(
        root_dir=tmp_path,
        current_file="src/auctions/auction.rs",
        reporter=reporter,
    )

    with pytest.raises(ValueError) as read_out_of_scope:
        backend.read("/src/other.rs")
    assert str(read_out_of_scope.value) == "Read access denied for '/src/other.rs'."
    assert reporter.events == [
        (
            "tool_denied",
            None,
            "read",
            "/src/other.rs",
            "/src/auctions/auction.rs",
            "outside-current-file-scope",
        )
    ]

    with pytest.raises(ValueError) as read_empty:
        backend.read("")
    assert str(read_empty.value) == "Read access denied for ''."
    assert reporter.events[-1] == (
        "tool_denied",
        None,
        "read",
        "",
        "/src/auctions/auction.rs",
        "File path must be non-empty.",
    )

    with pytest.raises(ValueError) as read_traversal:
        backend.read("/../src/auctions/auction.rs")
    assert (
        str(read_traversal.value)
        == "Read access denied for '/../src/auctions/auction.rs'."
    )
    assert reporter.events[-1] == (
        "tool_denied",
        None,
        "read",
        "/../src/auctions/auction.rs",
        "/src/auctions/auction.rs",
        "Path traversal is not allowed: /../src/auctions/auction.rs",
    )

    with pytest.raises(ValueError) as glob_root:
        backend.glob_info("*.rs", path="/")
    assert str(glob_root.value) == "Glob access denied for '/'."
    assert reporter.events[-1] == (
        "tool_denied",
        None,
        "glob_info",
        "/",
        "/src/auctions/auction.rs",
        "File path must be non-empty.",
    )

    with pytest.raises(ValueError) as ls_out_of_scope:
        backend.ls_info("/src/other.rs")
    assert str(ls_out_of_scope.value) == "List access denied for '/src/other.rs'."
    assert reporter.events[-1] == (
        "tool_denied",
        None,
        "ls_info",
        "/src/other.rs",
        "/src/auctions/auction.rs",
        "outside-current-file-scope",
    )


def test_file_scoped_backend_caps_supervisor_read_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    base_backend = FileScopedAuditBackend.__bases__[0]
    observed: dict[str, int] = {}

    def fake_init(self, *, root_dir: str, virtual_mode: bool = False, **_kwargs) -> None:
        self.root_dir = Path(root_dir)
        self.virtual_mode = virtual_mode

    def fake_read(self, file_path: str, offset: int = 0, limit: int = 2000):
        observed["limit"] = limit
        return "ok"

    monkeypatch.setattr(base_backend, "__init__", fake_init)
    monkeypatch.setattr(base_backend, "read", fake_read)

    source_dir = tmp_path / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "a.rs").write_text("line\n" * 800, encoding="utf-8")

    backend = FileScopedAuditBackend(
        root_dir=tmp_path,
        current_file="src/a.rs",
    )

    result = backend.read("/src/a.rs", offset=0, limit=5000)

    assert result == "ok"
    assert observed["limit"] == 500


def test_build_parent_audit_prompt_handles_missing_current_file_facts() -> None:
    prompt = build_parent_audit_prompt(
        current_file="contracts/plain.rs",
        current_file_facts={},
        all_facts={
            "contracts/gateway.rs::vote": FunctionSummary(
                authorization="Requires caller authorization.",
                vector_params="Accepts a vote vector.",
                time_dependent="None.",
                sentinel_values="None.",
            )
        },
    )

    assert "No extracted function facts for this file." in prompt
    assert "contracts/gateway.rs::vote" in prompt


def test_build_parent_audit_prompt_omits_absent_fact_categories() -> None:
    prompt = build_parent_audit_prompt(
        current_file="contracts/validator.rs",
        current_file_facts={
            "contracts/validator.rs::require_nonnegative": FunctionSummary(
                authorization="None",
                vector_params="None.",
                time_dependent="",
            )
        },
        all_facts={},
    )

    assert "contracts/validator.rs::require_nonnegative" in prompt
    assert "observed with no extracted categories." in prompt
    assert "authorization=" not in prompt


def test_build_parent_audit_prompt_appends_extra_prompt() -> None:
    prompt = build_parent_audit_prompt(
        current_file="contracts/validator.rs",
        current_file_facts={},
        all_facts={},
        extra_prompt="Never assume cross-contract calls are trusted.",
    )

    assert "Additional audit instructions:" in prompt
    assert "Never assume cross-contract calls are trusted." in prompt
