from __future__ import annotations

import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from .audit import run_audit_command
from .cli import parse_args
from .console_reporting import ConsoleOutput
from .errors import CommandError
from .extract_facts import run_extract_facts_command


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
