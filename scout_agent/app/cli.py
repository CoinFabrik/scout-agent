from __future__ import annotations

import argparse
from collections.abc import Sequence

from scout_agent.configuration.llm_modes import LLM_MODES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-agent",
        description="Multi-agent Soroban smart contract auditor.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract-facts",
        help="Generate per-file facts plus aggregate FACTS.yml for a target project.",
    )
    _add_shared_arguments(extract_parser)
    extract_parser.add_argument(
        "--facts-path",
        default=None,
        help="Optional output directory for per-file facts and aggregate FACTS.yml (default: <project_root>/.scout-ai/facts).",
    )
    extract_parser.add_argument(
        "--max-parallel-files",
        type=int,
        default=None,
        help="Maximum number of files to extract in parallel. Defaults to scout.json max_parallel_files or 4.",
    )

    audit_parser = subparsers.add_parser(
        "audit",
        help="Run the supervisor-worker audit plus execution_path_consistency using extracted facts.",
    )
    _add_shared_arguments(audit_parser)
    audit_parser.add_argument(
        "--facts-path",
        default=None,
        help="Optional path to the facts directory containing per-file facts and FACTS.yml (default: <project_root>/.scout-ai/facts).",
    )
    audit_parser.add_argument(
        "--report-path",
        default=None,
        help="Optional output path for REPORT.md (default: <project_root>/REPORT.md).",
    )
    audit_parser.add_argument(
        "--extra-prompt",
        default=None,
        help="Optional .txt file whose contents are appended to supervisor and expert prompts.",
    )
    audit_parser.add_argument(
        "--max-parallel-files",
        type=int,
        default=None,
        help="Maximum number of files to audit in parallel. Defaults to scout.json max_parallel_files or 4.",
    )
    audit_parser.add_argument(
        "--agent-read-limit",
        type=int,
        default=None,
        help="Maximum number of unique files each audit agent can read via read_file. Use 0 to disable the file-count cap. Defaults to scout.json agent_read_limit or 15.",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "project_root",
        help="Path to the target Soroban project root.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Required model identifier in the form provider:model, for example anthropic:claude-sonnet-4-5.",
    )
    parser.add_argument(
        "--llm-mode",
        choices=LLM_MODES,
        default=None,
        help="LLM mode to use for structured extraction and auditing. Defaults to scout.json mode or 'consistent'.",
    )
