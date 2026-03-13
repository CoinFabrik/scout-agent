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


class RepoScopedAuditBackend(FilesystemBackend):
    """Policy wrapper that exposes only a configured set of in-scope files."""

    def __init__(
        self,
        *,
        root_dir: Path,
        virtual_mode: bool = False,
        allowed_paths: set[str],
        **kwargs: Any,
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode, **kwargs)
        self.allowed_paths = {
            _normalize_backend_relative_path(path) for path in allowed_paths
        }

    def _normalize_repo_path(self, file_path: str | None) -> str:
        if file_path is None:
            return ""

        cleaned_path = file_path.strip()
        if cleaned_path in ("", "/"):
            return ""
        return _normalize_backend_relative_path(cleaned_path)

    def _path_is_allowed(
        self,
        normalized_path: str,
        *,
        allow_directory: bool,
    ) -> bool:
        if normalized_path == "":
            return allow_directory
        if normalized_path in self.allowed_paths:
            return True
        if not allow_directory:
            return False
        return any(path.startswith(f"{normalized_path}/") for path in self.allowed_paths)

    def _canonicalize_allowed_path(
        self,
        file_path: str | None,
        *,
        error_prefix: str,
        allow_directory: bool,
    ) -> str:
        try:
            normalized_path = self._normalize_repo_path(file_path)
        except ValueError as exc:
            raise ValueError(f"{error_prefix} for '{file_path}'.") from exc

        if not self._path_is_allowed(
            normalized_path,
            allow_directory=allow_directory,
        ):
            raise ValueError(f"{error_prefix} for '{file_path}'.")
        if normalized_path == "":
            return "/"
        return f"/{normalized_path}"

    def _guarded_call(
        self,
        file_path: str | None,
        *,
        error_prefix: str,
        allow_directory: bool,
        fallback: Any,
        delegate_fn: Callable[[str], Any],
    ) -> Any:
        try:
            canonical_path = self._canonicalize_allowed_path(
                file_path,
                error_prefix=error_prefix,
                allow_directory=allow_directory,
            )
        except ValueError as exc:
            return fallback(exc) if callable(fallback) else fallback

        try:
            return delegate_fn(canonical_path)
        except (ValueError, FileNotFoundError) as exc:
            return fallback(exc) if callable(fallback) else fallback

    def ls_info(self, path: str) -> list[FileInfo]:
        backend_ls_info = super(RepoScopedAuditBackend, self).ls_info
        results = self._guarded_call(
            path,
            error_prefix="List access denied",
            allow_directory=True,
            fallback=[],
            delegate_fn=backend_ls_info,
        )
        if not isinstance(results, list):
            return []
        return [
            item
            for item in results
            if self._path_is_allowed(
                self._normalize_repo_path(getattr(item, "path", "")),
                allow_directory=True,
            )
        ]

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> Any:
        backend_read = super(RepoScopedAuditBackend, self).read
        return self._guarded_call(
            file_path,
            error_prefix="Read access denied",
            allow_directory=False,
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
        backend_grep_raw = super(RepoScopedAuditBackend, self).grep_raw
        requested_path = "/" if path is None else path
        return self._guarded_call(
            requested_path,
            error_prefix="Grep access denied",
            allow_directory=True,
            fallback=lambda exc: f"Error: {exc}",
            delegate_fn=lambda canonical_path: backend_grep_raw(
                pattern,
                path=canonical_path,
                glob=glob,
            ),
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        backend_glob_info = super(RepoScopedAuditBackend, self).glob_info
        results = self._guarded_call(
            path,
            error_prefix="Glob access denied",
            allow_directory=True,
            fallback=[],
            delegate_fn=lambda canonical_path: backend_glob_info(
                pattern,
                path=canonical_path,
            ),
        )
        if not isinstance(results, list):
            return []
        return [
            item
            for item in results
            if self._path_is_allowed(
                self._normalize_repo_path(getattr(item, "path", "")),
                allow_directory=False,
            )
        ]

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return EditResult(error="Edits are not allowed")
