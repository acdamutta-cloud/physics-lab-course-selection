"""校验智能体：校验排课智能体输出的边界与可复现性。"""

from __future__ import annotations

from typing import Any

from app.agents.nodes.scheduling_agent import RULE_KEYWORDS
from app.agents.states.scheduling import SchedulingState

SOFT_CONSTRAINT_TEXT: dict[str, tuple[str, str]] = {
    "STUDENT_AVAILABILITY_COVERAGE": (
        "学生可选时间覆盖较好",
        "学生可选时间覆盖仍可提升",
    ),
    "TEACHER_BALANCE": (
        "教师课时分配较均衡",
        "教师之间的课时负荷存在差异",
    ),
    "EVENING_PENALTY": (
        "晚间实验安排较少",
        "晚间实验安排相对较多",
    ),
    "WEEKEND_PENALTY": (
        "周末实验安排较少",
        "周末实验安排相对较多",
    ),
    "TEACHER_COMPACTNESS": (
        "教师课时分布较紧凑",
        "部分教师课时较分散",
    ),
    "TEACHER_CONSECUTIVE_LOAD": (
        "教师连续实验负担较低",
        "部分教师连续实验负担较高",
    ),
    "TEACHER_PREFERRED_TIME": (
        "较好满足教师偏好时间",
        "教师偏好时间满足度仍可提升",
    ),
    "LAB_UTILIZATION_BALANCE": (
        "实验室利用较均衡",
        "实验室利用率存在不均衡",
    ),
    "TEACHER_TARGET_LOAD_SCORE": (
        "较好控制指定教师的课时负荷",
        "指定教师承担的课时仍相对较多",
    ),
    "COURSE_EARLY_WEEK_PREFERENCE": (
        "目标课程较多安排在指定前置周内",
        "目标课程仍有较多场次晚于指定周次",
    ),
    "PROJECT_EARLY_WEEK_PREFERENCE": (
        "目标项目较多安排在指定前置周内",
        "目标项目仍有较多场次晚于指定周次",
    ),
}


def review_candidate_soft_constraints(
    metrics: dict[str, float],
    comparison_weights: dict[str, float],
    peer_metrics: list[dict[str, float]],
) -> dict[str, list[dict[str, Any]]]:
    """基于同批候选的软约束指标，给出可直接展示的优缺点。"""

    applicable = [
        code
        for code, weight in comparison_weights.items()
        if weight > 0 and code in metrics and code in SOFT_CONSTRAINT_TEXT
    ]
    peer_averages = {
        code: (
            sum(peer.get(code, 0.0) for peer in peer_metrics)
            / max(1, len(peer_metrics))
        )
        for code in applicable
    }
    relative_deltas = {
        code: (peer_averages[code] - metrics[code])
        * comparison_weights[code]
        for code in applicable
    }
    advantages_ranked = sorted(
        (code for code in applicable if relative_deltas[code] > 1e-9),
        key=lambda code: (
            -relative_deltas[code],
            metrics[code],
        ),
    )
    tradeoffs_ranked = sorted(
        (code for code in applicable if relative_deltas[code] < -1e-9),
        key=lambda code: (
            relative_deltas[code],
            -metrics[code],
        ),
    )
    advantage_codes = advantages_ranked[:2]
    tradeoff_codes = tradeoffs_ranked[:2]
    return {
        "advantages": [
            {
                "rule_code": code,
                "text": SOFT_CONSTRAINT_TEXT[code][0],
                "penalty": round(metrics[code], 4),
            }
            for code in advantage_codes
        ],
        "tradeoffs": [
            {
                "rule_code": code,
                "text": SOFT_CONSTRAINT_TEXT[code][1],
                "penalty": round(metrics[code], 4),
            }
            for code in tradeoff_codes
        ],
    }


def validation_agent_node(state: SchedulingState) -> dict[str, Any]:
    errors: list[str] = []
    allowed_rules = set(RULE_KEYWORDS)
    comparison = state.get("comparison_weights", {})
    profiles = state.get("profiles", [])

    unknown = set(comparison) - allowed_rules
    if unknown:
        errors.append(f"存在非白名单软约束：{sorted(unknown)}")
    total = round(sum(comparison.values()), 4)
    if comparison and total not in (0.0, 100.0):
        errors.append(f"比较权重合计必须为 100，当前为 {total}")
    if not profiles or profiles[0].get("profile_code") != "BALANCED":
        errors.append("候选方案必须包含 BALANCED 基准方案")
    if len(profiles) < 2:
        errors.append("初始排课至少需要两个候选方案")
    if len(profiles) > max(2, min(5, state.get("max_candidate_count", 5))):
        errors.append("候选方案数量超过允许上限")
    for profile in profiles:
        solver_weights = profile.get("solver_weights", {})
        solver_total = round(sum(solver_weights.values()), 4)
        if solver_weights and solver_total not in (0.0, 100.0):
            errors.append(
                f"{profile.get('profile_code')} 求解权重合计不是 100"
            )
    return {"validation_errors": errors}
