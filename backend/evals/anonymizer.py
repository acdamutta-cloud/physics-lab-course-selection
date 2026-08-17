from __future__ import annotations

import json
import re
from typing import Any

UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
STUDENT_NO_PATTERN = re.compile(r"\b[A-Z]\d{10,14}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
TOKEN_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*[^\s,}\]]+"
)


def anonymize_text(value: str) -> str:
    value = UUID_PATTERN.sub("<UUID>", value)
    value = STUDENT_NO_PATTERN.sub("<STUDENT_NO>", value)
    value = PHONE_PATTERN.sub("<PHONE>", value)
    return TOKEN_PATTERN.sub(lambda match: f"{match.group(1)}=<REDACTED>", value)


def anonymize_payload(value: Any) -> Any:
    """递归脱敏后返回新对象，不修改业务输入。"""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {
                "student_id",
                "student_no",
                "user_id",
                "api_key",
                "authorization",
                "access_token",
                "refresh_token",
            }:
                result[key] = "<REDACTED>"
            else:
                result[key] = anonymize_payload(item)
        return result
    if isinstance(value, list):
        return [anonymize_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(anonymize_payload(item) for item in value)
    if isinstance(value, str):
        return anonymize_text(value)
    return value


def assert_payload_is_safe(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if UUID_PATTERN.search(serialized) or STUDENT_NO_PATTERN.search(serialized):
        raise ValueError("评测 Trace 脱敏失败，已禁止上传。")
    if TOKEN_PATTERN.search(serialized):
        raise ValueError("评测 Trace 中仍包含疑似密钥，已禁止上传。")
