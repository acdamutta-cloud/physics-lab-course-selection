from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Notification(BaseModel):
    __tablename__ = "notification"
    __table_args__ = (
        CheckConstraint(
            "notification_type IN ('SYSTEM', 'SELECTION', 'APPLICATION', "
            "'SCHEDULE', 'RESOURCE')",
            name="notification_type_allowed",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'READ', 'FAILED')",
            name="status_allowed",
        ),
        Index("ix_notification_recipient_status", "recipient_user_id", "status"),
    )

    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    business_type: Mapped[str | None] = mapped_column(String(64))
    business_id: Mapped[UUID | None] = mapped_column()
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
