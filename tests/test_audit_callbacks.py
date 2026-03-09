from __future__ import annotations

from uuid import uuid4

from scout_agent.runtime.audit.audit_callbacks import RuntimeProgressHandler


class FakeReporter:
    def __init__(self) -> None:
        self.experts: list[str] = []
        self.used: list[dict] = []
        self.denied: list[dict] = []

    def expert_spawned(self, **kwargs) -> None:
        self.experts.append(kwargs["expert_name"])

    def tool_used(self, **kwargs) -> None:
        self.used.append(kwargs)

    def tool_denied(self, **kwargs) -> None:
        self.denied.append(kwargs)


class FakeDumpWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def expert_started(self, **kwargs) -> None:
        self.calls.append(("expert_started", kwargs))

    def expert_result(self, **kwargs) -> None:
        self.calls.append(("expert_result", kwargs))

    def expert_completed(self, **kwargs) -> None:
        self.calls.append(("expert_completed", kwargs))

    def supervisor_tool_used(self, **kwargs) -> None:
        self.calls.append(("supervisor_tool_used", kwargs))

    def expert_tool_used(self, **kwargs) -> None:
        self.calls.append(("expert_tool_used", kwargs))

    def supervisor_tool_denied(self, **kwargs) -> None:
        self.calls.append(("supervisor_tool_denied", kwargs))

    def expert_tool_denied(self, **kwargs) -> None:
        self.calls.append(("expert_tool_denied", kwargs))


def _handler(
    *,
    reporter: FakeReporter,
    dump_writer: FakeDumpWriter | None = None,
) -> RuntimeProgressHandler:
    return RuntimeProgressHandler(
        reporter=reporter,
        expert_names={"execution_path_consistency", "collection_validation", "time_state", "sentinel_logic"},
        current_file="contracts/gateway.rs",
        dump_writer=dump_writer,
    )


def test_runtime_progress_handler_reports_expert_chain_start_by_name() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    handler.on_chain_start(
        {"name": "execution_path_consistency"},
        {},
        run_id=uuid4(),
    )

    assert reporter.experts == ["execution_path_consistency"]


def test_runtime_progress_handler_reports_expert_chain_start_by_id_fallback() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    handler.on_chain_start(
        {"id": ["langgraph", "node", "time_state"]},
        {},
        run_id=uuid4(),
    )

    assert reporter.experts == ["time_state"]


def test_runtime_progress_handler_reports_expert_chain_start_from_metadata() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    handler.on_chain_start(
        {},
        {},
        run_id=uuid4(),
        metadata={"lc_agent_name": "time_state"},
    )

    assert reporter.experts == ["time_state"]


def test_runtime_progress_handler_reports_expert_chain_start_once_while_active() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    first_run_id = uuid4()
    second_run_id = uuid4()
    third_run_id = uuid4()
    fourth_run_id = uuid4()

    handler.on_chain_start(
        {},
        {},
        run_id=first_run_id,
        metadata={"lc_agent_name": "time_state"},
    )
    handler.on_chain_start(
        {},
        {},
        run_id=second_run_id,
        metadata={"lc_agent_name": "time_state"},
    )
    handler.on_chain_end({}, run_id=first_run_id)
    handler.on_chain_start(
        {},
        {},
        run_id=third_run_id,
        metadata={"lc_agent_name": "time_state"},
    )
    handler.on_chain_end({}, run_id=second_run_id)
    handler.on_chain_end({}, run_id=third_run_id)
    handler.on_chain_start(
        {},
        {},
        run_id=fourth_run_id,
        metadata={"lc_agent_name": "time_state"},
    )

    assert reporter.experts == ["time_state", "time_state"]


def test_runtime_progress_handler_releases_active_expert_on_chain_error() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    first_run_id = uuid4()
    second_run_id = uuid4()

    handler.on_chain_start(
        {},
        {},
        run_id=first_run_id,
        metadata={"lc_agent_name": "time_state"},
    )
    handler.on_chain_error(RuntimeError("boom"), run_id=first_run_id)
    handler.on_chain_start(
        {},
        {},
        run_id=second_run_id,
        metadata={"lc_agent_name": "time_state"},
    )

    assert reporter.experts == ["time_state", "time_state"]


