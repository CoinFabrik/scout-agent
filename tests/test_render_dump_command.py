from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from scout_agent.app.render_dump import run_render_dump_command


class FakeOutput:
    def __init__(self) -> None:
        self.dump_dirs: list[Path] = []

    def print_dump_render_summary(self, *, dump_dir: Path) -> None:
        self.dump_dirs.append(dump_dir)


def test_run_render_dump_command_renders_existing_dump(tmp_path: Path) -> None:
    dump_dir = tmp_path / ".scout-ai" / "audit-dumps" / "run-1"
    file_dir = dump_dir / "files" / "src" / "contract.rs"
    file_dir.mkdir(parents=True)
    (dump_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "status": "completed",
                "project_root": str(tmp_path),
                "facts_path": str(tmp_path / "FACTS.yaml"),
                "report_path": str(tmp_path / "REPORT.md"),
                "model_name": "openai:gpt-5",
                "llm_mode": "consistent",
                "started_at": "2026-03-09T15:00:00Z",
                "finished_at": "2026-03-09T15:01:00Z",
                "files_total": 1,
                "files_completed": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (file_dir / "summary.json").write_text(
        json.dumps(
            {
                "relative_path": "src/contract.rs",
                "status": "completed",
                "started_at": "2026-03-09T15:00:00Z",
                "finished_at": "2026-03-09T15:01:00Z",
                "spawned_experts": [],
                "findings_count": 0,
                "supervisor_tool_calls": 0,
                "expert_tool_calls": 0,
                "used_text_fallback": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output = FakeOutput()

    exit_code = run_render_dump_command(
        Namespace(dump_dir=str(dump_dir)),
        output=output,  # type: ignore[arg-type]
    )

    assert exit_code == 0
    assert output.dump_dirs == [dump_dir.resolve()]
    assert (dump_dir / "index.md").exists()
    assert (file_dir / "file.md").exists()
    assert (file_dir / "supervisor.timeline.md").exists()
    assert not (file_dir / "timeline.md").exists()
