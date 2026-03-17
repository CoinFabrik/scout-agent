from __future__ import annotations


def append_extra_prompt(base_prompt: str, extra_prompt: str | None) -> str:
    if extra_prompt is None or not extra_prompt.strip():
        return base_prompt

    return f"{base_prompt}\n\nAdditional audit instructions:\n{extra_prompt.strip()}\n"