def test_runtime_progress_handler_ignores_unknown_and_malformed_serialized() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    handler.on_chain_start(
        {"name": "not-an-expert"},
        {},
        run_id=uuid4(),
    )
    handler.on_chain_start(
        {},
        {},
        run_id=uuid4(),
    )
    handler.on_chain_start(  # type: ignore[arg-type]
        None,
        {},
        run_id=uuid4(),
    )

    assert reporter.experts == []


def test_runtime_progress_handler_reports_expert_tool_success() -> None:
    reporter = FakeReporter()
    dump_writer = FakeDumpWriter()
    handler = _handler(reporter=reporter, dump_writer=dump_writer)

    expert_run_id = uuid4()
    tool_run_id = uuid4()

    handler.on_chain_start(
        {"name": "execution_path_consistency"},
        {},
        run_id=expert_run_id,
    )
    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        parent_run_id=expert_run_id,
        inputs={"file": "contracts/gateway.rs", "start_line": 2, "max_lines": 2},
    )
    handler.on_tool_end(
        "ok",
        run_id=tool_run_id,
        parent_run_id=expert_run_id,
    )

    assert reporter.used == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/gateway.rs",
            "expert_name": "execution_path_consistency",
            "line_start": 2,
            "line_end": 3,
            "offset": None,
            "limit": None,
        }
    ]
    assert reporter.denied == []
    assert dump_writer.calls == [
        (
            "expert_started",
            {
                "relative_path": "contracts/gateway.rs",
                "expert_name": "execution_path_consistency",
                "actor_run_id": str(expert_run_id),
            },
        ),
        (
            "expert_tool_used",
            {
                "relative_path": "contracts/gateway.rs",
                "expert_name": "execution_path_consistency",
                "tool_name": "read_code_chunk",
                "target": "contracts/gateway.rs",
                "line_start": 2,
                "line_end": 3,
                "max_lines": 2,
                "offset": None,
                "limit": None,
                "preview_text": "ok",
                "preview_truncated": False,
                "actor_run_id": str(expert_run_id),
                "tool_call_id": str(tool_run_id),
            },
        ),
    ]


def test_runtime_progress_handler_reports_expert_tool_success_from_metadata() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    tool_run_id = uuid4()

    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        metadata={"lc_agent_name": "collection_validation"},
        inputs={"file": "contracts/gateway.rs", "start_line": 1, "max_lines": 1},
    )
    handler.on_tool_end(
        "ok",
        run_id=tool_run_id,
    )

    assert reporter.used == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/gateway.rs",
            "expert_name": "collection_validation",
            "line_start": 1,
            "line_end": 1,
            "offset": None,
            "limit": None,
        }
    ]
    assert reporter.denied == []


def test_runtime_progress_handler_reports_expert_tool_success_from_parent_run() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    expert_run_id = uuid4()
    tool_run_id = uuid4()

    handler.on_chain_start(
        {"id": ["langgraph", "node", "time_state"]},
        {},
        run_id=expert_run_id,
    )
    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        parent_run_id=expert_run_id,
        inputs={"file": "contracts/gateway.rs", "start_line": 4, "max_lines": 2},
    )
    handler.on_tool_end(
        "ok",
        run_id=tool_run_id,
        parent_run_id=expert_run_id,
    )

    assert reporter.used == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/gateway.rs",
            "expert_name": "time_state",
            "line_start": 4,
            "line_end": 5,
            "offset": None,
            "limit": None,
        }
    ]


def test_runtime_progress_handler_reports_tool_denial_from_error_output() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    tool_run_id = uuid4()

    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        inputs={"file": "contracts/other.rs"},
    )
    handler.on_tool_end(
        "Error: File is outside FACTS scope: contracts/other.rs",
        run_id=tool_run_id,
    )

    assert reporter.used == []
    assert reporter.denied == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/other.rs",
            "current_file": "/contracts/gateway.rs",
            "reason": "File is outside FACTS scope: contracts/other.rs",
            "expert_name": "unknown_subagent",
        }
    ]


def test_runtime_progress_handler_keeps_supervisor_tools_unattributed() -> None:
    reporter = FakeReporter()
    dump_writer = FakeDumpWriter()
    handler = _handler(reporter=reporter, dump_writer=dump_writer)

    tool_run_id = uuid4()

    handler.on_tool_start(
        {"name": "read_file"},
        "",
        run_id=tool_run_id,
        inputs={"file": "contracts/gateway.rs", "offset": 10},
    )
    handler.on_tool_end(
        "ok",
        run_id=tool_run_id,
    )

    assert reporter.used == [
        {
            "tool_name": "read_file",
            "target": "contracts/gateway.rs",
            "expert_name": None,
            "line_start": None,
            "line_end": None,
            "offset": 10,
            "limit": None,
        }
    ]
    assert dump_writer.calls == [
        (
            "supervisor_tool_used",
            {
                "relative_path": "contracts/gateway.rs",
                "tool_name": "read_file",
                "target": "contracts/gateway.rs",
                "line_start": None,
                "line_end": None,
                "max_lines": None,
                "offset": 10,
                "limit": None,
                "preview_text": "ok",
                "preview_truncated": False,
                "tool_call_id": str(tool_run_id),
            },
        )
    ]


