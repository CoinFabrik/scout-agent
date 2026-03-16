from __future__ import annotations

from pathlib import Path
from typing import TextIO


class ExtractProgressReporter:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout

    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None:
        print(
            "Starting extract-facts for "
            f"{project_root} with {total_files} file(s) using {model_name} [{llm_mode}]",
            file=self._stdout,
            flush=True,
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
    ) -> None:
        print(
            f"Extracting {index}/{total}: {relative_path}",
            file=self._stdout,
            flush=True,
        )

    def file_completed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        function_count: int,
    ) -> None:
        print(
            f"Completed {index}/{total}: {relative_path} ({function_count} function(s))",
            file=self._stdout,
            flush=True,
        )

    def file_failed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        error_type: str,
        message: str,
    ) -> None:
        print(
            f"Failed {index}/{total}: {relative_path} ({error_type}: {message})",
            file=self._stdout,
            flush=True,
        )
