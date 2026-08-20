"""重放实验：验证 planner 对"推荐选课方案+偏好"的解析是否保留全部偏好字段。"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.model_provider import get_chat_model
from app.schemas.student_consultation import StudentAgentPlan

PLANNER_PROMPT = (
    Path(__file__).resolve().parents[1]
    / "app" / "agents" / "prompts" / "student_advisor" / "planner_v2.md"
).read_text(encoding="utf-8")

QUESTION = (
    sys.argv[1]
    if len(sys.argv) > 1
    else (
        "帮我推荐选课方案，第7周以后，尽量不选择周末和晚上，"
        "喜欢上李强老师和王芳老师的课，多安排些电学实验课程"
    )
)

BASE_CONTEXT = {
    "profile": {"name": "测试学生", "student_no": "D2024010001"},
    "student_status": {
        "academic_status": "ACTIVE",
        "study_period": {"study_year": 2, "semester_no": 1},
        "selection_count": {"selected": 1, "required_remaining": 5, "optional_remaining": 1},
    },
    "term": {
        "academic_year": "2026-2027",
        "semester_no": 1,
        "current_week": 6,
        "total_weeks": 18,
    },
    "selection_window": {
        "start_at": "2026-08-10T02:01:22+00:00",
        "end_at": "2026-09-11T02:06:22+00:00",
        "withdraw_end_at": "2026-09-23T02:06:22+00:00",
        "status": "OPEN",
    },
    "training_plan_summary": {
        "plan_code": "PLAN-2026",
        "courses": [
            {"course_name": "工程物理实验", "course_nature": "必修"},
            {"course_name": "大学物理实验", "course_nature": "选修"},
        ],
    },
    "current_selections": [
        {
            "project_id": "p1",
            "project_name": "交流电桥",
            "course_name": "工程物理实验",
            "week_no": 6,
            "day_of_week": 3,
            "start_slot": 1,
            "end_slot": 4,
            "teacher_name": "李强",
            "laboratory_name": "电学综合实验室 B102",
            "status": "SELECTED",
        }
    ],
    "page": {"view": "ai"},
}


def build_content() -> str:
    conversation = [
        {"role": "user", "content": QUESTION},
    ]
    return (
        "<output_schema>\n"
        f"{json.dumps(StudentAgentPlan.model_json_schema(), ensure_ascii=False)}\n"
        "</output_schema>\n"
        "<student_base_context>\n"
        f"{json.dumps(BASE_CONTEXT, ensure_ascii=False, default=str)}\n"
        "</student_base_context>\n"
        "<page_context>\n"
        f"{json.dumps({'view': 'ai'}, ensure_ascii=False)}\n"
        "</page_context>\n<conversation>\n"
        f"{json.dumps(conversation, ensure_ascii=False)}\n"
        "</conversation>\n<current_question>\n"
        f"{QUESTION}\n</current_question>"
    )


def extract_plan(content: object) -> StudentAgentPlan:
    text = content
    if isinstance(content, list):
        text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    text = str(text)
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON found in: {text[:500]}")
    return StudentAgentPlan.model_validate_json(text[start : end + 1])


async def main() -> None:
    model = get_chat_model()
    if model is None:
        print("MODEL NOT CONFIGURED")
        return
    messages = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=build_content()),
    ]
    response = await model.ainvoke(messages)
    plan = extract_plan(response.content)
    print("intent:", plan.intent)
    print("request_mode:", plan.request_mode)
    print("operation_stage:", plan.operation_stage)
    print("preferences(原始):", plan.preferences.model_dump(mode="json"))
    print("tool_requests:", [t.model_dump() for t in plan.tool_requests])
    print("scope:", plan.recommendation_scope.model_dump(mode="json"))
    print("needs_clarification:", plan.needs_clarification)
    # 模拟 validate_plan 的合并逻辑
    from app.agents.nodes.student_advisor import _merge_tool_preference_arguments
    merged = _merge_tool_preference_arguments(plan)
    print("preferences(合并后):", merged.preferences.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