def test_runtime_progress_handler_unattributed_read_code_chunk_is_anomaly_actor() -> None:
    reporter = FakeReporter()
    handler = _handler(reporter=reporter)

    tool_run_id = uuid4()

    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        inputs={"file": "contracts/gateway.rs"},
    )
    handler.on_tool_end(
        "ok",
        run_id=tool_run_id,
    )

    assert reporter.used == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/gateway.rs",
            "expert_name": "unknown_subagent",
            "line_start": None,
            "line_end": None,
            "offset": None,
            "limit": None,
        }
    ]


def test_runtime_progress_handler_reports_tool_denial_from_tool_error() -> None:
    reporter = FakeReporter()
    dump_writer = FakeDumpWriter()
    handler = _handler(reporter=reporter, dump_writer=dump_writer)

    tool_run_id = uuid4()

    handler.on_tool_start(
        {"name": "read_code_chunk"},
        "",
        run_id=tool_run_id,
        inputs={"file": "contracts/gateway.rs"},
    )
    handler.on_tool_error(
        RuntimeError("backend denied"),
        run_id=tool_run_id,
    )

    assert reporter.used == []
    assert reporter.denied == [
        {
            "tool_name": "read_code_chunk",
            "target": "contracts/gateway.rs",
            "current_file": "/contracts/gateway.rs",
            "reason": "backend denied",
            "expert_name": "unknown_subagent",
        }
    ]
    assert dump_writer.calls == [
        (
            "expert_tool_denied",
            {
                "relative_path": "contracts/gateway.rs",
                "expert_name": "unknown_subagent",
                "tool_name": "read_code_chunk",
                "target": "contracts/gateway.rs",
                "current_file": "/contracts/gateway.rs",
                "reason": "backend denied",
                "actor_run_id": None,
                "tool_call_id": str(tool_run_id),
            },
        )
    ]


def test_runtime_progress_handler_records_expert_result_on_chain_end() -> None:
    reporter = FakeReporter()
    dump_writer = FakeDumpWriter()
    handler = _handler(reporter=reporter, dump_writer=dump_writer)
    expert_run_id = uuid4()

    handler.on_chain_start(
        {"name": "time_state"},
        {},
        run_id=expert_run_id,
    )
    handler.on_chain_end(
        {
            "structured_response": {
                "status": "VULNERABLE",
                "finding": {
                    "pattern": "Lost rewards on withdrawal",
                    "severity": "HIGH",
                    "location": "contracts/gateway.rs:12",
                    "description": "x",
                    "evidence": "contracts/gateway.rs:12-20",
                },
            }
        },
        run_id=expert_run_id,
    )

    assert dump_writer.calls[0] == (
        "expert_started",
        {
            "relative_path": "contracts/gateway.rs",
            "expert_name": "time_state",
            "actor_run_id": str(expert_run_id),
        },
    )
    assert dump_writer.calls[1][0] == "expert_result"
    assert dump_writer.calls[1][1]["relative_path"] == "contracts/gateway.rs"
    assert dump_writer.calls[1][1]["expert_name"] == "time_state"
    assert dump_writer.calls[1][1]["status"] == "VULNERABLE"
    assert dump_writer.calls[1][1]["actor_run_id"] == str(expert_run_id)
    assert dump_writer.calls[1][1]["final_message_text"] is None
    assert dump_writer.calls[1][1]["final_message_truncated"] is None
    finding = dump_writer.calls[1][1]["finding"]
    assert finding.pattern == "Lost rewards on withdrawal"
    assert finding.severity == "HIGH"
    assert finding.location == "contracts/gateway.rs:12"
    assert dump_writer.calls[2] == (
        "expert_completed",
        {
            "relative_path": "contracts/gateway.rs",
            "expert_name": "time_state",
            "actor_run_id": str(expert_run_id),
        },
    )
