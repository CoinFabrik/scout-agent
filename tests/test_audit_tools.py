from __future__ import annotations

from pathlib import Path

import pytest

from scout_agent.runtime.audit.tools import read_sanitized_code_chunk


def test_read_sanitized_code_chunk_ignores_inline_test_code(tmp_path: Path) -> None:
    source = tmp_path / "contracts"
    source.mkdir()
    file_path = source / "gateway.rs"
    file_path.write_text(
        "pub fn prod() {}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn unit_test() {}\n}\n",
        encoding="utf-8",
    )

    chunk = read_sanitized_code_chunk(
        tmp_path,
        "contracts/gateway.rs",
        allowed_paths=["contracts/gateway.rs"],
        start_line=1,
        max_lines=20,
    )

    assert "prod()" in chunk
    assert "unit_test" not in chunk


def test_read_sanitized_code_chunk_rejects_large_windows(tmp_path: Path) -> None:
    source = tmp_path / "contracts"
    source.mkdir()
    (source / "gateway.rs").write_text("pub fn prod() {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="max_lines must be between 1 and 100"):
        read_sanitized_code_chunk(
            tmp_path,
            "contracts/gateway.rs",
            allowed_paths=["contracts/gateway.rs"],
            start_line=1,
            max_lines=101,
        )
