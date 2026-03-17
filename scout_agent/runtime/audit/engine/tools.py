from __future__ import annotations

from pathlib import Path
from threading import Lock

from deepagents.backends import FilesystemBackend
from langchain.tools import tool
from langchain_core.tools import BaseTool

DEFAULT_AGENT_READ_LIMIT = 5
DEFAULT_SINGLE_READ_LIMIT = 2_000


def _error(code: str, message: str, next_step: str | None = None) -> str:
    if next_step:
        return f"Error[{code}]: {message} Next: {next_step}"
    return f"Error[{code}]: {message}"


def build_readonly_tools(
    *,
    root_dir: Path,
    scope_path: Path,
    agent_read_limit: int = DEFAULT_AGENT_READ_LIMIT,
    default_grep_path: str | None = None,
) -> list[BaseTool]:
    root_dir = root_dir.resolve()
    scope_path = scope_path.resolve()
    grep_path = (
        Path(default_grep_path).expanduser().resolve()
        if default_grep_path is not None
        else scope_path
    )

    backend = FilesystemBackend(root_dir=root_dir, virtual_mode=False)
    read_policy = _AgentReadPolicy(max_unique_files=agent_read_limit)
    grep_policy = _AgentGrepPolicy()

    def resolve_in_scope(raw_path: str | None, *, field_name: str) -> Path:
        candidate = grep_path if raw_path is None else Path(raw_path).expanduser()
        if not candidate.is_absolute():
            raise ValueError(
                _error(
                    "PATH_NOT_ABSOLUTE",
                    f"`{field_name}` must be an absolute path inside the allowed scope. Received: {candidate}.",
                    "Use an absolute path returned by `grep`. If no `grep` matches yet, ensure you are searching from the absolute project root.",
                )
            )

        candidate = candidate.resolve()

        if candidate != scope_path and not candidate.is_relative_to(scope_path):
            raise ValueError(
                _error(
                    "PATH_OUT_OF_SCOPE",
                    f"`{field_name}` is outside the allowed scope `{scope_path}`.",
                    "Choose a path within the allowed scope.",
                )
            )

        return candidate

    @tool("read_file")
    def read_file(
        file_path: str,
        offset: int = 0,
        limit: int = DEFAULT_SINGLE_READ_LIMIT,
    ) -> str:
        """Read a focused chunk from one file inside the allowed scope.

        Use this to inspect the actual contents of a file when you already have a likely relevant path.
        `file_path` must be an absolute path inside the allowed scope.
        Prefer reading enough context to answer the question in one pass; for code, around 100 lines or more is often better than many tiny reads.
        Avoid many adjacent or heavily overlapping reads from the same file.
        If you need more context, increase `limit` substantially instead of shifting `offset` by 1.
        Do not repeat the exact same `(file_path, offset, limit)` span.
        Use `grep` when you need to locate candidate files or matching regions first.
        """
        if offset < 0:
            return _error(
                "INVALID_OFFSET",
                "`offset` must be >= 0.",
                "Provide a non-negative offset.",
            )
        if limit < 1:
            return _error(
                "INVALID_LIMIT",
                "`limit` must be >= 1.",
                "Provide a positive limit.",
            )

        try:
            resolved_path = resolve_in_scope(file_path, field_name="file_path")
            with read_policy.lock:
                reason = read_policy.check_read_allowed(
                    file_path=resolved_path,
                    offset=offset,
                    limit=limit,
                )
                if reason is not None:
                    return reason

            result = backend.read(
                resolved_path.as_posix(),
                offset=offset,
                limit=limit,
            )
            rendered = result if isinstance(result, str) else str(result)

            if rendered.startswith("Error:") or rendered.startswith("Error["):
                return rendered

            with read_policy.lock:
                read_policy.record_success(
                    file_path=resolved_path,
                    offset=offset,
                    limit=limit,
                )
            return rendered
        except FileNotFoundError:
            return _error(
                "FILE_NOT_FOUND",
                f"File does not exist: {file_path}.",
                "Verify the path from `grep` output before reading.",
            )
        except ValueError as exc:
            return str(exc)

    @tool("grep")
    def grep(
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> str:
        """Search for a regex pattern inside the allowed scope.

        Use this to locate candidate files or matching regions when the relevant file or location is not yet clear.
        `path` must be an absolute path inside the allowed scope, or omit it to search the default scope.
        Prefer a narrow `path` or `glob` to reduce noise.
        Returns matches and file paths, not full file contents.
        After finding a promising result, use `read_file` to inspect the surrounding content.
        """
        try:
            resolved_path = resolve_in_scope(path, field_name="path")
            with grep_policy.lock:
                reason = grep_policy.check_grep_allowed(
                    pattern=pattern,
                    path=resolved_path,
                    glob=glob,
                )
                if reason is not None:
                    return reason

            result = backend.grep_raw(
                pattern,
                path=resolved_path.as_posix(),
                glob=glob,
            )
            rendered = result if isinstance(result, str) else str(result)

            if rendered.startswith("Error:") or rendered.startswith("Error["):
                return rendered

            with grep_policy.lock:
                grep_policy.record_success(
                    pattern=pattern,
                    path=resolved_path,
                    glob=glob,
                )
            return rendered
        except FileNotFoundError:
            bad_path = path if path is not None else grep_path.as_posix()
            return _error(
                "FILE_NOT_FOUND",
                f"Search path does not exist: {bad_path}.",
                "Choose an existing path within the allowed scope.",
            )
        except ValueError as exc:
            return str(exc)

    return [read_file, grep]


class _AgentReadPolicy:
    def __init__(self, *, max_unique_files: int) -> None:
        self.max_unique_files = max_unique_files
        self.read_files: set[str] = set()
        self.read_spans: set[tuple[str, int, int]] = set()
        self.lock = Lock()

    def check_read_allowed(
        self,
        *,
        file_path: Path,
        offset: int,
        limit: int,
    ) -> str | None:
        normalized_path = file_path.as_posix()
        span = (normalized_path, offset, limit)

        if span in self.read_spans:
            return _error(
                "SPAN_ALREADY_READ",
                (
                    "This exact file span was already read. "
                    f"file_path={normalized_path}, offset={offset}, limit={limit}."
                ),
                "Change `offset` or `limit`, or use `grep` to find a different location. Do not retry the same span.",
            )

        if self.max_unique_files == 0:
            return None

        if (
            normalized_path not in self.read_files
            and len(self.read_files) >= self.max_unique_files
        ):
            return _error(
                "READ_LIMIT_REACHED",
                (
                    f"Unique file read limit reached ({self.max_unique_files}). "
                    f"Cannot open new file: {normalized_path}."
                ),
                "Continue with files already read, or use `grep` to choose the single best file before reading.",
            )

        return None

    def record_success(
        self,
        *,
        file_path: Path,
        offset: int,
        limit: int,
    ) -> None:
        normalized_path = file_path.as_posix()
        self.read_files.add(normalized_path)
        self.read_spans.add((normalized_path, offset, limit))


class _AgentGrepPolicy:
    def __init__(self) -> None:
        self.grep_calls: set[tuple[str, str, str | None]] = set()
        self.lock = Lock()

    def check_grep_allowed(
        self,
        *,
        pattern: str,
        path: Path,
        glob: str | None,
    ) -> str | None:
        normalized_path = path.as_posix()
        call = (pattern, normalized_path, glob)

        if call in self.grep_calls:
            return _error(
                "REPETITIVE_GREP",
                (
                    "This exact grep call was already made. "
                    f"pattern='{pattern}', path='{normalized_path}', glob='{glob}'."
                ),
                "Change the pattern, path, or glob to search differently, or use `read_file` on one of the previously found results.",
            )

        return None

    def record_success(
        self,
        *,
        pattern: str,
        path: Path,
        glob: str | None,
    ) -> None:
        normalized_path = path.as_posix()
        self.grep_calls.add((pattern, normalized_path, glob))
