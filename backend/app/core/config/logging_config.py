import json
import logging
import logging.config
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config.settings import Settings


class JsonFormatter(logging.Formatter):
    """生产环境可选的单行 JSON 日志格式。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    """配置控制台日志；开发环境可额外输出到本地日志目录。"""

    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if settings.log_json else "standard",
            "stream": "ext://sys.stdout",
        }
    }

    if settings.app_env == "development":
        log_dir = Path(settings.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "standard",
            "filename": str(log_dir / "app.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": (
                        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
                    )
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": handlers,
            "root": {
                "level": settings.log_level,
                "handlers": list(handlers),
            },
            "loggers": {
                "uvicorn.access": {
                    "level": settings.log_level,
                    "handlers": list(handlers),
                    "propagate": False,
                }
            },
        }
    )
