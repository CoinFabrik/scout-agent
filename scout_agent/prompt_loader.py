from __future__ import annotations

from functools import cache
from pathlib import Path


@cache
def load_prompt_asset(
    prompts_dir: Path,
    file_name: str,
    *,
    preserve_trailing_newline: bool = False,
    empty_error_label: str = "Prompt asset",
) -> str:
    prompt_path = prompts_dir / file_name
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not preserve_trailing_newline:
        prompt_text = prompt_text.removesuffix("\n")
    if not prompt_text.strip():
        raise ValueError(f"{empty_error_label} is empty: {prompt_path}")
    return prompt_text


@cache
def load_optional_prompt_asset(
    prompts_dir: Path,
    file_name: str,
    *,
    preserve_trailing_newline: bool = False,
    empty_error_label: str = "Prompt asset",
) -> str:
    if not file_name.strip():
        return ""
    prompt_path = prompts_dir / file_name
    if not prompt_path.exists():
        return ""
    return load_prompt_asset(
        prompts_dir,
        file_name,
        preserve_trailing_newline=preserve_trailing_newline,
        empty_error_label=empty_error_label,
    )
