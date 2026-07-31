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
        UniqueConstraint(
            "rule_domain",
            "rule_set_code",
            "version_no",
            name="domain_code_version",
        ),
        CheckConstraint(
            "rule_domain IN "
            "('SCHEDULING', 'SELECTION', 'ADJUSTMENT', 'APPROVAL')",
            name="domain_allowed",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="status_allowed",
        ),
        Index(
            "uq_rule_set_published",
            "rule_domain",
            "rule_set_code",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )

    rule_domain: Mapped[str] = mapped_column(String(20), nullable=False)
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
            "enforcement_type IN ('BLOCK', 'SCORE', 'WARN', 'ROUTE')",
            name="enforcement_type_allowed",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        CheckConstraint("weight >= 0", name="weight_nonnegative"),
        CheckConstraint(
            "enforcement_type = 'SCORE' OR weight = 0",
            name="non_score_weight_zero",
        ),
        Index(
            "ix_rule_config_enforcement_enabled",
            "enforcement_type",
            "enabled",
        ),
    )

    rule_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_set.id", ondelete="CASCADE"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(150), nullable=False)
    enforcement_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    scope_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    condition_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    action_config: Mapped[dict] = mapped_column(nullable=False, default=dict)
    weight: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal(0)
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
