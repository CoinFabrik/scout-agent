from __future__ import annotations

from pathlib import Path
from typing import Final
from collections.abc import Collection

from scout_agent.runtime.source.source_filter import build_analysis_source

EXPERT_READ_MAX_LINES: Final[int] = 100


def read_sanitized_code_chunk(
    project_root: Path,
    relative_path: str,
    *,
    allowed_paths: Collection[str],
    start_line: int = 1,
    max_lines: int = EXPERT_READ_MAX_LINES,
) -> str:
    if start_line < 1:
        raise ValueError(f"start_line must be >= 1; got {start_line}")
    if max_lines < 1 or max_lines > EXPERT_READ_MAX_LINES:
        raise ValueError(
            f"max_lines must be between 1 and {EXPERT_READ_MAX_LINES}; got {max_lines}"
        )

    file_path, normalized_relative_path = resolve_in_scope_file(
        project_root=project_root.resolve(),
        relative_path=relative_path,
        allowed_paths=allowed_paths,
    )
    analysis_source = build_analysis_source(
        path=file_path,
        relative_path=normalized_relative_path,
    )

    all_lines = analysis_source.analysis_text.splitlines()
    start_index = start_line - 1
    selected_lines = all_lines[start_index : start_index + max_lines]

    if start_index >= len(all_lines):
        return (
            f"Error: Line offset {start_index} exceeds file length "
            f"({len(all_lines)} lines)"
        )

    return "\n".join(
        f"{line_number:4}: {line}"
        for line_number, line in enumerate(selected_lines, start=start_line)
    )


def resolve_in_scope_file(
    *,
    project_root: Path,
    relative_path: str,
    allowed_paths: Collection[str],
) -> tuple[Path, str]:
    raw_relative_path = relative_path.strip()
    if not raw_relative_path:
        raise ValueError("File path must be non-empty.")

    raw_path = Path(raw_relative_path)
    if raw_path.is_absolute():
        raise ValueError(
            f"File path must be relative to the project root: {raw_relative_path}"
        )

    normalized_relative_path = raw_path.as_posix()
    normalized_allowed_paths = {
        Path(path.strip()).as_posix() for path in allowed_paths if path and path.strip()
    }

    if normalized_relative_path not in normalized_allowed_paths:
        raise ValueError(f"File is outside FACTS scope: {normalized_relative_path}")

    joined_path = project_root / raw_path
    if joined_path.is_symlink():
        raise ValueError(
            f"Refusing to operate on symlinked in-scope file: {normalized_relative_path}"
        )

    resolved_path = joined_path.resolve()
    if resolved_path != project_root and project_root not in resolved_path.parents:
        raise ValueError(
            f"Resolved file path escapes project root: {normalized_relative_path}"
        )

    if not resolved_path.exists():
        raise FileNotFoundError(
            f"In-scope file does not exist: {normalized_relative_path}"
        )
    if not resolved_path.is_file():
        raise ValueError(f"In-scope path is not a file: {normalized_relative_path}")

    return resolved_path, normalized_relative_path
