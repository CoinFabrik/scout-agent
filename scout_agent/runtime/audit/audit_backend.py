from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileInfo


def _normalize_backend_relative_path(file_path: str | None) -> str:
    if file_path is None:
        raise ValueError("File path must be non-empty.")

    cleaned_path = file_path.strip()
    if not cleaned_path:
        raise ValueError("File path must be non-empty.")

    parts: list[str] = []
    for part in PurePosixPath(cleaned_path.lstrip("/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"Path traversal is not allowed: {file_path}")
        parts.append(part)

    if not parts:
        raise ValueError("File path must be non-empty.")

    return PurePosixPath(*parts).as_posix()


class FileScopedAuditBackend(FilesystemBackend):
    """Policy wrapper that only exposes the in-scope file to parent-agent file tools."""

    def __init__(
        self,
        *,
        root_dir: Path,
        virtual_mode: bool = False,
        current_file: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode, **kwargs)
        self.current_file = _normalize_backend_relative_path(current_file)

    def _canonicalize_allowed_path(
        self,
        file_path: str | None,
        *,
        error_prefix: str,
    ) -> str:
        try:
            normalized_path = _normalize_backend_relative_path(file_path)
        except ValueError as exc:
            raise ValueError(f"{error_prefix} for '{file_path}'.") from exc
        if normalized_path != self.current_file:
            raise ValueError(f"{error_prefix} for '{file_path}'.")
        return f"/{normalized_path}"

    def _guarded_call(
        self,
        file_path: str | None,
        *,
        error_prefix: str,
        fallback: Any,
        delegate_fn: Callable[[str], Any],
    ) -> Any:
        try:
            canonical_path = self._canonicalize_allowed_path(
                file_path,
                error_prefix=error_prefix,
            )
        except ValueError as exc:
            return fallback(exc) if callable(fallback) else fallback

        try:
            return delegate_fn(canonical_path)
        except (ValueError, FileNotFoundError) as exc:
            return fallback(exc) if callable(fallback) else fallback

    def ls_info(self, path: str) -> list[FileInfo]:
        return self._guarded_call(
            path,
            error_prefix="List access denied",
            fallback=[],
            delegate_fn=super().ls_info,
        )

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> Any:
        backend_read = super(FileScopedAuditBackend, self).read
        return self._guarded_call(
            file_path,
            error_prefix="Read access denied",
            fallback=lambda exc: f"Error: {exc}",
            delegate_fn=lambda canonical_path: backend_read(
                canonical_path,
                offset=offset,
                limit=limit,
            ),
        )

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> Any:
        backend_grep_raw = super(FileScopedAuditBackend, self).grep_raw
        requested_path = self.current_file if path is None else path
        return self._guarded_call(
            requested_path,
            error_prefix="Grep access denied",
            fallback=lambda exc: f"Error: {exc}",
            delegate_fn=lambda canonical_path: backend_grep_raw(
                pattern,
                path=canonical_path,
                glob=glob,
            ),
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        backend_glob_info = super(FileScopedAuditBackend, self).glob_info
        return self._guarded_call(
            path,
            error_prefix="Glob access denied",
            fallback=[],
            delegate_fn=lambda canonical_path: backend_glob_info(
                pattern,
                path=canonical_path,
            ),
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return EditResult(error="Edits are not allowed")
