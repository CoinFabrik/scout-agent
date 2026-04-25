from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from scout_agent.rust_analysis.discovery import DiscoveredRustFile, discover_rust_files


def discover_in_scope_files_or_raise(
    *,
    project_root: Path,
    configured_paths: Collection[str] | None = None,
) -> list[DiscoveredRustFile]:
    resolved_project_root = project_root.resolve()
    discovered_files = discover_rust_files(
        resolved_project_root,
        configured_paths=configured_paths,
    )
    if not discovered_files:
        raise ValueError(
            "No in-scope production Rust source files were discovered under "
            f"{resolved_project_root}"
        )
    return discovered_files
