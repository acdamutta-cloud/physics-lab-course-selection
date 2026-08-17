from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EvalMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class EvalInputs(BaseModel):
    messages: list[EvalMessage] = Field(min_length=1, max_length=20)
    page_context: dict[str, Any] = Field(default_factory=lambda: {"view": "ai"})
    student_fixture_id: str = "student_default"
    database_fixture_id: str | None = None


class EvalReference(BaseModel):
    expected_intent: str
    acceptable_intents: list[str] = Field(default_factory=list)
    expected_request_mode: str | None = None
    expected_operation_stage: str | None = None
    expected_entities: dict[str, Any] = Field(default_factory=dict)
    expected_preferences: dict[str, Any] = Field(default_factory=dict)
    expected_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_tool_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expected_tool_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expected_guide_ids: list[str] = Field(default_factory=list)
    expected_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    expected_answer_points: list[str] = Field(default_factory=list)
    expected_cards: list[str] = Field(default_factory=list)
    should_clarify: bool = False


class EvalMetadata(BaseModel):
    case_id: str
    category: Literal["RAG", "CONTEXT", "TOOL", "ROUTING_SAFETY"]
    subcategory: str
    difficulty: Literal["EASY", "MEDIUM", "HARD"]
    robustness_type: str = "NONE"
    pair_id: str | None = None
    invariance_expected: bool = True
    safety_critical: bool = False
    split: Literal["smoke", "full"] = "full"


class EvalCase(BaseModel):
    inputs: EvalInputs
    reference_outputs: EvalReference
    metadata: EvalMetadata

    @model_validator(mode="after")
    def validate_pairing(self) -> EvalCase:
        if self.metadata.robustness_type != "NONE" and not self.metadata.pair_id:
            raise ValueError("鲁棒性样本必须提供 pair_id")
        tool_names = set(self.reference_outputs.expected_tools)
        result_tools = set(self.reference_outputs.expected_tool_results)
        if not result_tools.issubset(tool_names):
            raise ValueError("工具结果断言必须对应 expected_tools 中的工具")
        if self.reference_outputs.expected_tool_results and self.reference_outputs.should_clarify:
            raise ValueError("要求澄清的样本不能同时断言工具已经执行并返回结果")
        if (
            self.reference_outputs.expected_guide_ids
            and "lookup_operation_guide" not in tool_names
        ):
            raise ValueError("知识库 guide_id 断言必须调用 lookup_operation_guide")
        return self


class TargetOutput(BaseModel):
    answer: str = ""
    intent: str = "UNKNOWN"
    plan: dict[str, Any] | None = None
    resolved_entities: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    tool_requests: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    cards: list[dict[str, Any]] = Field(default_factory=list)
    grounding_passed: bool = False
    repaired_plan_attempted: bool = False
    model_error: str | None = None
    trace_id: str | None = None
    database_fixture_id: str | None = None
    evaluation_context: dict[str, Any] = Field(default_factory=dict)
