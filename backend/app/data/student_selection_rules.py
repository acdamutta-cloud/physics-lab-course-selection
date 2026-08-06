from __future__ import annotations

from typing import Final, TypedDict

from app.schemas.student_consultation import StudentRuleTopic


class StudentSelectionRuleDefinition(TypedDict):
    rule_code: str
    topic: StudentRuleTopic
    rule_name: str
    description: str
    enforcement_type: str


STUDENT_SELECTION_RULES: Final[tuple[StudentSelectionRuleDefinition, ...]] = (
    {
        "rule_code": "STUDENT_INACTIVE",
        "topic": "ACADEMIC_STATUS",
        "rule_name": "学籍状态有效",
        "description": "只有学籍状态有效的学生可以选择实验场次。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "STUDY_PERIOD_NOT_REACHED",
        "topic": "STUDY_PERIOD",
        "rule_name": "达到培养方案修读学期",
        "description": "未达到培养方案规定的修读学年和学期时不能选择该课程场次。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "PREREQUISITE_COURSE_NOT_PASSED",
        "topic": "PREREQUISITE",
        "rule_name": "先修课程要求",
        "description": "培养方案要求必须完成的先修课程尚未通过时不能选择该课程场次。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "COURSE_ALREADY_PASSED",
        "topic": "COURSE_COMPLETION",
        "rule_name": "已通过课程不得重复修读",
        "description": "实验课程已经通过时不能重复修读；未通过课程允许按规则重修。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "SCHEDULE_NOT_PUBLISHED",
        "topic": "SESSION_AVAILABILITY",
        "rule_name": "课表必须发布",
        "description": "场次所属课表尚未发布时不能选择该场次。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "SESSION_NOT_OPEN",
        "topic": "SESSION_AVAILABILITY",
        "rule_name": "场次必须开放",
        "description": "实验场次未开放选课时不能选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "SESSION_FULL",
        "topic": "SESSION_AVAILABILITY",
        "rule_name": "场次容量限制",
        "description": "实验场次没有剩余名额时不能选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "SESSION_ALREADY_SELECTED",
        "topic": "PROJECT_UNIQUENESS",
        "rule_name": "相同场次幂等提示",
        "description": "重复提交已经选择的同一场次时返回已选择提示。",
        "enforcement_type": "WARN",
    },
    {
        "rule_code": "PROJECT_ALREADY_SELECTED",
        "topic": "PROJECT_UNIQUENESS",
        "rule_name": "同一项目仅选一个场次",
        "description": "同一学期同一实验项目只能保留一个有效场次，退选后可改选其他场次。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "PROJECT_OCCUPIED_BY_APPLICATION",
        "topic": "APPLICATION_OCCUPANCY",
        "rule_name": "处理中申请占用项目",
        "description": "待审核或处理中的有效申请占用项目唯一名额，处理完成前不能重复选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "BASE_SCHEDULE_CONFLICT",
        "topic": "TIME_CONFLICT",
        "rule_name": "学生基础课表时间冲突",
        "description": "实验场次与学生当前学期Busy课表冲突时不能选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "EXPERIMENT_SESSION_CONFLICT",
        "topic": "TIME_CONFLICT",
        "rule_name": "实验安排时间冲突",
        "description": "实验场次与已选或处理中的实验安排冲突时不能选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "PROJECT_ORDER_VIOLATION",
        "topic": "PROJECT_ORDER",
        "rule_name": "项目修读顺序",
        "description": "场次时间违反培养方案中的项目先后约束时不能选择。",
        "enforcement_type": "BLOCK",
    },
    {
        "rule_code": "PROJECT_ORDER_PENDING",
        "topic": "PROJECT_ORDER",
        "rule_name": "前置项目待选择提醒",
        "description": "先选择后置项目时，仍需选择时间更早且符合资格的前置项目场次。",
        "enforcement_type": "WARN",
    },
)


RULES_BY_CODE: Final = {
    item["rule_code"]: item for item in STUDENT_SELECTION_RULES
}


def rules_for_topics(
    topics: list[StudentRuleTopic],
) -> tuple[StudentSelectionRuleDefinition, ...]:
    selected = set(topics) - {"TRAINING_PLAN", "OTHER"}
    return tuple(item for item in STUDENT_SELECTION_RULES if item["topic"] in selected)

