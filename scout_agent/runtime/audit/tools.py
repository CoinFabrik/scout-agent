from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Collection, Final

from scout_agent.runtime.source.source_filter import build_analysis_source

SUPERVISOR_READ_MAX_LINES: Final[int] = 500
EXPERT_READ_MAX_LINES: Final[int] = 100
SEARCH_MAX_RESULTS: Final[int] = 20


def supervisor_read_code(
    project_root: Path,
    relative_path: str,
    *,
    allowed_paths: Collection[str],
    start_line: int = 1,
    max_lines: int = SUPERVISOR_READ_MAX_LINES,
) -> str:
    return _read_code_window(
        project_root=project_root,
        relative_path=relative_path,
        allowed_paths=allowed_paths,
        start_line=start_line,
        max_lines=max_lines,
        hard_limit=SUPERVISOR_READ_MAX_LINES,
    )


def expert_read_code(
    project_root: Path,
    relative_path: str,
    *,
    allowed_paths: Collection[str],
    start_line: int = 1,
    max_lines: int = EXPERT_READ_MAX_LINES,
) -> str:
    return _read_code_window(
        project_root=project_root,
        relative_path=relative_path,
        allowed_paths=allowed_paths,
        start_line=start_line,
        max_lines=max_lines,
        hard_limit=EXPERT_READ_MAX_LINES,
    )


def search_code(
    project_root: Path,
    *,
    allowed_paths: Collection[str],
    pattern: str,
    file_glob: str | None = None,
    max_results: int = SEARCH_MAX_RESULTS,
) -> str:
    if not pattern or not pattern.strip():
        raise ValueError("Search pattern must be non-empty.")

    if max_results < 1 or max_results > SEARCH_MAX_RESULTS:
        raise ValueError(
            f"max_results must be between 1 and {SEARCH_MAX_RESULTS}; got {max_results}"
        )

    try:
        compiled_pattern = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid search pattern: {pattern!r}") from exc

    root = project_root.resolve()
    normalized_allowed_paths = sorted(_normalize_allowed_paths(allowed_paths))
    matches: list[str] = []

    for relative_path in normalized_allowed_paths:
        if file_glob and not fnmatch.fnmatch(relative_path, file_glob):
            continue

        file_path, normalized_relative_path = _resolve_in_scope_file(
            project_root=root,
            relative_path=relative_path,
            allowed_paths=normalized_allowed_paths,
        )
        analysis_source = build_analysis_source(
            path=file_path,
            relative_path=normalized_relative_path,
        )

        for line_number, line in enumerate(
            analysis_source.analysis_text.splitlines(),
            start=1,
        ):
            if not compiled_pattern.search(line):
                continue

            matches.append(f"{normalized_relative_path}:{line_number}: {line}")
            if len(matches) >= max_results:
                return "\n".join(matches)

    if not matches:
        return "No matches found."

    return "\n".join(matches)


def _read_code_window(
    *,
    project_root: Path,
    relative_path: str,
    allowed_paths: Collection[str],
    start_line: int,
    max_lines: int,
    hard_limit: int,
) -> str:
    if start_line < 1:
        raise ValueError(f"start_line must be >= 1; got {start_line}")
    if max_lines < 1 or max_lines > hard_limit:
        raise ValueError(
            f"max_lines must be between 1 and {hard_limit}; got {max_lines}"
        )

    file_path, normalized_relative_path = _resolve_in_scope_file(
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

    return "\n".join(
        f"{line_number:4}: {line}"
        for line_number, line in enumerate(selected_lines, start=start_line)
    )


def _resolve_in_scope_file(
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
    normalized_allowed_paths = _normalize_allowed_paths(allowed_paths)

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


def _normalize_allowed_paths(allowed_paths: Collection[str]) -> set[str]:
    normalized: set[str] = set()

    for path in allowed_paths:
        cleaned = path.strip()
        if not cleaned:
            continue
        normalized.add(Path(cleaned).as_posix())

    return normalized
