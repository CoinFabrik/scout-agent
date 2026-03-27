from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from threading import Lock

from deepagents.backends import FilesystemBackend
from langchain.tools import tool
from langchain_core.tools import BaseTool

DEFAULT_AGENT_READ_LIMIT = 15
DEFAULT_AGENT_GREP_LIMIT = 15
DEFAULT_SINGLE_READ_LIMIT = 500
MIN_SINGLE_READ_LIMIT = 100
MAX_SINGLE_READ_LIMIT = 500


class PolicyViolationError(Exception):
    """Raised when an agent reaches the threshold for consecutive policy violations."""

    pass


class _ConsecutivePolicyGuard:
    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self.last_code: str | None = None
        self.count = 0
        self.lock = Lock()

    def record_error(self, code: str) -> None:
        with self.lock:
            if code == self.last_code:
                self.count += 1
            else:
                self.last_code = code
                self.count = 1

            if self.count >= self.threshold:
                raise PolicyViolationError(
                    f"Expert shut down: reached {self.threshold} consecutive {code} violations."
                )

    def record_success(self) -> None:
        with self.lock:
            self.last_code = None
            self.count = 0


_ERROR_CODE_PATTERN = re.compile(r"Error\[([A-Z_]+)\]")


def _extract_error_code(message: str) -> str | None:
    match = _ERROR_CODE_PATTERN.search(message)
    return match.group(1) if match else None


def _error(code: str, message: str, next_step: str | None = None) -> str:
    if next_step:
        return f"Error[{code}]: {message} Next: {next_step}"
    return f"Error[{code}]: {message}"


def _regex_grep(
    pattern: str,
    path: Path,
    glob: str | None = None,
) -> list[dict[str, object]] | str:
    """Perform a regex search using ripgrep with BRE-to-PCRE normalization and repair."""

    def run_rg(p: str) -> subprocess.CompletedProcess[str]:
        cmd = [
            "rg",
            "--json",
            "--pcre2",
            "--no-ignore",
            "--hidden",
            "--color",
            "never",
            "-e",
            p,
            path.as_posix(),
        ]
        if glob:
            cmd.insert(1, "--glob")
            cmd.insert(2, glob)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    # 1. Primary Normalization (Fix common OR mistakes)
    # Handle double and single backslashes for pipes
    norm = pattern.replace(r"\\|", "|").replace(r"\|", "|")
    # Reduce double-escaped parens to single (literal)
    norm = norm.replace(r"\\(", r"\(").replace(r"\\)", r"\)")

    try:
        proc = run_rg(norm)
    except FileNotFoundError:
        return "Error[RG_NOT_FOUND]: ripgrep (rg) is not installed."
    except subprocess.TimeoutExpired:
        return "Error[RG_TIMEOUT]: Grep search timed out after 30s."

    # 2. Automatic Repair (Handle unclosed parentheses)
    if proc.returncode == 2 and "missing closing parenthesis" in proc.stderr.lower():
        # The agent likely forgot to escape literal parentheses.
        # We'll try to escape ALL unescaped parentheses and retry.
        repaired = ""
        i = 0
        while i < len(norm):
            char = norm[i]
            if char in "()" and (i == 0 or norm[i - 1] != "\\"):
                repaired += "\\" + char
            else:
                repaired += char
            i += 1

        if repaired != norm:
            try:
                proc = run_rg(repaired)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    if proc.returncode == 2:
        # rg return code 2 indicates a regex error
        return f"Error[INVALID_REGEX]: {proc.stderr.strip()}"

    results: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        try:
            data = json.loads(line)
            if data.get("type") == "match":
                payload = data.get("data", {})
                results.append(
                    {
                        "path": payload.get("path", {}).get("text"),
                        "line": payload.get("line_number"),
                        "text": payload.get("lines", {}).get("text", "").rstrip("\n"),
                    }
                )
        except json.JSONDecodeError:
            continue

    return results



