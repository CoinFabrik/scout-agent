from __future__ import annotations

import argparse
from typing import Sequence

from scout_agent.configuration.llm_modes import LLM_MODES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout-agent",
        description="Multi-agent Soroban smart contract auditor.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract-facts",
        help="Generate FACTS.yaml for a target project.",
    )
    _add_shared_arguments(extract_parser)
    extract_parser.add_argument(
        "--facts-path",
        default=None,
        help="Optional output path for FACTS.yaml (default: <project_root>/FACTS.yaml).",
    )
    extract_parser.add_argument(
        "--max-parallel-files",
        type=int,
        default=None,
        help="Maximum number of files to extract in parallel. Defaults to scout.json max_parallel_files or 4.",
    )

    audit_parser = subparsers.add_parser(
        "audit",
        help="Run the supervisor-worker audit using FACTS.yaml.",
    )
    _add_shared_arguments(audit_parser)
    audit_parser.add_argument(
        "--facts-path",
        default=None,
        help="Optional path to FACTS.yaml (default: <project_root>/FACTS.yaml).",
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
