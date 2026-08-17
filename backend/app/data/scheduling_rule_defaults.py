"""排课软约束规则默认配置（权威值，迁移与测试引用）。

由 scripts/seed_demo_data.py 抽取，供 alembic 迁移测试校验规则初始化。
"""

from __future__ import annotations

from decimal import Decimal

RULE_SET_SPECS = {
    "SCHEDULING": {
        "code": "DEMO-PHYSICS-LAB-SCHEDULING",
        "name": "物理实验模拟排课规则集 V1",
        "status": "PUBLISHED",
    },
    "SELECTION": {
        "code": "DEMO-PHYSICS-LAB-SELECTION",
        "name": "物理实验模拟选课规则集 V1",
        "status": "PUBLISHED",
    },
    "ADJUSTMENT": {
        "code": "DEMO-PHYSICS-LAB-ADJUSTMENT",
        "name": "物理实验模拟调整规则集 V1",
        "status": "DRAFT",
    },
    "APPROVAL": {
        "code": "DEMO-PHYSICS-LAB-APPROVAL",
        "name": "物理实验模拟审批规则集 V1",
        "status": "DRAFT",
    },
}

RULE_SPECS = {
    "SCHEDULING": [
        ("TEACHER_TIME_CONFLICT", "教师时间不得冲突", "BLOCK", 100, {}),
        ("LAB_TIME_CONFLICT", "实验室时间不得冲突", "BLOCK", 100, {}),
        ("SESSION_CAPACITY", "场次不得超过容量", "BLOCK", 100, {}),
        ("TEACHER_QUALIFICATION", "教师须具备项目资格", "BLOCK", 100, {}),
        ("LAB_CAPABILITY", "实验室须支持对应项目", "BLOCK", 100, {}),
        ("EQUIPMENT_AVAILABLE", "设备数量须满足要求", "BLOCK", 100, {}),
        ("TEACHER_BALANCE", "教师工作量尽量均衡", "SCORE", 50, {}),
        ("EVENING_PENALTY", "尽量减少晚间场次", "SCORE", 40, {}),
    ],
    "SELECTION": [
        ("STUDENT_TIME_CONFLICT", "学生时间不得冲突", "BLOCK", 100, {}),
        ("PROJECT_DUPLICATE", "项目不得重复修读", "BLOCK", 100, {}),
    ],
    "ADJUSTMENT": [
        ("RESCHEDULE_SAME_PROJECT", "调课须保持实验项目不变", "BLOCK", 100, {}),
        ("RESCHEDULE_TARGET_CAPACITY", "调课目标场次须有余量", "BLOCK", 100, {}),
        ("RESCHEDULE_STUDENT_TIME", "调课不得与学生课表冲突", "BLOCK", 100, {}),
        ("GROUP_CHANGE_CAPACITY", "换组目标组须有容量", "BLOCK", 100, {}),
        (
            "GROUP_CHANGE_FEATURE_ENABLED",
            "未启用场次内分组时不得换组",
            "BLOCK",
            100,
            {},
        ),
        ("MAKEUP_ABSENCE_REQUIRED", "补做须存在缺做记录", "BLOCK", 100, {}),
        ("MAKEUP_WITHIN_DEADLINE", "补做须在规定期限内申请", "BLOCK", 100, {}),
        (
            "REPLACEMENT_TEACHER_QUALIFIED",
            "替换教师须具备项目资格",
            "BLOCK",
            100,
            {},
        ),
        ("RESOURCE_ISSUE_SUSPEND", "资源异常须暂停受影响安排", "WARN", 100, {}),
        ("LAB_UNAVAILABLE_BLOCK", "停用实验室不得继续使用", "BLOCK", 100, {}),
        (
            "MINIMIZE_PUBLISHED_SCHEDULE_CHANGE",
            "调整应尽量减少对已发布课表的影响",
            "SCORE",
            50,
            {},
        ),
    ],
    "APPROVAL": [
        (
            "SINGLE_STUDENT_NO_SESSION_CHANGE_AUTO_APPROVE",
            "单人且不改变正式场次时自动批准",
            "ROUTE",
            100,
            {"route": "AUTO_APPROVE"},
        ),
        (
            "HARD_CONFLICT_AUTO_REJECT",
            "存在阻断级硬冲突时自动驳回",
            "ROUTE",
            100,
            {"route": "AUTO_REJECT"},
        ),
        (
            "OFFICIAL_SESSION_CHANGE_MANUAL_REVIEW",
            "改变正式场次时转管理员审批",
            "ROUTE",
            100,
            {"route": "MANUAL_REVIEW"},
        ),
        (
            "MULTI_STUDENT_IMPACT_MANUAL_REVIEW",
            "影响多名学生时转管理员审批",
            "ROUTE",
            100,
            {"route": "MANUAL_REVIEW"},
        ),
        (
            "INITIAL_SCHEDULE_PUBLISH_ADMIN_CONFIRM",
            "初始课表发布须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
        (
            "RULE_SET_PUBLISH_ADMIN_CONFIRM",
            "规则启用须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
        (
            "SCHEDULE_ROLLBACK_ADMIN_CONFIRM",
            "课表回滚须管理员确认",
            "ROUTE",
            100,
            {"route": "ADMIN_CONFIRM"},
        ),
    ],
}

NEW_SCHEDULING_SOFT_RULES = [
    (
        "TEACHER_COMPACTNESS",
        "减少教师课时过于分散",
        "SCORE",
        0,
        {"metric": "teacher_schedule_compactness"},
    ),
    (
        "TEACHER_CONSECUTIVE_LOAD",
        "避免教师连续承担过多实验",
        "SCORE",
        0,
        {"metric": "teacher_consecutive_load"},
    ),
    (
        "LAB_UTILIZATION_BALANCE",
        "平衡实验室利用率",
        "SCORE",
        0,
        {"metric": "laboratory_utilization_balance"},
    ),
    (
        "STUDENT_AVAILABILITY_COVERAGE",
        "提高学生可选时间覆盖率",
        "SCORE",
        0,
        {"metric": "student_availability_coverage"},
    ),
    (
        "WEEKEND_PENALTY",
        "尽量减少周末实验",
        "SCORE",
        0,
        {"metric": "weekend_session_count"},
    ),
    (
        "TEACHER_PREFERRED_TIME",
        "尽量满足教师偏好时间",
        "SCORE",
        0,
        {"metric": "teacher_preferred_time_match"},
    ),
    (
        "TEACHER_TARGET_LOAD_SCORE",
        "指定教师课时负荷评分",
        "SCORE",
        0,
        {"metric": "target_teacher_assigned_session_count"},
    ),
    (
        "COURSE_EARLY_WEEK_PREFERENCE",
        "指定课程前置周安排评分",
        "SCORE",
        0,
        {"metric": "target_course_late_session_ratio"},
    ),
    (
        "PROJECT_EARLY_WEEK_PREFERENCE",
        "指定项目前置周安排评分",
        "SCORE",
        0,
        {"metric": "target_project_late_session_ratio"},
    ),
]

NEW_SCHEDULING_SOFT_CONDITIONS = {
    "WEEKEND_PENALTY": {"weekend_days": [1, 7]},
    "TEACHER_PREFERRED_TIME": {
        "availability_type": "PREFERRED",
    },
    "TEACHER_TARGET_LOAD_SCORE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
    },
    "COURSE_EARLY_WEEK_PREFERENCE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    },
    "PROJECT_EARLY_WEEK_PREFERENCE": {
        "configuration_status": "RUNTIME",
        "target_source": "SCHEDULE_JOB_INPUT",
        "parameter": "preferred_end_week",
    },
}

SCHEDULING_SOFT_RULE_INITIALS = {
    "STUDENT_AVAILABILITY_COVERAGE": {
        "weight": Decimal(25),
        "priority": 90,
    },
    "TEACHER_BALANCE": {
        "weight": Decimal(15),
        "priority": 80,
    },
    "EVENING_PENALTY": {
        "weight": Decimal(12),
        "priority": 70,
    },
    "WEEKEND_PENALTY": {
        "weight": Decimal(10),
        "priority": 70,
    },
    "TEACHER_COMPACTNESS": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "TEACHER_CONSECUTIVE_LOAD": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "TEACHER_PREFERRED_TIME": {
        "weight": Decimal(10),
        "priority": 60,
    },
    "LAB_UTILIZATION_BALANCE": {
        "weight": Decimal(8),
        "priority": 50,
    },
    "TEACHER_TARGET_LOAD_SCORE": {
        "weight": Decimal(0),
        "priority": 40,
    },
    "COURSE_EARLY_WEEK_PREFERENCE": {
        "weight": Decimal(0),
        "priority": 40,
    },
    "PROJECT_EARLY_WEEK_PREFERENCE": {
        "weight": Decimal(0),
        "priority": 40,
    },
}
