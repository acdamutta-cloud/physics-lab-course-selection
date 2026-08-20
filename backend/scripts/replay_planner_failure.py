"""重放用户在 AI 助手报"规划输出无法解析:ValidationError"的输入。

用真实 DeepSeek 模型调用 plan_with_llm,验证 repair 分支携带具体字段
错误详情后能否自愈;失败时打印 ValidationError 详情。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.model_provider import get_chat_model
from app.agents.nodes.student_advisor import plan_with_llm

QUESTION = (
    "帮我推荐选课方案，时间不早于第8周，优先选择李强老师和王芳老师的课，"
    "不要晚上和周末的时间，多做一些电学实验"
)


async def main() -> None:
    model = get_chat_model()
    result = await plan_with_llm(
        {
            "model": model,
            "current_question": QUESTION,
            "conversation_context": [],
            "base_context": {},
        }
    )
    if "model_error" in result:
        print("MODEL ERROR:", result["model_error"])
        print(
            "repaired_plan_attempted:",
            result.get("repaired_plan_attempted"),
        )
        return
    plan = result["plan"]
    print("PLAN OK, intent:", plan.intent)
    print("preferences:", json.dumps(plan.preferences.model_dump(), ensure_ascii=False))
    print("tool_requests:", [t.name for t in plan.tool_requests])


if __name__ == "__main__":
    asyncio.run(main())
