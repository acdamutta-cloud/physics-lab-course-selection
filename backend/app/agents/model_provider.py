from langchain_openai import ChatOpenAI

from app.core.config.settings import get_settings


def get_chat_model() -> ChatOpenAI | None:
    """Return the configured chat model; mock mode stays deterministic."""

    settings = get_settings()
    if settings.model_provider == "mock":
        return None
    settings.validate_runtime_secrets()
    assert settings.deepseek_api_key is not None
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_base_url,
        temperature=settings.deepseek_temperature,
        max_tokens=settings.deepseek_max_tokens,
        timeout=settings.deepseek_timeout_seconds,
        max_retries=settings.deepseek_max_retries,
    )
