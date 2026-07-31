"""排课智能体：将管理员自然语言偏好转换为受控的运行时配置。"""

from __future__ import annotations

import re
from typing import Any

from app.agents.states.scheduling import SchedulingState
from app.scheduler.objective import (
    build_comparison_weights,
    build_profile_weights,
)

RULE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "STUDENT_AVAILABILITY_COVERAGE": (
        "学生空闲",
        "学生可选",
        "可选时间",
        "空闲人数",
    ),
    "TEACHER_BALANCE": ("教师均衡", "课时均衡", "工作量均衡"),
    "EVENING_PENALTY": ("晚间", "晚上", "夜间"),
    "WEEKEND_PENALTY": ("周末", "周六", "周日"),
    "TEACHER_COMPACTNESS": (
        "课时紧凑",
        "排得紧凑",
        "尽量紧凑",
        "减少分散",
        "不要分散",
    ),
    "TEACHER_CONSECUTIVE_LOAD": (
        "连续实验",
        "连续承担",
        "避免连排",
        "连续课时",
    ),
    "TEACHER_PREFERRED_TIME": ("教师偏好时间", "教师时间偏好", "偏好时间"),
    "LAB_UTILIZATION_BALANCE": (
        "实验室利用",
        "场地均衡",
        "实验室均衡",
    ),
    "TEACHER_TARGET_LOAD_SCORE": (
        "教师少排",
        "少排课",
        "尽量少排",
        "本学期少排",
        "教师减负",
        "减少教师课时",
    ),
    "COURSE_EARLY_WEEK_PREFERENCE": ("前", "前几周", "提前安排"),
    "PROJECT_EARLY_WEEK_PREFERENCE": ("前", "前几周", "提前安排"),
}

CHINESE_DIGITS = {
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
}
WEEK_NUMBER_PATTERN = r"([0-9]+|[零一二两三四五六七八九十]+)"


def _preference_level(text: str, keyword: str) -> str:
    index = text.find(keyword)
    nearby = text[max(0, index - 8) : index + len(keyword) + 8]
    if any(token in nearby for token in ("忽略", "不考虑", "无需考虑")):
        return "IGNORE"
    if any(token in nearby for token in ("强烈", "务必", "最优先", "非常")):
        return "STRONGLY_PREFER"
    return "PREFER"


def _matched_teachers(
    text: str,
    teacher_directory: dict[str, str],
) -> list[str]:
    return [
        teacher_id
        for teacher_id, teacher_name in teacher_directory.items()
        if teacher_name and teacher_name in text
    ]


def _parse_week_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        tens_value = CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = CHINESE_DIGITS.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    return CHINESE_DIGITS.get(value)


def _entity_week_preferences(
    text: str,
    entity_directory: dict[str, str],
    *,
    id_field: str,
    name_field: str,
) -> tuple[list[dict[str, Any]], int | None]:
    matches: list[dict[str, Any]] = []
    generic_match = re.search(
        rf"前\s*{WEEK_NUMBER_PATTERN}\s*周",
        text,
    )
    generic_week = (
        _parse_week_number(generic_match.group(1))
        if generic_match
        else None
    )
    for entity_id, entity_name in entity_directory.items():
        match = re.search(
            rf"{re.escape(entity_name)}.{{0,24}}?"
            rf"前\s*{WEEK_NUMBER_PATTERN}\s*周",
            text,
        )
        if match is None:
            continue
        preferred_end_week = _parse_week_number(match.group(1))
        if preferred_end_week is None:
            continue
        matches.append(
            {
                id_field: entity_id,
                name_field: entity_name,
                "preferred_end_week": preferred_end_week,
            }
        )
    return matches, generic_week


