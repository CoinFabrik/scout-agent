from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "scout_agent"


def test_feature_and_config_packages_do_not_import_cli() -> None:
    for package_name in ("audit", "extract", "rust_analysis", "config"):
        offenders = _imports_matching(
            PACKAGE_ROOT / package_name,
            forbidden_package="scout_agent.cli",
        )
        assert offenders == []


def test_extract_does_not_import_config() -> None:
    offenders = _imports_matching(
        PACKAGE_ROOT / "extract",
        forbidden_package="scout_agent.config",
    )
    assert offenders == []


def test_audit_graph_does_not_import_cli() -> None:
    offenders = _imports_matching(
        PACKAGE_ROOT / "audit" / "graph",
        forbidden_package="scout_agent.cli",
    )
    assert offenders == []


def test_domain_only_imports_domain_modules() -> None:
    offenders: list[str] = []
    for path in _python_files(PACKAGE_ROOT / "domain"):
        current_module = _module_name(path)
        for imported_module in _imported_modules(path, current_module=current_module):
            if imported_module == "scout_agent" or (
                imported_module.startswith("scout_agent.")
                and not imported_module.startswith("scout_agent.domain.")
                and imported_module != "scout_agent.domain"
            ):
                offenders.append(f"{path.relative_to(ROOT)} imports {imported_module}")

    assert offenders == []


def _imports_matching(path: Path, *, forbidden_package: str) -> list[str]:
    offenders: list[str] = []
    for python_file in _python_files(path):
        current_module = _module_name(python_file)
        for imported_module in _imported_modules(
            python_file,
            current_module=current_module,
        ):
            if imported_module == forbidden_package or imported_module.startswith(
                f"{forbidden_package}."
            ):
                offenders.append(
                    f"{python_file.relative_to(ROOT)} imports {imported_module}"
                )

    return offenders


def _python_files(path: Path) -> list[Path]:
    return sorted(
        python_file
        for python_file in path.rglob("*.py")
        if "__pycache__" not in python_file.parts
    )


def _module_name(path: Path) -> str:
    relative_path = path.relative_to(ROOT).with_suffix("")
    return ".".join(relative_path.parts)


def _imported_modules(path: Path, *, current_module: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_name = _resolve_import_from(
                current_module=current_module,
                level=node.level,
                module=node.module,
            )
            if module_name is not None:
                imported_modules.append(module_name)
                imported_modules.extend(
                    f"{module_name}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                )

    return imported_modules


def _resolve_import_from(
    *,
    current_module: str,
    level: int,
    module: str | None,
) -> str | None:
    if level == 0:
        return module

    current_package_parts = current_module.split(".")[:-1]
    drop_count = level - 1
    if drop_count > len(current_package_parts):
        return module

    resolved_parts = current_package_parts[: len(current_package_parts) - drop_count]
    if module:
        resolved_parts.extend(module.split("."))

    return ".".join(resolved_parts) if resolved_parts else module
