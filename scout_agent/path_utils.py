from __future__ import annotations

from pathlib import Path


def validate_project_root(project_root: Path) -> None:
    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")
    if not project_root.is_dir():
        raise ValueError(f"Project root must be a directory: {project_root}")
