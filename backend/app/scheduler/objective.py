"""排课软约束的运行时权重计算。

数据库中的 ``RuleConfig.weight`` 始终作为基础权重读取；管理员偏好和
候选方案 profile 只生成内存中的权重快照，不回写规则表。
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

PREFERENCE_DELTAS: dict[str, Decimal] = {
    "IGNORE": Decimal(-1000),
    "DEFAULT": Decimal(0),
    "PREFER": Decimal(15),
    "STRONGLY_PREFER": Decimal(30),
}
PROFILE_DELTA = Decimal(25)
WEIGHT_QUANTUM = Decimal("0.0001")


def normalize_weights(weights: Mapping[str, Decimal | int | float]) -> dict[str, Decimal]:
    """将非负权重归一化为 100，并保持四位小数之和严格等于 100。"""

    cleaned = {
        code: max(Decimal(0), Decimal(str(value)))
        for code, value in weights.items()
    }
    total = sum(cleaned.values(), Decimal(0))
    if not cleaned:
        return {}
    if total == 0:
        return {code: Decimal(0) for code in cleaned}

    normalized = {
        code: (value * Decimal(100) / total).quantize(
            WEIGHT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        for code, value in cleaned.items()
    }
    difference = Decimal(100) - sum(normalized.values(), Decimal(0))
    if difference:
        largest_code = max(cleaned, key=lambda code: (cleaned[code], code))
        normalized[largest_code] += difference
    return normalized


def build_comparison_weights(
    base_weights: Mapping[str, Decimal | int | float],
    *,
    applicability: Mapping[str, bool],
    preference_levels: Mapping[str, str],
) -> dict[str, Decimal]:
    """生成所有候选方案共用的比较权重。"""

    adjusted: dict[str, Decimal] = {}
    for code, base in base_weights.items():
        if not applicability.get(code, True):
            adjusted[code] = Decimal(0)
            continue
        level = preference_levels.get(code, "DEFAULT")
        if level == "IGNORE":
            adjusted[code] = Decimal(0)
            continue
        delta = PREFERENCE_DELTAS.get(level, Decimal(0))
        adjusted[code] = min(
            Decimal(100),
            max(Decimal(0), Decimal(str(base)) + delta),
        )
    return normalize_weights(adjusted)


def build_profile_weights(
    comparison_weights: Mapping[str, Decimal | int | float],
    profile_rule_code: str | None,
) -> dict[str, Decimal]:
    """在比较权重上构造某一候选 profile 的求解权重。"""

    solver_weights = {
        code: Decimal(str(value))
        for code, value in comparison_weights.items()
    }
    if profile_rule_code and profile_rule_code in solver_weights:
        solver_weights[profile_rule_code] += PROFILE_DELTA
    return normalize_weights(solver_weights)
