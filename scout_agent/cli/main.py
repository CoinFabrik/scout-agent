from __future__ import annotations

import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from scout_agent.cli.commands.audit import run_audit_command
from scout_agent.cli.commands.extract_facts import run_extract_facts_command
from scout_agent.cli.errors import CommandError
from scout_agent.cli.output.console import ConsoleOutput
from scout_agent.cli.parser import parse_args



def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    output = ConsoleOutput(stdout=sys.stdout, stderr=sys.stderr)

    try:
        if args.command == "extract-facts":
            return run_extract_facts_command(args, output)

        if args.command == "audit":
            return run_audit_command(args, output)

        raise CommandError(f"Unknown command: {args.command}")
    except CommandError as exc:
        output.print_error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        output.print_error(f"unexpected {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
