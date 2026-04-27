import os
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from scout_agent.paths import validate_project_root
from scout_agent.rust_analysis.source_filter import (
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
    relative_path: str
    analysis_text: str
    content_sha256: str


def discover_rust_files(
    project_root: Path,
    excluded_dir_names: Collection[str] | None = None,
    configured_paths: Collection[str] | None = None,
) -> list[DiscoveredRustFile]:
    root = project_root.resolve()
    validate_project_root(root)

    excluded = set(DEFAULT_EXCLUDED_DIR_NAMES)
    if excluded_dir_names is not None:
        excluded.update(name for name in excluded_dir_names if name)

    if configured_paths:
        discovered = _discover_configured_rust_files(
            root=root,
            configured_paths=configured_paths,
            excluded_dir_names=excluded,
        )
    else:
        discovered = _walk_for_rust_files(
            root=root,
            scan_dir=root,
            excluded_dir_names=excluded,
        )

    discovered.sort(key=lambda item: item.relative_path)
    return discovered


def _walk_for_rust_files(
    *,
    root: Path,
    scan_dir: Path,
    excluded_dir_names: Collection[str],
) -> list[DiscoveredRustFile]:
    discovered: list[DiscoveredRustFile] = []

    for current_root, dir_names, file_names in os.walk(
        scan_dir,
        topdown=True,
        followlinks=False,
    ):
        current_path = Path(current_root)

        dir_names[:] = sorted(
            directory_name
            for directory_name in dir_names
            if directory_name not in excluded_dir_names
            and not (current_path / directory_name).is_symlink()
        )

        for file_name in sorted(file_names):
            candidate = current_path / file_name
            if candidate.suffix != ".rs":
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue

            relative_path = candidate.relative_to(root).as_posix()
            if is_test_rust_path(relative_path):
                continue

            analysis_source = build_analysis_source(
                path=candidate,
                relative_path=relative_path,
            )
            discovered.append(
                DiscoveredRustFile(
                    relative_path=relative_path,
                    analysis_text=analysis_source.analysis_text,
                    content_sha256=analysis_source.content_sha256,
                )
            )

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
            for item in _walk_for_rust_files(
                root=root,
                scan_dir=candidate,
                excluded_dir_names=excluded_dir_names,
            ):
                discovered[item.relative_path] = item
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
            relative_path=relative_path,
            analysis_text=analysis_source.analysis_text,
            content_sha256=analysis_source.content_sha256,
        )

    return list(discovered.values())
