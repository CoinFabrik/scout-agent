from __future__ import annotations


class CommandError(RuntimeError):
    """User-facing command failure that should be rendered without a traceback."""
