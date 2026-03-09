from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from scout_agent.app.cli import parse_args
from scout_agent.app.command_config import (
    resolve_audit_config,
    resolve_extract_config,
)


def test_resolve_extract_config_cli_values_override_env_and_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "scout.json").write_text(
        "{\n"
        '  "model": "openai:gpt-5",\n'
        '  "mode": "creative",\n'
        '  "files": ["contracts"],\n'
        '  "max_parallel_files": 8\n'
        "}\n",
        encoding="utf-8",
    )

    config = resolve_extract_config(
        Namespace(
            project_root=str(tmp_path),
            facts_path="nested/FACTS.yaml",
            model="anthropic:claude-sonnet-4-5",
            llm_mode="consistent",
            max_parallel_files=3,
        ),
        env={"SCOUT_MODEL": "gemini:gemini-3-flash-preview"},
    )

    assert config.project_root == tmp_path.resolve()
    assert config.facts_path == (tmp_path / "nested" / "FACTS.yaml").resolve()
    assert config.model_name == "anthropic:claude-sonnet-4-5"
    assert config.llm_mode == "consistent"
    assert config.scout_files == ["contracts"]
    assert config.max_parallel_files == 3


def test_resolve_extract_config_uses_env_and_config_fallbacks(tmp_path: Path) -> None:
    (tmp_path / "scout.json").write_text(
        "{\n"
        '  "model": "openai:gpt-5",\n'
        '  "mode": "creative",\n'
        '  "files": ["contracts"],\n'
        '  "max_parallel_files": 7\n'
        "}\n",
        encoding="utf-8",
    )

    config = resolve_extract_config(
        Namespace(
            project_root=str(tmp_path),
            facts_path=None,
            model=None,
            llm_mode=None,
            max_parallel_files=None,
        ),
        env={"SCOUT_MODEL": "anthropic:claude-sonnet-4-5"},
    )

    assert config.model_name == "anthropic:claude-sonnet-4-5"
    assert config.llm_mode == "creative"
    assert config.scout_files == ["contracts"]
    assert config.max_parallel_files == 7


def test_resolve_audit_config_falls_back_to_facts_model_only_when_needed(
    tmp_path: Path,
) -> None:
    config = resolve_audit_config(
        Namespace(
            project_root=str(tmp_path),
            facts_path=None,
            report_path=None,
            model=None,
            llm_mode=None,
            extra_prompt=None,
            ui="tui",
            dump_runtime=False,
        ),
        facts_model="openai:gpt-5",
        env={},
    )

    assert config.project_root == tmp_path.resolve()
    assert config.facts_path == (tmp_path / "FACTS.yaml").resolve()
    assert config.report_path == (tmp_path / "REPORT.md").resolve()
    assert config.model_name == "openai:gpt-5"
    assert config.llm_mode == "consistent"
    assert config.scout_files is None
    assert config.extra_prompt is None
    assert config.ui_mode == "tui"
    assert config.dump_runtime is False


def test_parse_args_defaults_audit_ui_to_tui() -> None:
    args = parse_args(["audit", "/tmp/project"])

    assert args.ui == "tui"
    assert args.dump_runtime is False


def test_parse_args_accepts_dump_runtime_flag() -> None:
    args = parse_args(["audit", "/tmp/project", "--dump-runtime"])

    assert args.dump_runtime is True


def test_parse_args_accepts_render_dump_command() -> None:
    args = parse_args(["render-dump", "/tmp/project/.scout-ai/audit-dumps/run-1"])

    assert args.command == "render-dump"
    assert args.dump_dir == "/tmp/project/.scout-ai/audit-dumps/run-1"


def test_resolve_audit_config_accepts_plain_ui_mode(tmp_path: Path) -> None:
    config = resolve_audit_config(
        Namespace(
            project_root=str(tmp_path),
            facts_path=None,
            report_path=None,
            model=None,
            llm_mode=None,
            extra_prompt=None,
            ui="plain",
            dump_runtime=True,
        ),
        facts_model="openai:gpt-5",
        env={},
    )

    assert config.ui_mode == "plain"
    assert config.dump_runtime is True
