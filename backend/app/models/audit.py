from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class OperationLog(BaseModel):
    __tablename__ = "operation_log"
    __table_args__ = (
        Index("ix_operation_log_object", "object_type", "object_id"),
        Index("ix_operation_log_operator_time", "operator_user_id", "created_at"),
    )

    operator_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[UUID | None] = mapped_column()
    request_id: Mapped[str | None] = mapped_column(String(64))
    before_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(nullable=False, default=dict)
    rule_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("rule_set.id", ondelete="SET NULL")
    )
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET)
