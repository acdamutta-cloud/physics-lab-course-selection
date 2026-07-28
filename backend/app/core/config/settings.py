from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用运行配置。

    开发环境默认从 backend/.env 读取。生产环境应通过环境变量或密钥服务注入。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "物理实验智能排课系统"
    app_version: str = "0.1.0"
    app_env: Literal["development", "testing", "production"] = "development"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    timezone: str = "Asia/Shanghai"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    database_url: str = (
        "postgresql+asyncpg://physics_lab:physics_lab_dev_password"
        "@127.0.0.1:5432/physics_lab"
    )
    langgraph_database_url: str = (
        "postgresql://physics_lab:physics_lab_dev_password"
        "@127.0.0.1:5432/physics_lab"
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1)
    jwt_refresh_token_expire_days: int = Field(default=7, ge=1)

    model_provider: Literal["mock", "deepseek"] = "mock"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout_seconds: float = Field(default=120, gt=0)
    deepseek_max_retries: int = Field(default=2, ge=0, le=10)
    deepseek_temperature: float = Field(default=0.1, ge=0, le=2)
    deepseek_max_tokens: int = Field(default=4096, ge=1)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = False
    log_dir: str = "logs"

    def validate_runtime_secrets(self) -> None:
        """在需要真实外部服务时检查密钥，避免模块导入阶段直接失败。"""

        has_deepseek_key = bool(
            self.deepseek_api_key
            and self.deepseek_api_key.get_secret_value().strip()
        )
        if self.model_provider == "deepseek" and not has_deepseek_key:
            raise ValueError(
                "MODEL_PROVIDER=deepseek 时必须通过环境变量注入 DEEPSEEK_API_KEY"
            )
        jwt_secret = self.jwt_secret_key.get_secret_value().strip()
        if self.app_env == "production" and not jwt_secret:
            raise ValueError("生产环境必须设置安全的 JWT_SECRET_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
