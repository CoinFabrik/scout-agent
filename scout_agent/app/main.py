from __future__ import annotations

import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from scout_agent.llm.providers import ProviderError

from .audit import run_audit_command
from .cli import parse_args
from .console_reporting import ConsoleOutput
from .extract_facts import run_extract_facts_command


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    output = ConsoleOutput(stdout=sys.stdout, stderr=sys.stderr)

    try:
        if args.command == "extract-facts":
            return run_extract_facts_command(args, output=output)

        if args.command == "audit":
            return run_audit_command(args, output=output)

        output.print_error(f"Unknown command: {args.command}")
        return 1
    except (FileNotFoundError, ValueError, ProviderError) as exc:
        output.print_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