def build_readonly_tools(
    *,
    root_dir: Path,
    scope_path: Path,
    agent_read_limit: int = DEFAULT_AGENT_READ_LIMIT,
    agent_grep_limit: int = DEFAULT_AGENT_GREP_LIMIT,
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
    grep_policy = _AgentGrepPolicy(max_calls=agent_grep_limit)
    policy_guard = _ConsecutivePolicyGuard()

    def resolve_in_scope(raw_path: str | None, *, field_name: str) -> Path:
        candidate = grep_path if raw_path is None else Path(raw_path).expanduser()
        if not candidate.is_absolute():
            code = "PATH_NOT_ABSOLUTE"
            err = _error(
                code,
                f"`{field_name}` must be an absolute path inside the allowed scope. Received: {candidate}.",
                "Use an absolute path returned by `grep`. If no `grep` matches yet, ensure you are searching from the absolute project root.",
            )
            policy_guard.record_error(code)
            raise ValueError(err)

        candidate = candidate.resolve()

        if candidate != scope_path and not candidate.is_relative_to(scope_path):
            code = "PATH_OUT_OF_SCOPE"
            err = _error(
                code,
                f"`{field_name}` is outside the allowed scope `{scope_path}`.",
                "Choose a path within the allowed scope.",
            )
            policy_guard.record_error(code)
            raise ValueError(err)

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
        `limit` must be between 1 and 500 lines.
        Reads MUST be sequential for each file. Your next read for a specific file must start at the offset where the previous read for that file ended (or later).
        Overlapping or repeated reads are blocked to prevent context loops.
        Use `grep` when you need to locate candidate files or matching regions first.
        """
        if offset < 0:
            policy_guard.record_error("INVALID_OFFSET")
            return _error(
                "INVALID_OFFSET",
                "`offset` must be >= 0.",
                "Provide a non-negative offset.",
            )

        if limit > MAX_SINGLE_READ_LIMIT:
            policy_guard.record_error("INVALID_LIMIT")
            return _error(
                "INVALID_LIMIT",
                f"`limit` ({limit}) exceeds the maximum allowed of {MAX_SINGLE_READ_LIMIT}.",
                f"Request a smaller window (<= {MAX_SINGLE_READ_LIMIT}).",
            )

        try:
            resolved_path = resolve_in_scope(file_path, field_name="file_path")
            with read_policy.lock:
                reason = read_policy.check_read_allowed(
                    file_path=resolved_path,
                    offset=offset,
                    limit=limit,
                    policy_guard=policy_guard,
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
                code = _extract_error_code(rendered)
                if code:
                    policy_guard.record_error(code)
                return rendered

            actual_lines_read = len(rendered.splitlines())

            with read_policy.lock:
                read_policy.record_success(
                    file_path=resolved_path,
                    offset=offset,
                    limit=limit,
                    actual_lines_read=actual_lines_read,
                )
            policy_guard.record_success()
            return rendered
        except FileNotFoundError:
            code = "FILE_NOT_FOUND"
            err = _error(
                code,
                f"File does not exist: {file_path}.",
                "Verify the path from `grep` output before reading.",
            )
            policy_guard.record_error(code)
            return err
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
        `pattern` must be a valid regular expression (PCRE-compatible). Use `|` for OR, not `\\|`.
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
                    policy_guard=policy_guard,
                )
                if reason is not None:
                    return reason

            result = _regex_grep(
                pattern,
                path=resolved_path,
                glob=glob,
            )
            rendered = result if isinstance(result, str) else str(result)

            if rendered.startswith("Error:") or rendered.startswith("Error["):
                code = _extract_error_code(rendered)
                if code:
                    policy_guard.record_error(code)
                return rendered

            with grep_policy.lock:
                grep_policy.record_success(
                    pattern=pattern,
                    path=resolved_path,
                    glob=glob,
                )
            policy_guard.record_success()
            return rendered
        except FileNotFoundError:
            code = "FILE_NOT_FOUND"
            bad_path = path if path is not None else grep_path.as_posix()
            err = _error(
                code,
                f"Search path does not exist: {bad_path}.",
                "Choose an existing path within the allowed scope.",
            )
            policy_guard.record_error(code)
            return err
        except ValueError as exc:
            return str(exc)

    return [read_file, grep]


class _AgentReadPolicy:
    def __init__(self, *, max_unique_files: int) -> None:
        self.max_unique_files = max_unique_files
        self.read_files: set[str] = set()
        self.read_spans: set[tuple[str, int, int]] = set()
        self.file_high_water_mark: dict[str, int] = {}
        self.lock = Lock()

    def check_read_allowed(
        self,
        *,
        file_path: Path,
        offset: int,
        limit: int,
        policy_guard: _ConsecutivePolicyGuard,
    ) -> str | None:
        normalized_path = file_path.as_posix()
        high_water_mark = self.file_high_water_mark.get(normalized_path, 0)

        if offset < high_water_mark:
            policy_guard.record_error("REDUNDANT_READ")
            return _error(
                "REDUNDANT_READ",
                (
                    f"You already read this file up to offset {high_water_mark}. "
                    "To avoid redundant context and prevent loops, your next read MUST start at or after that offset."
                ),
                f"Set `offset` to {high_water_mark} or greater, or use `grep` to find a different location.",
            )

        if self.max_unique_files == 0:
            return None

        if (
            normalized_path not in self.read_files
            and len(self.read_files) >= self.max_unique_files
        ):
            policy_guard.record_error("READ_LIMIT_REACHED")
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
        actual_lines_read: int,
    ) -> None:
        normalized_path = file_path.as_posix()
        self.read_files.add(normalized_path)
        self.read_spans.add((normalized_path, offset, limit))
        
        new_mark = offset + actual_lines_read
        if new_mark > self.file_high_water_mark.get(normalized_path, 0):
            self.file_high_water_mark[normalized_path] = new_mark


class _AgentGrepPolicy:
    def __init__(self, *, max_calls: int) -> None:
        self.max_calls = max_calls
        self.grep_calls: set[tuple[str, str, str | None]] = set()
        self.total_calls = 0
        self.lock = Lock()

    def check_grep_allowed(
        self,
        *,
        pattern: str,
        path: Path,
        glob: str | None,
        policy_guard: _ConsecutivePolicyGuard,
    ) -> str | None:
        normalized_path = path.as_posix()
        call = (pattern, normalized_path, glob)

        if self.max_calls > 0 and self.total_calls >= self.max_calls:
            policy_guard.record_error("GREP_LIMIT_REACHED")
            return _error(
                "GREP_LIMIT_REACHED",
                f"Grep search budget exhausted ({self.max_calls} calls).",
                "Choose a file already identified and use `read_file` to perform analysis.",
            )

        if call in self.grep_calls:
            policy_guard.record_error("REPETITIVE_GREP")
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
        self.total_calls += 1
