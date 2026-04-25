from __future__ import annotations

from pathlib import Path

from scout_agent.progress import LineProgressSink


class ExtractProgressReporter:
    def __init__(self, sink: LineProgressSink) -> None:
        self._sink = sink

    def started(
        self,
        *,
        project_root: Path,
        total_files: int,
        model_name: str,
        llm_mode: str,
    ) -> None:
        self._sink.emit(
            "Starting extract-facts for "
            f"{project_root} with {total_files} file(s) using {model_name} [{llm_mode}]"
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
    ) -> None:
        self._sink.emit(f"Extracting {index}/{total}: {relative_path}")

    def file_completed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        function_count: int,
    ) -> None:
        self._sink.emit(
            f"Completed {index}/{total}: {relative_path} ({function_count} function(s))",
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
        self._sink.emit(
            f"Failed {index}/{total}: {relative_path} ({error_type}: {message})"
        )
