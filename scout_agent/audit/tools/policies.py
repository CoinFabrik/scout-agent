from __future__ import annotations

from pathlib import Path
from threading import Lock


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


def _error(code: str, message: str, next_step: str | None = None) -> str:
    if next_step:
        return f"Error[{code}]: {message} Next: {next_step}"
    return f"Error[{code}]: {message}"


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
        policy_guard: _ConsecutivePolicyGuard,
    ) -> str | None:
        normalized_path = file_path.as_posix()
        span = (normalized_path, offset, limit)

        if span in self.read_spans:
            policy_guard.record_error("SPAN_ALREADY_READ")
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
    ) -> None:
        normalized_path = file_path.as_posix()
        self.read_files.add(normalized_path)
        self.read_spans.add((normalized_path, offset, limit))


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
