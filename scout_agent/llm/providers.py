import os
from collections.abc import Mapping
from dataclasses import dataclass

from scout_agent.llm.model_config import get_model_kwargs



DEFAULT_SDK_MAX_RETRIES = 2


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    credential_env: str


@dataclass(frozen=True, slots=True)
class ProviderMatch:
    provider: Provider
    model_name: str


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(name="openai", credential_env="OPENAI_API_KEY"),
    "anthropic": Provider(name="anthropic", credential_env="ANTHROPIC_API_KEY"),
    "gemini": Provider(name="gemini", credential_env="GOOGLE_API_KEY"),
}


class ProviderError(ValueError):
    pass


def infer_provider(model_name: str) -> ProviderMatch:
    if not model_name or not model_name.strip():
        raise ProviderError("Model name must be non-empty.")

    raw = model_name.strip()
    if ":" not in raw:
        raise ProviderError(
            "Model must use the form 'provider:model', "
            "for example 'anthropic:claude-sonnet-4-5'."
        )

    maybe_provider, candidate_model = raw.split(":", 1)
    provider_key = maybe_provider.strip().lower()
    normalized_model = candidate_model.strip()

    if provider_key not in PROVIDERS:
        raise ProviderError(
            f"Unknown provider prefix {maybe_provider!r}. "
            f"Supported providers: {', '.join(sorted(PROVIDERS))}."
        )

    if not normalized_model:
        raise ProviderError(
            "Model must include a non-empty model after the provider prefix."
        )

    return ProviderMatch(
        provider=PROVIDERS[provider_key],
        model_name=normalized_model,
    )


def resolve_model_identifier(model_name: str) -> str:
    match = infer_provider(model_name)
    return f"{match.provider.name}:{match.model_name}"


def is_gemini_model(model_name: str) -> bool:
    return infer_provider(model_name).provider.name == "gemini"


def get_api_key(
    provider: Provider,
    env: Mapping[str, str] | None = None,
) -> str:
    resolved_env = os.environ if env is None else env
    api_key = resolved_env.get(provider.credential_env)

    if api_key and api_key.strip():
        return api_key.strip()

    raise ProviderError(
        f"Missing credentials for provider '{provider.name}'. "
        f"Set {provider.credential_env}."
    )


def build_chat_model(model_name: str, llm_mode: str):
    match = infer_provider(model_name)
    kwargs = get_model_kwargs(match.provider.name, match.model_name, llm_mode)
    kwargs.setdefault("max_retries", DEFAULT_SDK_MAX_RETRIES)
    kwargs.setdefault("streaming", False)

    if match.provider.name == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ProviderError("Missing langchain-openai dependency.") from exc
        return ChatOpenAI(
            model=match.model_name,
            use_responses_api=True,
            api_key=get_api_key(match.provider),
            **kwargs,
        )

    if match.provider.name == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise ProviderError("Missing langchain-anthropic dependency.") from exc

        return ChatAnthropic(
            model=match.model_name,
            api_key=get_api_key(match.provider),
            **kwargs,
        )

    if match.provider.name == "gemini":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            raise ProviderError("Missing langchain-google-genai dependency.") from exc

        return ChatGoogleGenerativeAI(
            model=match.model_name,
            google_api_key=get_api_key(match.provider),
            **kwargs,
        )

    raise ProviderError(f"Unsupported provider '{match.provider.name}'.")
