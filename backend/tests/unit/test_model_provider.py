from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.agents.model_provider import get_chat_model
from app.core.config.settings import Settings


def test_huggingface_provider_requires_hf_token() -> None:
    settings = Settings(
        model_provider="huggingface",
        hf_token=None,
        postgres_password=SecretStr("test-password"),
    )

    with pytest.raises(ValueError, match="HF_TOKEN"):
        settings.validate_runtime_secrets()


def test_deepseek_provider_still_requires_deepseek_key() -> None:
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key=None,
        postgres_password=SecretStr("test-password"),
    )

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        settings.validate_runtime_secrets()


def test_dashscope_provider_requires_api_key() -> None:
    settings = Settings(
        model_provider="dashscope",
        dashscope_api_key=None,
        postgres_password=SecretStr("test-password"),
    )

    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        settings.validate_runtime_secrets()


def test_mock_provider_still_returns_no_chat_model() -> None:
    with patch(
        "app.agents.model_provider.get_settings",
        return_value=SimpleNamespace(model_provider="mock"),
    ):
        assert get_chat_model() is None


def test_huggingface_provider_builds_openai_compatible_chat_model() -> None:
    settings = Settings(
        model_provider="huggingface",
        hf_token=SecretStr("test-hf-token"),
        postgres_password=SecretStr("test-password"),
    )

    with (
        patch("app.agents.model_provider.get_settings", return_value=settings),
        patch("app.agents.model_provider.ChatOpenAI") as chat_openai,
    ):
        get_chat_model()

    kwargs = chat_openai.call_args.kwargs
    assert kwargs["model"] == settings.huggingface_model
    assert kwargs["api_key"] == "test-hf-token"
    assert kwargs["base_url"] == settings.huggingface_base_url


def test_deepseek_provider_remains_available() -> None:
    settings = Settings(
        model_provider="deepseek",
        deepseek_api_key=SecretStr("test-deepseek-key"),
        postgres_password=SecretStr("test-password"),
    )

    with (
        patch("app.agents.model_provider.get_settings", return_value=settings),
        patch("app.agents.model_provider.ChatOpenAI") as chat_openai,
    ):
        get_chat_model()

    kwargs = chat_openai.call_args.kwargs
    assert kwargs["model"] == settings.deepseek_model
    assert kwargs["api_key"] == "test-deepseek-key"
    assert kwargs["base_url"] == settings.deepseek_base_url


def test_dashscope_provider_builds_qwen3_14b_without_thinking_output() -> None:
    settings = Settings(
        model_provider="dashscope",
        dashscope_api_key=SecretStr("test-dashscope-key"),
        postgres_password=SecretStr("test-password"),
    )

    with (
        patch("app.agents.model_provider.get_settings", return_value=settings),
        patch("app.agents.model_provider.ChatOpenAI") as chat_openai,
    ):
        get_chat_model()

    kwargs = chat_openai.call_args.kwargs
    assert kwargs["model"] == "qwen3-14b"
    assert kwargs["api_key"] == "test-dashscope-key"
    assert kwargs["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert kwargs["extra_body"] == {"enable_thinking": False}
