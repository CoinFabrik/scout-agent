from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from deepagents.backends import FilesystemBackend
from langchain.tools import tool
from langchain_core.tools import BaseTool

from scout_agent.audit.tools.policies import (
    _AgentGrepPolicy,
    _AgentReadPolicy,
    _ConsecutivePolicyGuard,
    _error,
)

DEFAULT_AGENT_READ_LIMIT = 15
DEFAULT_AGENT_GREP_LIMIT = 15
DEFAULT_SINGLE_READ_LIMIT = 500
MIN_SINGLE_READ_LIMIT = 100
MAX_SINGLE_READ_LIMIT = 500


_ERROR_CODE_PATTERN = re.compile(r"Error\[([A-Z_]+)\]")


def _extract_error_code(message: str) -> str | None:
    match = _ERROR_CODE_PATTERN.search(message)
    return match.group(1) if match else None


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
        if raw_path is not None:
            raw_path = raw_path.strip('"').strip("'")
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
        `limit` must be between 100 and 500 lines. Requests below 100 will be automatically increased.
        Prefer reading enough context to answer the question in one pass.
        Avoid many adjacent or heavily overlapping reads from the same file.
        If you need more context, increase `limit` substantially instead of shifting `offset` by 1.
        Do not repeat the exact same `(file_path, offset, limit)` span.
        Use `grep` when you need to locate candidate files or matching regions first.
        """
        if offset < 0:
            policy_guard.record_error("INVALID_OFFSET")
            return _error(
                "INVALID_OFFSET",
                "`offset` must be >= 0.",
                "Provide a non-negative offset.",
            )

        if limit < MIN_SINGLE_READ_LIMIT:
            limit = MIN_SINGLE_READ_LIMIT

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

            with read_policy.lock:
                read_policy.record_success(
                    file_path=resolved_path,
                    offset=offset,
                    limit=limit,
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
