from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Collection, Final, Sequence

from scout_agent.runtime.source.source_filter import (
    build_analysis_source,
    is_test_rust_path,
)

DEFAULT_EXCLUDED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "target",
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredRustFile:
    absolute_path: Path
    relative_path: str
    content_sha256: str


def discover_rust_files(
    project_root: Path,
    excluded_dir_names: Collection[str] | None = None,
    configured_paths: Collection[str] | None = None,
) -> list[DiscoveredRustFile]:
    _validate_project_root(project_root)

    excluded = set(DEFAULT_EXCLUDED_DIR_NAMES)
    if excluded_dir_names is not None:
        excluded.update(name for name in excluded_dir_names if name)

    if configured_paths:
        discovered = _discover_configured_rust_files(
            root=project_root,
            configured_paths=configured_paths,
            excluded_dir_names=excluded,
        )
        discovered.sort(key=lambda item: item.relative_path)
        return discovered

    discovered: list[DiscoveredRustFile] = []

    for current_root, dir_names, file_names in os.walk(
        project_root, topdown=True, followlinks=False
    ):
        current_path = Path(current_root)

        dir_names[:] = sorted(
            directory_name
            for directory_name in dir_names
            if directory_name not in excluded
            and not (current_path / directory_name).is_symlink()
        )

        for file_name in sorted(file_names):
            candidate = current_path / file_name

            if candidate.suffix != ".rs":
                continue
            if candidate.is_symlink():
                continue
            if not candidate.is_file():
                continue

            relative_path = candidate.relative_to(project_root).as_posix()
            if is_test_rust_path(relative_path):
                continue

            analysis_source = build_analysis_source(
                path=candidate,
                relative_path=relative_path,
            )
            discovered.append(
                DiscoveredRustFile(
                    absolute_path=candidate,
                    relative_path=relative_path,
                    content_sha256=analysis_source.content_sha256,
                )
            )

    discovered.sort(key=lambda item: item.relative_path)
    return discovered


def _discover_configured_rust_files(
    *,
    root: Path,
    configured_paths: Collection[str],
    excluded_dir_names: Collection[str],
) -> list[DiscoveredRustFile]:
    discovered: dict[str, DiscoveredRustFile] = {}

    for configured_path in configured_paths:
        raw = Path(configured_path).expanduser()
        candidate = (
            raw.resolve(strict=False)
            if raw.is_absolute()
            else (root / raw).resolve(strict=False)
        )

        if candidate != root and root not in candidate.parents:
            raise ValueError(
                f"Configured scout.json path escapes project root: {configured_path}"
            )
        if not candidate.exists():
            raise FileNotFoundError(
                f"Configured scout.json path does not exist: {configured_path}"
            )
        if candidate.is_symlink():
            raise ValueError(
                f"Refusing to process symlinked scout.json path: {configured_path}"
            )

        if candidate.is_dir():
            for current_root, dir_names, file_names in os.walk(
                candidate, topdown=True, followlinks=False
            ):
                current_path = Path(current_root)
                dir_names[:] = sorted(
                    directory_name
                    for directory_name in dir_names
                    if directory_name not in excluded_dir_names
                    and not (current_path / directory_name).is_symlink()
                )
                for file_name in sorted(file_names):
                    maybe_file = current_path / file_name
                    if maybe_file.suffix != ".rs":
                        continue
                    if maybe_file.is_symlink() or not maybe_file.is_file():
                        continue
                    relative_path = maybe_file.relative_to(root).as_posix()
                    if is_test_rust_path(relative_path):
                        continue
                    analysis_source = build_analysis_source(
                        path=maybe_file,
                        relative_path=relative_path,
                    )
                    discovered[relative_path] = DiscoveredRustFile(
                        absolute_path=maybe_file,
                        relative_path=relative_path,
                        content_sha256=analysis_source.content_sha256,
                    )
            continue

        if candidate.suffix != ".rs":
            raise ValueError(
                f"Configured scout.json path must point to a Rust file or directory: {configured_path}"
            )
        if not candidate.is_file():
            raise ValueError(
                f"Configured scout.json path must be a file or directory: {configured_path}"
            )

        relative_path = candidate.relative_to(root).as_posix()
        if is_test_rust_path(relative_path):
            continue

        analysis_source = build_analysis_source(
            path=candidate,
            relative_path=relative_path,
        )
        discovered[relative_path] = DiscoveredRustFile(
            absolute_path=candidate,
            relative_path=relative_path,
            content_sha256=analysis_source.content_sha256,
        )

    return list(discovered.values())


def compute_scope_fingerprint(files: Sequence[DiscoveredRustFile]) -> str:
    digest = sha256()

    for source_file in sorted(files, key=lambda item: item.relative_path):
        digest.update(source_file.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source_file.content_sha256.encode("utf-8"))
        digest.update(b"\n")

    return digest.hexdigest()


def _validate_project_root(project_root: Path) -> None:
    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")
    if not project_root.is_dir():
        raise ValueError(f"Project root must be a directory: {project_root}")
