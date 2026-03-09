from __future__ import annotations

import json
from pathlib import Path

from scout_agent.runtime.audit.dump_rendering import render_dump_artifacts


def test_render_dump_artifacts_renders_legacy_dump_markdown(tmp_path: Path) -> None:
    dump_dir = tmp_path / ".scout-ai" / "audit-dumps" / "run-1"
    file_dir = dump_dir / "files" / "src" / "backstop" / "deposit.rs"
    expert_dir = file_dir / "experts"
    expert_dir.mkdir(parents=True)

    (dump_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "status": "running",
                "project_root": str(tmp_path),
                "facts_path": str(tmp_path / "FACTS.yaml"),
                "report_path": str(tmp_path / "REPORT.md"),
                "model_name": "gemini:gemini-3-flash-preview",
                "llm_mode": "consistent",
                "started_at": "2026-03-09T15:17:14Z",
                "finished_at": None,
                "files_total": 20,
                "files_completed": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (file_dir / "summary.json").write_text(
        json.dumps(
            {
                "relative_path": "src/backstop/deposit.rs",
                "status": "running",
                "started_at": "2026-03-09T15:17:14Z",
                "finished_at": None,
                "spawned_experts": ["sentinel_logic"],
                "findings_count": 0,
                "supervisor_tool_calls": 2,
                "expert_tool_calls": 1,
                "used_text_fallback": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (file_dir / "supervisor.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "seq": 1,
                        "timestamp": "2026-03-09T15:17:14Z",
                        "run_id": "run-1",
                        "file": "src/backstop/deposit.rs",
                        "actor_type": "supervisor",
                        "actor_name": "supervisor",
                        "event_type": "started",
                    }
                ),
                json.dumps(
                    {
                        "seq": 2,
                        "timestamp": "2026-03-09T15:17:18Z",
                        "run_id": "run-1",
                        "file": "src/backstop/deposit.rs",
                        "actor_type": "supervisor",
                        "actor_name": "supervisor",
                        "event_type": "tool_used",
                        "tool_name": "task",
                        "target": "src/backstop/deposit.rs",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (expert_dir / "sentinel_logic.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "seq": 3,
                        "timestamp": "2026-03-09T15:17:32Z",
                        "run_id": "run-1",
                        "file": "src/backstop/deposit.rs",
                        "actor_type": "expert",
                        "actor_name": "sentinel_logic",
                        "event_type": "started",
                    }
                ),
                json.dumps(
                    {
                        "seq": 4,
                        "timestamp": "2026-03-09T15:17:35Z",
                        "run_id": "run-1",
                        "file": "src/backstop/deposit.rs",
                        "actor_type": "expert",
                        "actor_name": "sentinel_logic",
                        "event_type": "result",
                        "status": "SAFE",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    render_dump_artifacts(dump_dir)

    index_text = (dump_dir / "index.md").read_text(encoding="utf-8")
    file_text = (file_dir / "file.md").read_text(encoding="utf-8")
    supervisor_timeline_text = (file_dir / "supervisor.timeline.md").read_text(
        encoding="utf-8"
    )
    expert_timeline_text = (
        file_dir / "experts" / "sentinel_logic.timeline.md"
    ).read_text(encoding="utf-8")

    assert "`src/backstop/deposit.rs`" in index_text
    assert "./files/src/backstop/deposit.rs/file.md" in index_text
    assert "Supervisor timeline" in file_text
    assert "Expert timeline: `sentinel_logic`" in file_text
    assert "Legacy dump" in supervisor_timeline_text
    assert "Dispatch subagent task" in supervisor_timeline_text
    assert "Final AI message: not captured in this run." in supervisor_timeline_text
    assert "Result: `SAFE`" in expert_timeline_text
    assert not (file_dir / "timeline.md").exists()
