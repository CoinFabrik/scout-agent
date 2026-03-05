from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ExtractProgressReporter(Protocol):
    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None: ...

    def file_started(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
    ) -> None: ...

    def file_completed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        function_count: int,
    ) -> None: ...

    def file_failed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        error_type: str,
        message: str,
    ) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ExtractContext:
    project_root: Path
    facts_path: Path
    model_name: str
    llm_mode: str
    scout_files: list[str] | None
    max_parallel_files: int
    reporter: ExtractProgressReporter


@dataclass(frozen=True, slots=True)
class ExtractFactsPipelineResult:
    project_root: Path
    facts_path: Path
    file_count: int
    function_count: int
    scope_fingerprint: str


@dataclass(frozen=True, slots=True)
class FileExtractionFailure:
    relative_path: str
    error_type: str
    message: str


class RetryableExtractionError(ValueError):
    """Raised for malformed extraction outputs that are safe to retry."""


class ExtractFactsParallelError(ValueError):
    def __init__(self, failures: list[FileExtractionFailure]) -> None:
        self.failures = failures
        super().__init__(self._render_message(failures))

    @staticmethod
    def _render_message(failures: list[FileExtractionFailure]) -> str:
        lines = [f"extract-facts failed for {len(failures)} file(s):"]
        for failure in failures:
            lines.append(
                f"- {failure.relative_path}: {failure.error_type}: {failure.message}"
            )
        return "\n".join(lines)
