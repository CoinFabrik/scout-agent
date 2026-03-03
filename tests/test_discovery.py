from pathlib import Path

from scout_agent.runtime.source.discovery import compute_scope_fingerprint, discover_rust_files
from scout_agent.runtime.source.source_filter import build_analysis_source


def test_discovery_excludes_known_directories(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "gateway.rs").write_text("pub fn gateway() {}\n", encoding="utf-8")

    target = tmp_path / "target"
    target.mkdir()
    (target / "ignored.rs").write_text("pub fn ignored() {}\n", encoding="utf-8")

    discovered = discover_rust_files(tmp_path)

    assert [item.relative_path for item in discovered] == ["contracts/gateway.rs"]


def test_discovery_fingerprint_changes_when_file_changes(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    file_path = src / "lib.rs"
    file_path.write_text("pub fn first() {}\n", encoding="utf-8")

    first = compute_scope_fingerprint(discover_rust_files(tmp_path))

    file_path.write_text("pub fn second() {}\n", encoding="utf-8")
    second = compute_scope_fingerprint(discover_rust_files(tmp_path))

    assert first != second


def test_discovery_can_be_scoped_by_configured_paths(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "gateway.rs").write_text("pub fn gateway() {}\n", encoding="utf-8")
    (contracts / "risk.rs").write_text("pub fn risk() {}\n", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn lib() {}\n", encoding="utf-8")

    discovered = discover_rust_files(
        tmp_path,
        configured_paths=["contracts"],
    )

    assert [item.relative_path for item in discovered] == [
        "contracts/gateway.rs",
        "contracts/risk.rs",
    ]


def test_discovery_excludes_dedicated_test_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    examples = tmp_path / "examples"
    examples.mkdir()
    benches = tmp_path / "benches"
    benches.mkdir()

    (src / "lib.rs").write_text("pub fn prod() {}\n", encoding="utf-8")
    (src / "tests.rs").write_text("pub fn ignored() {}\n", encoding="utf-8")
    (src / "foo_test.rs").write_text("pub fn ignored_too() {}\n", encoding="utf-8")
    (tests_dir / "integration.rs").write_text("pub fn ignored_three() {}\n", encoding="utf-8")
    (examples / "demo.rs").write_text("pub fn demo() {}\n", encoding="utf-8")
    (benches / "bench.rs").write_text("pub fn bench() {}\n", encoding="utf-8")

    discovered = discover_rust_files(tmp_path)

    assert [item.relative_path for item in discovered] == [
        "benches/bench.rs",
        "examples/demo.rs",
        "src/lib.rs",
    ]


def test_discovery_scoped_paths_still_ignore_tests(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    (src / "lib.rs").write_text("pub fn prod() {}\n", encoding="utf-8")
    (src / "tests.rs").write_text("pub fn ignored() {}\n", encoding="utf-8")
    (tests_dir / "integration.rs").write_text("pub fn ignored_too() {}\n", encoding="utf-8")

    discovered = discover_rust_files(
        tmp_path,
        configured_paths=["src", "tests", "src/tests.rs"],
    )

    assert [item.relative_path for item in discovered] == ["src/lib.rs"]


def test_discovery_hash_ignores_inline_test_only_changes(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    file_path = src / "lib.rs"

    file_path.write_text(
        "pub fn prod() {}\n\n#[cfg(test)]\nmod tests {\n    fn a() {}\n}\n",
        encoding="utf-8",
    )
    first = build_analysis_source(
        path=file_path,
        relative_path="src/lib.rs",
    ).content_sha256

    file_path.write_text(
        "pub fn prod() {}\n\n#[cfg(test)]\nmod tests {\n    fn b() {}\n    fn c() {}\n}\n",
        encoding="utf-8",
    )
    second = build_analysis_source(
        path=file_path,
        relative_path="src/lib.rs",
    ).content_sha256

    assert first == second
