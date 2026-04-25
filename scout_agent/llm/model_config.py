from __future__ import annotations

from typing import Any, Final

from scout_agent.config.llm_modes import DEFAULT_LLM_MODE


DEFAULT_SEED: Final[int] = 42

# Keep model-family matching coarse and conservative. OpenAI parameter support
# changes across model families, so unknown families should receive no defaults.
#
# This project currently routes OpenAI calls through ChatOpenAI, which uses the
# Chat Completions surface. GPT-5.4 tool flows with reasoning must use the
# Responses API, so only the GPT-5.1 line gets the automatic reasoning default
# on this transport.
OPENAI_REASONING_MODEL_PREFIXES: Final[tuple[str, ...]] = (
    "gpt-5.4",
    "o1",
    "o3",
    "o4",
)
OPENAI_DETERMINISTIC_MODEL_PREFIXES: Final[tuple[str, ...]] = ("gpt-4.1",)


def openai_reasoning_conf(
    *,
    reasoning_effort: str = "xhigh",
    **overrides: Any,
) -> dict[str, Any]:
    return {
        "reasoning_effort": reasoning_effort,
        **overrides,
    }


def openai_deterministic_conf(
    *,
    seed: int = DEFAULT_SEED,
    temperature: float = 0.0,
    top_p: float = 1.0,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
    **overrides: Any,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "temperature": temperature,
        "top_p": top_p,
        "presence_penalty": presence_penalty,
        "frequency_penalty": frequency_penalty,
        **overrides,
    }


def anthropic_consistent_conf(**overrides: Any) -> dict[str, Any]:
    return {
        "temperature": 0.0,
        **overrides,
    }


def gemini_consistent_conf(**overrides: Any) -> dict[str, Any]:
    return {
        "temperature": 0.0,
        "top_k": 1,
        **overrides,
    }


def get_model_kwargs(provider: str, model_name: str, llm_mode: str) -> dict[str, Any]:
    if llm_mode != DEFAULT_LLM_MODE:
        return {}

    normalized_provider = provider.strip().lower()
    normalized_model = model_name.strip().lower()

    if normalized_provider == "openai":
        return _openai_kwargs_for_model(normalized_model)

    if normalized_provider == "anthropic":
        return anthropic_consistent_conf()

    if normalized_provider == "gemini":
        return gemini_consistent_conf()

    return {}


def _openai_kwargs_for_model(model_name: str) -> dict[str, Any]:
    if model_name.startswith(OPENAI_DETERMINISTIC_MODEL_PREFIXES):
        return openai_deterministic_conf()

    if model_name.startswith(OPENAI_REASONING_MODEL_PREFIXES):
        return openai_reasoning_conf()

    return {}
