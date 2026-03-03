from pathlib import Path

from scout_agent.runtime.audit.tools import (
    expert_read_code,
    search_code,
    supervisor_read_code,
)


def test_audit_tools_ignore_inline_test_code(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    file_path = src / "lib.rs"
    file_path.write_text(
        "pub fn prod() {\n"
        "    let value = 1;\n"
        "}\n"
        "\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn unit_test() {\n"
        "        prod();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    allowed = {"src/lib.rs"}

    supervisor_view = supervisor_read_code(
        tmp_path,
        "src/lib.rs",
        allowed_paths=allowed,
        start_line=1,
        max_lines=20,
    )
    expert_view = expert_read_code(
        tmp_path,
        "src/lib.rs",
        allowed_paths=allowed,
        start_line=1,
        max_lines=20,
    )
    search_results = search_code(
        tmp_path,
        allowed_paths=allowed,
        pattern=r"unit_test|prod\(\)",
    )

    assert "unit_test" not in supervisor_view
    assert "unit_test" not in expert_view
    assert "src/lib.rs:1: pub fn prod() {" in search_results
    assert "src/lib.rs:9:         prod();" not in search_results
