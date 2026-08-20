from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EquipmentUsageRule:
    usage_note: str
    students_per_unit: int | None
    sharing_rule_status: str
    evidence: str | None = None
    source: str = "SEMANTIC_PARSER"


_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value in _CN_DIGITS:
        return _CN_DIGITS[value]
    if value.startswith("十") and len(value) == 2 and value[1] in _CN_DIGITS:
        return 10 + _CN_DIGITS[value[1]]
    if value.endswith("十") and len(value) == 2 and value[0] in _CN_DIGITS:
        return _CN_DIGITS[value[0]] * 10
    if len(value) == 3 and value[1] == "十":
        left = _CN_DIGITS.get(value[0])
        right = _CN_DIGITS.get(value[2])
        if left is not None and right is not None:
            return left * 10 + right
    return None


def interpret_equipment_usage_note(note: str | None) -> EquipmentUsageRule:
    """Convert common Chinese sharing descriptions into a deterministic rule.

    The parsed multiplier is persisted and used by capacity calculations.  Runtime
    capacity code never asks an LLM to reinterpret free text.
    """

    raw = (note or "").strip()
    if not raw:
        return EquipmentUsageRule(
            usage_note="",
            students_per_unit=1,
            sharing_rule_status="CONFIRMED",
            evidence="系统默认：一人一台",
            source="SYSTEM_DEFAULT",
        )

    compact = re.sub(r"[\s，,。；;：:]", "", raw)
    number = r"([0-9]+|[零一二两三四五六七八九十]{1,3})"
    patterns = (
        rf"{number}(?:名)?(?:学生|人)(?:共用|使用)?(?:一|1)(?:台|套)",
        rf"每(?:一|1)?(?:台|套)(?:可供|供|容纳|使用)?{number}(?:名)?(?:学生|人)",
        rf"{number}(?:名)?(?:学生|人)[/／](?:台|套)",
        rf"每{number}(?:名)?(?:学生|人)(?:共用|使用)(?:一|1)(?:台|套)",
        rf"(?:一|1)(?:台|套)(?:设备|仪器)?(?:可供|供){number}(?:名)?(?:学生|人)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            value = _number(match.group(1))
            if value and value >= 1:
                return EquipmentUsageRule(
                    raw,
                    value,
                    "CONFIRMED",
                    match.group(0),
                )

    if any(token in compact for token in ("多人共用", "轮流使用", "全班共用")):
        return EquipmentUsageRule(raw, None, "AMBIGUOUS", raw)
    return EquipmentUsageRule(raw, None, "UNPARSED", raw)
