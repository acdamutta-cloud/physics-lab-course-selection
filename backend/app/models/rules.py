from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import AuditMixin, BaseModel


class RuleSet(AuditMixin, BaseModel):
    __tablename__ = "rule_set"
    __table_args__ = (
        UniqueConstraint("rule_set_code", "version_no", name="code_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="status_allowed",
        ),
        Index(
            "uq_rule_set_published",
            "rule_set_code",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    rule_set_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    published_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class RuleConfig(AuditMixin, BaseModel):
    __tablename__ = "rule_config"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "rule_code", name="rule_set_code"),
        CheckConstraint(
            "rule_type IN ('HARD', 'SOFT', 'RUNTIME', 'APPROVAL')",
            name="rule_type_allowed",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("weight >= 0", name="weight_nonnegative"),
        Index("ix_rule_config_type_enabled", "rule_type", "enabled"),
    )

    rule_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_set.id", ondelete="CASCADE"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(150), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    condition_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    action_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    weight: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
