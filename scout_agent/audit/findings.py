from pathlib import Path
from scout_agent.domain.audit import Finding


def relativize_findings(
    findings: list[Finding],
    project_root: Path,
) -> list[Finding]:
    for finding in findings:
        finding.location = _relativize_location(finding.location, project_root)
    return findings


def _relativize_location(location: str, project_root: Path) -> str:
    if ":" not in location:
        return location

    parts = location.rsplit(":", 1)
    path_part = parts[0]
    line_part = parts[1]

    try:
        path = Path(path_part).expanduser()
        if path.is_absolute() and path.is_relative_to(project_root):
            rel_path = path.relative_to(project_root).as_posix()
            return f"{rel_path}:{line_part}"
    except (ValueError, RuntimeError):
        pass

    return location
