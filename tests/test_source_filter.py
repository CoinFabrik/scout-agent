from pathlib import Path

from scout_agent.runtime.source.rust_parser import parse_rust_source
from scout_agent.runtime.source.source_filter import (
    build_analysis_source,
    is_test_rust_path,
    sanitize_rust_source_for_analysis,
)


def test_is_test_rust_path_matches_only_dedicated_test_paths() -> None:
    assert is_test_rust_path("tests/integration.rs") is True
    assert is_test_rust_path("src/tests.rs") is True
    assert is_test_rust_path("src/foo_test.rs") is True
    assert is_test_rust_path("src/test_helpers.rs") is True

    assert is_test_rust_path("examples/demo.rs") is False
    assert is_test_rust_path("benches/bench.rs") is False
    assert is_test_rust_path("src/testing_support.rs") is False


def test_sanitize_rust_source_strips_cfg_test_module_and_preserves_line_count() -> None:
    source_text = (
        "pub fn prod() {}\n"
        "\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn unit() {}\n"
        "}\n"
        "\n"
        "pub fn prod_two() {}\n"
    )

    sanitized = sanitize_rust_source_for_analysis(
        source_text,
        relative_path="src/lib.rs",
    )

    assert len(sanitized.splitlines()) == len(source_text.splitlines())
    assert "mod tests" not in sanitized
    assert "fn unit()" not in sanitized
    assert "pub fn prod()" in sanitized
    assert "pub fn prod_two()" in sanitized

    parsed = parse_rust_source(sanitized.encode("utf-8"), relative_path="src/lib.rs")
    assert [(fn.name, fn.line_start) for fn in parsed.functions] == [
        ("prod", 1),
        ("prod_two", 9),
    ]


def test_sanitize_rust_source_strips_test_function_but_keeps_non_test_functions() -> (
    None
):
    source_text = "pub fn prod() {}\n\n#[test]\nfn unit_test() {}\n\nfn helper() {}\n"

    sanitized = sanitize_rust_source_for_analysis(
        source_text,
        relative_path="src/lib.rs",
    )

    assert "fn unit_test()" not in sanitized
    assert "fn helper()" in sanitized

    parsed = parse_rust_source(sanitized.encode("utf-8"), relative_path="src/lib.rs")
    assert [fn.name for fn in parsed.functions] == ["prod", "helper"]


def test_build_analysis_source_hash_ignores_inline_test_only_changes(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "lib.rs"
    file_path.write_text(
        "pub fn prod() {}\n\n#[cfg(test)]\nmod tests {\n    fn a() {}\n}\n",
        encoding="utf-8",
    )
    first = build_analysis_source(
        path=file_path,
        relative_path="src/lib.rs",
    )

    file_path.write_text(
        "pub fn prod() {}\n\n#[cfg(test)]\nmod tests {\n    fn changed() {}\n    fn also_changed() {}\n}\n",
        encoding="utf-8",
    )
    second = build_analysis_source(
        path=file_path,
        relative_path="src/lib.rs",
    )

    assert first.content_sha256 == second.content_sha256
