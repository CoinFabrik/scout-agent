from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from scout_agent.app.console_reporting import ConsoleOutput
from scout_agent.app.errors import CommandError
from scout_agent.runtime.audit.dump_rendering import render_dump_artifacts


def run_render_dump_command(
    args: Namespace,
    *,
    output: ConsoleOutput,
) -> int:
    try:
        dump_dir = Path(args.dump_dir).expanduser().resolve()
        if not dump_dir.exists():
            raise FileNotFoundError(f"Dump directory does not exist: {dump_dir}")
        if not dump_dir.is_dir():
            raise ValueError(f"Dump path must be a directory: {dump_dir}")
        if not (dump_dir / "run.json").exists():
            raise ValueError(f"Dump directory does not contain run.json: {dump_dir}")

        render_dump_artifacts(dump_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandError(str(exc)) from exc

    output.print_dump_render_summary(dump_dir=dump_dir)
    return 0