def parse_preferences(
    text: str,
    *,
    teacher_directory: dict[str, str] | None = None,
    course_directory: dict[str, str] | None = None,
    project_directory: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """只输出白名单规则与偏好等级，智能体不能直接输出数字权重。"""

    parsed: list[dict[str, Any]] = []
    teacher_directory = teacher_directory or {}
    course_directory = course_directory or {}
    project_directory = project_directory or {}
    for rule_code, keywords in RULE_KEYWORDS.items():
        if rule_code == "COURSE_EARLY_WEEK_PREFERENCE":
            course_preferences, generic_week = _entity_week_preferences(
                text,
                course_directory,
                id_field="course_id",
                name_field="course_name",
            )
            if generic_week is None or not course_preferences:
                continue
            parsed.append(
                {
                    "rule_code": rule_code,
                    "preference_level": _preference_level(text, "前"),
                    "evidence": f"前{generic_week}周",
                    "course_week_preferences": course_preferences,
                }
            )
            continue
        if rule_code == "PROJECT_EARLY_WEEK_PREFERENCE":
            project_preferences, generic_week = _entity_week_preferences(
                text,
                project_directory,
                id_field="project_id",
                name_field="project_name",
            )
            if generic_week is None or not project_preferences:
                continue
            parsed.append(
                {
                    "rule_code": rule_code,
                    "preference_level": _preference_level(text, "前"),
                    "evidence": f"前{generic_week}周",
                    "project_week_preferences": project_preferences,
                }
            )
            continue
        keyword = next((item for item in keywords if item in text), None)
        if keyword is None:
            continue
        item: dict[str, Any] = {
            "rule_code": rule_code,
            "preference_level": _preference_level(text, keyword),
            "evidence": keyword,
        }
        if rule_code == "TEACHER_TARGET_LOAD_SCORE":
            item["target_teacher_ids"] = _matched_teachers(
                text,
                teacher_directory,
            )
        parsed.append(item)
    return parsed


def scheduling_agent_node(state: SchedulingState) -> dict[str, Any]:
    parsed = parse_preferences(
        state.get("preference_text", ""),
        teacher_directory=state.get("teacher_directory", {}),
        course_directory=state.get("course_directory", {}),
        project_directory=state.get("project_directory", {}),
    )
    warnings: list[str] = []
    applicability = dict(state.get("applicability", {}))

    for item in parsed:
        code = item["rule_code"]
        if (
            code == "TEACHER_TARGET_LOAD_SCORE"
            and not item.get("target_teacher_ids")
        ):
            applicability[code] = False
            warnings.append(
                "识别到教师减负偏好，但未匹配到具体教师，本次不启用该评分。"
            )
        if code == "COURSE_EARLY_WEEK_PREFERENCE":
            valid_course_preferences = [
                preference
                for preference in item.get("course_week_preferences", [])
                if 1
                <= preference["preferred_end_week"]
                <= state.get("total_weeks", 0)
            ]
            item["course_week_preferences"] = valid_course_preferences
            if not valid_course_preferences:
                applicability[code] = False
                warnings.append(
                    "识别到课程前置周偏好，但未匹配到有效课程和教学周，"
                    "本次不启用该评分。"
                )
        if code == "PROJECT_EARLY_WEEK_PREFERENCE":
            valid_project_preferences = [
                preference
                for preference in item.get("project_week_preferences", [])
                if 1
                <= preference["preferred_end_week"]
                <= state.get("total_weeks", 0)
            ]
            item["project_week_preferences"] = valid_project_preferences
            if not valid_project_preferences:
                applicability[code] = False
                warnings.append(
                    "识别到项目前置周偏好，但未匹配到有效项目和教学周，"
                    "本次不启用该评分。"
                )
        if not applicability.get(code, True):
            warnings.append(f"{code} 缺少适用数据，本次运行权重置为 0。")

    levels = {
        item["rule_code"]: item["preference_level"]
        for item in parsed
    }
    comparison = build_comparison_weights(
        state.get("base_weights", {}),
        applicability=applicability,
        preference_levels=levels,
    )

    applicable_preferences = [
        item
        for item in parsed
        if item["rule_code"] in state.get("base_weights", {})
        and applicability.get(item["rule_code"], True)
        and item["preference_level"] != "IGNORE"
    ]
    applicable_preferences.sort(
        key=lambda item: state.get("rule_priorities", {}).get(
            item["rule_code"],
            0,
        ),
        reverse=True,
    )
    max_count = max(2, min(5, state.get("max_candidate_count", 5)))
    profile_rules: list[str | None] = [None]
    profile_rules.extend(
        item["rule_code"]
        for item in applicable_preferences[: max_count - 1]
    )
    if len(profile_rules) < 2:
        alternative_rule = max(
            (
                code
                for code, weight in comparison.items()
                if weight > 0
            ),
            key=lambda code: (
                state.get("rule_priorities", {}).get(code, 0),
                comparison[code],
                code,
            ),
            default=None,
        )
        profile_rules.append(alternative_rule)
    profiles = [
        {
            "profile_code": (
                "BALANCED"
                if profile_index == 0
                else rule_code or "ALTERNATIVE"
            ),
            "focus_rule_code": rule_code,
            "solver_weights": {
                code: float(value)
                for code, value in build_profile_weights(
                    comparison,
                    rule_code,
                ).items()
            },
        }
        for profile_index, rule_code in enumerate(profile_rules)
    ]
    return {
        "parsed_preferences": parsed,
        "comparison_weights": {
            code: float(value) for code, value in comparison.items()
        },
        "profiles": profiles,
        "warnings": warnings,
        "applicability": applicability,
    }
