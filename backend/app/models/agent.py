from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class PromptTemplate(AuditMixin, BaseModel):
    __tablename__ = "prompt_template"
    __table_args__ = (
        UniqueConstraint("agent_code", "version_no", name="agent_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="status_allowed",
        ),
        Index("ix_prompt_template_agent_status", "agent_code", "status"),
    )

    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(nullable=False, default=dict)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class AgentRun(BaseModel):
    __tablename__ = "agent_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'INTERRUPTED', 'SUCCEEDED', "
            "'FAILED', 'CANCELLED')",
            name="status_allowed",
        ),
        Index("ix_agent_run_requester_status", "requester_user_id", "status"),
    )

    thread_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    graph_name: Mapped[str] = mapped_column(String(100), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    business_type: Mapped[str | None] = mapped_column(String(64))
    business_id: Mapped[UUID | None] = mapped_column()
    prompt_versions: Mapped[dict] = mapped_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class AgentStepLog(BaseModel):
    __tablename__ = "agent_step_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', "
            "'INTERRUPTED', 'SKIPPED')",
            name="status_allowed",
        ),
        Index("ix_agent_step_log_run_time", "run_id", "created_at"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
    )
    node_name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_code: Mapped[str | None] = mapped_column(String(64))
    input_summary: Mapped[dict] = mapped_column(nullable=False, default=dict)
    tool_calls: Mapped[dict] = mapped_column(nullable=False, default=dict)
    output_summary: Mapped[dict] = mapped_column(nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict] = mapped_column(nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class AgentFeedback(BaseModel):
    __tablename__ = "agent_feedback"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_valid"),
        Index("ix_agent_feedback_run", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_type: Mapped[str | None] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
