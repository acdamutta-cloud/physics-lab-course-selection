from langchain_openai import ChatOpenAI

from app.core.config.settings import get_settings


def provider_failure_message(error: Exception) -> str | None:
    """Return a student-facing message for provider failures, including wrapped ones."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        error_name = type(current).__name__
        if error_name in {"APIConnectionError", "ConnectError"}:
            return "AI服务连接暂时失败，请稍后重试。"
        if error_name in {"APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
            return "AI服务响应超时，请稍后重试。"
        if error_name == "RateLimitError":
            return "AI服务当前请求较多，请稍后重试。"
        current = current.__cause__ or current.__context__
    return None


def get_chat_model() -> ChatOpenAI:
    """Return the configured chat model."""

    settings = get_settings()
    settings.validate_runtime_secrets()
    extra_body = None
    if settings.model_provider == "huggingface":
        assert settings.hf_token is not None
        model = settings.huggingface_model
        api_key = settings.hf_token.get_secret_value()
        base_url = settings.huggingface_base_url
    elif settings.model_provider == "dashscope":
        assert settings.dashscope_api_key is not None
        model = settings.dashscope_model
        api_key = settings.dashscope_api_key.get_secret_value()
        base_url = settings.dashscope_base_url
        extra_body = {"enable_thinking": settings.dashscope_enable_thinking}
    else:
        assert settings.deepseek_api_key is not None
        model = settings.deepseek_model
        api_key = settings.deepseek_api_key.get_secret_value()
        base_url = settings.deepseek_base_url
    model_options = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": settings.deepseek_temperature,
        "max_tokens": settings.deepseek_max_tokens,
        "timeout": settings.deepseek_timeout_seconds,
        "max_retries": settings.deepseek_max_retries,
    }
    if extra_body is not None:
        model_options["extra_body"] = extra_body
    return ChatOpenAI(**model_options)
