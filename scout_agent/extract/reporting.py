from __future__ import annotations

from pathlib import Path

from scout_agent.progress import LineProgressSink


def format_extract_started_line(
    *,
    project_root: Path,
    total_files: int,
    model_name: str,
    llm_mode: str,
) -> str:
    return (
        "Starting extract-facts for "
        f"{project_root} with {total_files} file(s) using {model_name} [{llm_mode}]"
    )


def format_extract_file_started_line(
    *,
    index: int,
    total: int,
    relative_path: str,
) -> str:
    return f"Extracting {index}/{total}: {relative_path}"


def format_extract_file_completed_line(
    *,
    index: int,
    total: int,
    relative_path: str,
    function_count: int,
) -> str:
    return f"Completed {index}/{total}: {relative_path} ({function_count} function(s))"


def format_extract_file_failed_line(
    *,
    index: int,
    total: int,
    relative_path: str,
    error_type: str,
    message: str,
) -> str:
    return f"Failed {index}/{total}: {relative_path} ({error_type}: {message})"


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
            format_extract_started_line(
                project_root=project_root,
                total_files=total_files,
                model_name=model_name,
                llm_mode=llm_mode,
            )
        )

    def file_started(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
    ) -> None:
        self._sink.emit(
            format_extract_file_started_line(
                index=index,
                total=total,
                relative_path=relative_path,
            )
        )

    def file_completed(
        self,
        *,
        index: int,
        total: int,
        relative_path: str,
        function_count: int,
    ) -> None:
        self._sink.emit(
            format_extract_file_completed_line(
                index=index,
                total=total,
                relative_path=relative_path,
                function_count=function_count,
            ),
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
            format_extract_file_failed_line(
                index=index,
                total=total,
                relative_path=relative_path,
                error_type=error_type,
                message=message,
            )
        )
