from __future__ import annotations

from pathlib import Path

from scout_agent.domain.audit import ExpertResult, ExpertTypeEnum
from scout_agent.runtime.audit import experts as expert_runtime
from scout_agent.runtime.audit.experts import build_expert_subagents


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
        "alpha\nbeta\ngamma\ndelta\n",
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

    assert len(subagents) == len(ExpertTypeEnum)
    assert len(captured_calls) == len(ExpertTypeEnum)

    for expert_type, subagent, captured in zip(
        ExpertTypeEnum,
        subagents,
        captured_calls,
        strict=True,
    ):
        assert subagent["name"] == expert_type.value
        assert subagent["description"] == expert_runtime._description_for_expert(
            expert_type
        )
        assert subagent["runnable"] == {"agent_name": expert_type.value}
        assert captured["model"] is model
        assert captured["system_prompt"] == expert_runtime._system_prompt_for_expert(
            expert_type
        )
        assert captured["response_format"] is ExpertResult
        assert captured["name"] == expert_type.value
        assert len(captured["tools"]) == 1
        assert callable(captured["tools"][0])
        assert captured["tools"][0].__name__ == "read_code_chunk"


def test_read_code_chunk_tool_reads_numbered_lines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _model, tool = _build_tool_for_test(tmp_path, monkeypatch)

    result = tool(file="contracts/gateway.rs", start_line=2, max_lines=2)

    assert "   2: beta" in result
    assert "   3: gamma" in result


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
    events: list[tuple[str, str, str | None]] = []
    captured_calls: list[dict[str, object]] = []

    class FakeReporter:
        def expert_spawned(self, **kwargs) -> None:
            events.append(("expert_spawned", kwargs["expert_name"], None))

        def tool_used(self, **kwargs) -> None:
            events.append(
                (
                    "tool_used",
                    kwargs.get("expert_name"),
                    kwargs["tool_name"],
                    kwargs["target"],
                    kwargs.get("line_start"),
                    kwargs.get("line_end"),
                )
            )

        def tool_denied(self, **kwargs) -> None:
            events.append(
                (
                    "tool_denied",
                    kwargs.get("expert_name"),
                    kwargs["tool_name"],
                    kwargs["target"],
                    kwargs["reason"],
                )
            )

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
        "alpha\nbeta\ngamma\ndelta\n",
        encoding="utf-8",
    )

    subagents = build_expert_subagents(
        model_name="anthropic:claude-sonnet-4-5",
        llm_mode="consistent",
        project_root=tmp_path,
        allowed_paths=["contracts/gateway.rs"],
        reporter=FakeReporter(),
    )

    subagents[0]["runnable"].invoke({"messages": []})
    first_tool = captured_calls[0]["tools"][0]
    assert callable(first_tool)

    result = first_tool(file="contracts/gateway.rs", start_line=1, max_lines=1)

    assert "   1: alpha" in result
    assert events == [
        ("expert_spawned", "execution_path_consistency", None),
        (
            "tool_used",
            "execution_path_consistency",
            "read_code_chunk",
            "contracts/gateway.rs",
            1,
            1,
        ),
    ]


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
