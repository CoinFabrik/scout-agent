from __future__ import annotations

from pathlib import Path

from scout_agent.domain.audit import ExpertResult
from scout_agent.runtime.audit import experts as expert_runtime
from scout_agent.runtime.audit.experts import (
    BASE_EXPERT_PROMPT,
    SUBAGENT_MANIFEST,
    build_expert_subagents,
)


def _capture_agent_builds(monkeypatch) -> tuple[object, list[dict[str, object]]]:
    model = object()
    captured_calls: list[dict[str, object]] = []

    def fake_create_agent(**kwargs):
        captured_calls.append(kwargs)
        return {"agent_name": kwargs["name"]}

    monkeypatch.setattr(expert_runtime, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        expert_runtime,
        "build_chat_model",
        lambda *_args, **_kwargs: model,
    )

    return model, captured_calls


def _build_tool_for_test(tmp_path: Path, monkeypatch):
    model, captured_calls = _capture_agent_builds(monkeypatch)
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "gateway.rs").write_text(
        "pub fn alpha() {}\n"
        "pub fn beta() {}\n"
        "pub fn gamma() {}\n"
        "pub fn delta() {}\n",
        encoding="utf-8",
    )

    build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
    )

    return model, captured_calls[0]["tools"][0]


def test_build_expert_subagents_returns_compiled_subagents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model, captured_calls = _capture_agent_builds(monkeypatch)

    subagents = build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
    )

    assert len(subagents) == len(SUBAGENT_MANIFEST)
    assert len(captured_calls) == len(SUBAGENT_MANIFEST)

    for spec, subagent, captured in zip(
        SUBAGENT_MANIFEST,
        subagents,
        captured_calls,
        strict=True,
    ):
        assert subagent["name"] == spec.name
        assert subagent["description"] == spec.description
        assert subagent["runnable"] == {"agent_name": spec.name}
        assert captured["model"] is model
        assert (
            captured["system_prompt"] == f"{BASE_EXPERT_PROMPT}\n\n{spec.system_prompt}"
        )
        assert captured["response_format"] is ExpertResult
        assert captured["name"] == spec.name
        assert len(captured["tools"]) == 1
        assert callable(captured["tools"][0])
        assert captured["tools"][0].__name__ == "read_code_chunk"


def test_read_code_chunk_tool_reads_numbered_lines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _model, tool = _build_tool_for_test(tmp_path, monkeypatch)

    result = tool(file="contracts/gateway.rs", start_line=2, max_lines=2)

    assert "   2: pub fn beta() {}" in result
    assert "   3: pub fn gamma() {}" in result


def test_read_code_chunk_tool_returns_error_strings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _model, tool = _build_tool_for_test(tmp_path, monkeypatch)

    assert tool(file="contracts/other.rs") == (
        "Error: File is outside FACTS scope: contracts/other.rs"
    )
    assert tool(file="contracts/gateway.rs", start_line=0) == (
        "Error: start_line must be >= 1; got 0"
    )
    assert tool(file="contracts/gateway.rs", max_lines=101) == (
        "Error: max_lines must be between 1 and 100; got 101"
    )


def test_build_expert_subagents_logs_spawn_and_tool_use(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class FakeRunnable:
        def invoke(self, payload):
            return payload

    def fake_create_agent(**kwargs):
        captured_calls.append(kwargs)
        return FakeRunnable()

    monkeypatch.setattr(expert_runtime, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        expert_runtime,
        "build_chat_model",
        lambda *_args, **_kwargs: object(),
    )

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "gateway.rs").write_text(
        "pub fn alpha() {}\n"
        "pub fn beta() {}\n"
        "pub fn gamma() {}\n"
        "pub fn delta() {}\n",
        encoding="utf-8",
    )

    subagents = build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
    )

    subagents[0]["runnable"].invoke({"messages": []})

    first_tool = captured_calls[0]["tools"][0]
    assert callable(first_tool)

    result = first_tool(file="contracts/gateway.rs", start_line=1, max_lines=1)

    assert "   1: pub fn alpha() {}" in result


def test_build_expert_subagents_appends_extra_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _model, captured_calls = _capture_agent_builds(monkeypatch)

    build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
        extra_prompt="Always track state invariants before and after mutation.",
    )

    for captured in captured_calls:
        assert "Additional audit instructions:" in captured["system_prompt"]
        assert (
            "Always track state invariants before and after mutation."
            in captured["system_prompt"]
        )


def test_read_code_chunk_denied_log_uses_facts_scope_current(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_calls: list[dict[str, object]] = []

    class FakeRunnable:
        def invoke(self, payload):
            return payload

    def fake_create_agent(**kwargs):
        captured_calls.append(kwargs)
        return FakeRunnable()

    monkeypatch.setattr(expert_runtime, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        expert_runtime,
        "build_chat_model",
        lambda *_args, **_kwargs: object(),
    )

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "gateway.rs").write_text("pub fn alpha() {}\n", encoding="utf-8")

    build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
    )
    first_tool = captured_calls[0]["tools"][0]
    assert callable(first_tool)

    result = first_tool(file="contracts/other.rs")

    assert result == "Error: File is outside FACTS scope: contracts/other.rs"
