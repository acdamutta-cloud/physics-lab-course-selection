from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """应用运行配置。

    开发环境默认从 backend/.env 读取。生产环境应通过环境变量或密钥服务注入。
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
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

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "postgres"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "physics_lab"
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0)
    postgres_sslmode: Literal[
        "disable",
        "allow",
        "prefer",
        "require",
        "verify-ca",
        "verify-full",
    ] = "disable"

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_max_connections: int = Field(default=128, ge=16, le=1000)
    redis_pool_wait_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    redis_socket_connect_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    redis_socket_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    redis_warm_connections: int = Field(default=32, ge=0, le=256)
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    auth_profile_cache_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    auth_profile_cache_ttl_jitter_seconds: int = Field(default=600, ge=0, le=3600)
    auth_profile_cache_rebuild_lock_ms: int = Field(default=3000, ge=500, le=10000)
    auth_profile_cache_wait_ms: int = Field(default=40, ge=10, le=500)
    auth_profile_cache_wait_attempts: int = Field(default=5, ge=0, le=20)
    student_dashboard_cache_ttl_seconds: int = Field(default=1800, ge=300, le=86400)
    student_dashboard_static_cache_ttl_seconds: int = Field(
        default=21600, ge=1800, le=172800
    )
    student_bitmap_cache_ttl_seconds: int = Field(default=86400, ge=3600, le=172800)
    student_ai_context_cache_ttl_seconds: int = Field(default=1800, ge=300, le=86400)
    student_cache_ttl_jitter_seconds: int = Field(default=600, ge=0, le=3600)
    student_cache_warm_concurrency: int = Field(default=5, ge=1, le=20)
    student_cache_rebuild_lock_ms: int = Field(default=5000, ge=500, le=30000)
    student_cache_wait_ms: int = Field(default=50, ge=10, le=1000)
    student_cache_wait_attempts: int = Field(default=4, ge=0, le=20)
    student_cache_initial_refresh_delay_seconds: int = Field(
        default=3600, ge=60, le=86400
    )
    student_cache_periodic_refresh_seconds: int = Field(
        default=3600, ge=300, le=86400
    )
    student_ai_max_concurrency: int = Field(default=8, ge=1, le=1000)
    student_ai_acquire_timeout_seconds: float = Field(default=2.0, ge=0.1, le=60)
    selection_window_cache_ttl_seconds: int = Field(default=120, ge=10, le=3600)
    selection_reconcile_interval_seconds: float = Field(default=5.0, ge=1, le=300)
    selection_queue_max_length: int = Field(default=20000, ge=1000, le=1000000)
    selection_queue_worker_count: int = Field(default=16, ge=1, le=100)
    selection_queue_batch_size: int = Field(default=20, ge=1, le=200)
    selection_queue_block_ms: int = Field(default=1000, ge=100, le=10000)
    selection_queue_reservation_seconds: int = Field(default=900, ge=60, le=3600)
    selection_request_status_ttl_seconds: int = Field(
        default=86400, ge=300, le=604800
    )
    selection_context_initial_warm_delay_seconds: int = Field(
        default=3, ge=0, le=300
    )
    selection_context_missing_scan_seconds: int = Field(
        default=300, ge=30, le=3600
    )
    resource_issue_overdue_scan_seconds: int = Field(
        default=600, ge=60, le=86400
    )
    selection_context_warm_concurrency: int = Field(default=10, ge=1, le=30)

    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1)
    jwt_refresh_token_expire_days: int = Field(default=7, ge=1)

    model_provider: Literal["deepseek", "huggingface", "dashscope"] = "dashscope"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-v4-pro"
    hf_token: SecretStr | None = None
    huggingface_base_url: str = "https://router.huggingface.co/v1"
    huggingface_model: str = "Qwen/Qwen3.5-4B:featherless-ai"
    dashscope_api_key: SecretStr | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen3-14b"
    dashscope_enable_thinking: bool = False
    deepseek_timeout_seconds: float = Field(default=120, gt=0)
    deepseek_max_retries: int = Field(default=2, ge=0, le=10)
    deepseek_temperature: float = Field(default=0.1, ge=0, le=2)
    deepseek_max_tokens: int = Field(default=4096, ge=1)

    embedding_provider: Literal["disabled", "openai_compatible"] = "disabled"
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = Field(default=1024, ge=1)
    siliconflow_base_url: str | None = None
    siliconflow_api_key: SecretStr | None = None

    sms_code_ttl_seconds: int = Field(default=300, ge=60, le=600)
    sms_send_interval_seconds: int = Field(default=60, ge=30)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = False
    log_dir: str = "logs"

    @property
    def sqlalchemy_database_url(self) -> str:
        username = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password.get_secret_value())
        database = quote_plus(self.postgres_db)
        return (
            f"postgresql+asyncpg://{username}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def embedding_enabled(self) -> bool:
        return self.embedding_provider == "openai_compatible" or bool(
            self.siliconflow_api_key
            and self.siliconflow_api_key.get_secret_value().strip()
        )

    @property
    def effective_embedding_base_url(self) -> str:
        return self.siliconflow_base_url or self.embedding_base_url

    @property
    def effective_embedding_api_key(self) -> SecretStr | None:
        return self.embedding_api_key or self.siliconflow_api_key

    @property
    def sqlalchemy_connect_args(self) -> dict[str, object]:
        if self.postgres_sslmode == "disable":
            return {"ssl": False}
        return {"ssl": self.postgres_sslmode}

    @property
    def langgraph_database_url(self) -> str:
        username = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password.get_secret_value())
        database = quote_plus(self.postgres_db)
        return (
            f"postgresql://{username}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
            f"?sslmode={self.postgres_sslmode}"
        )

    def validate_runtime_secrets(self) -> None:
        """在需要真实外部服务时检查密钥，避免模块导入阶段直接失败。"""

        has_deepseek_key = bool(
            self.deepseek_api_key and self.deepseek_api_key.get_secret_value().strip()
        )
        if self.model_provider == "deepseek" and not has_deepseek_key:
            raise ValueError(
                "MODEL_PROVIDER=deepseek 时必须通过环境变量注入 DEEPSEEK_API_KEY"
            )
        has_hf_token = bool(self.hf_token and self.hf_token.get_secret_value().strip())
        if self.model_provider == "huggingface" and not has_hf_token:
            raise ValueError(
                "MODEL_PROVIDER=huggingface 时必须通过环境变量注入 HF_TOKEN"
            )
        has_dashscope_key = bool(
            self.dashscope_api_key and self.dashscope_api_key.get_secret_value().strip()
        )
        if self.model_provider == "dashscope" and not has_dashscope_key:
            raise ValueError(
                "MODEL_PROVIDER=dashscope 时必须通过环境变量注入 DASHSCOPE_API_KEY"
            )
        if self.embedding_enabled and not (
            self.effective_embedding_api_key
            and self.effective_embedding_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "EMBEDDING_PROVIDER=openai_compatible 时必须注入 EMBEDDING_API_KEY"
            )
        jwt_secret = self.jwt_secret_key.get_secret_value().strip()
        if self.app_env == "production" and not jwt_secret:
            raise ValueError("生产环境必须设置安全的 JWT_SECRET_KEY")
        if not self.postgres_password.get_secret_value().strip():
            raise ValueError("必须通过环境变量设置 POSTGRES_PASSWORD")


@lru_cache
def get_settings() -> Settings:
    return Settings()
